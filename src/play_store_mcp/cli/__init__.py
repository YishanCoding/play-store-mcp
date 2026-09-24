"""gpcli — Google Play Console command line."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
import traceback
from argparse import Namespace
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from play_store_mcp.cli.catalog import BROWSER_CAPABILITIES, PAGEABLE_TOOLS, SPECS, ToolSpec
from play_store_mcp.cli.output import (
    apply_fields,
    apply_limit,
    print_json,
    print_ndjson,
    print_table,
    redact,
)
from play_store_mcp.cli.parser import (
    UsageError,
    build_parser,
    default_package,
    function_params_schema,
    load_json_arg,
    tool_function,
)

# argparse dest / --body keys → tool parameter names
_FLAG_TO_PARAM: dict[str, str] = {
    "package": "package_name",
    "file": "file_path",
}
_PAGE_SIZE = 100
from play_store_mcp.cli.smoke import plan as smoke_plan
from play_store_mcp.cli.smoke import write_report

CRED_ENV = "GOOGLE_PLAY_STORE_CREDENTIALS"
READ_RETRY_STATUSES = {429, 500, 502, 503}
READ_RETRY_ATTEMPTS = 3
_HTTP_STATUS_IN_TEXT = re.compile(
    r"<HttpError\s+(\d{3})\b|\bHttpError\s+(\d{3})\b|\"status\"\s*:\s*(\d{3})\b|\bstatus\s*[:=]\s*(\d{3})\b",
    re.IGNORECASE,
)


def _configure_logging(*, verbose: bool, stream: TextIO) -> None:
    """Send library logs to stderr so stdout stays JSON."""
    import logging

    import structlog

    level = logging.INFO if verbose else logging.CRITICAL
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=stream),
    )


class AuthError(Exception):
    """Missing or invalid credentials (exit 4)."""


class ApiError(Exception):
    """Upstream API error (exit 3)."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _ns_get(ns: Namespace, name: str, default: Any = None) -> Any:
    value = getattr(ns, name, default)
    if value is argparse.SUPPRESS:
        return default
    return value


def _file_meta(path: str) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.is_file():
        raise UsageError(f"--file not found: {path}")
    data = file_path.read_bytes()
    mime, _ = mimetypes.guess_type(path)
    return {
        "name": file_path.name,
        "bytes": len(data),
        "mime": mime,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _param_name(raw: str) -> str:
    key = raw.replace("-", "_")
    return _FLAG_TO_PARAM.get(key, key)


def _explicit_params(ns: Namespace, spec: ToolSpec, argv: list[str]) -> set[str]:
    explicit: set[str] = set()
    for token in argv:
        if token.startswith("--") and token != "--":
            explicit.add(_param_name(token[2:].split("=", 1)[0]))
    if spec.positional:
        value = _ns_get(ns, spec.positional)
        if value is not None:
            explicit.add(spec.positional)
    return explicit


def _to_jsonable(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump()
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    return value


def _error(stream: TextIO, payload: dict[str, Any]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, default=str))
    stream.write("\n")


def _usage_payload(message: str) -> dict[str, Any]:
    return {"error": {"type": "usage", "message": message}}


def _auth_payload(message: str) -> dict[str, Any]:
    return {"error": {"type": "auth", "message": message}}


def _api_payload(exc: BaseException) -> dict[str, Any]:
    status = None
    code = None
    detail = str(exc)
    http = _http_error(exc)
    if http is not None:
        status = int(getattr(http.resp, "status", None) or 0) or None
        code = getattr(http, "reason", None) or None
        try:
            body = json.loads(http.content.decode("utf-8"))
            err = body.get("error", body)
            if isinstance(err, dict):
                code = err.get("status") or err.get("reason") or err.get("code") or code
                detail = err.get("message") or err.get("detail") or detail
        except Exception:
            pass
        if getattr(exc, "status", None) is None and isinstance(exc, ApiError):
            exc.status = status
            exc.code = str(code) if code is not None else None
    if isinstance(exc, ApiError):
        status = exc.status if status is None else status
        code = exc.code if code is None else code
    return {
        "error": {
            "type": "api",
            "status": status,
            "code": code,
            "detail": detail,
        }
    }


def _http_error(exc: BaseException) -> Any | None:
    try:
        from googleapiclient.errors import HttpError
    except Exception:
        HttpError = ()  # type: ignore[assignment, misc]
    current: BaseException | None = exc
    seen = 0
    while current is not None and seen < 5:
        if HttpError and isinstance(current, HttpError):
            return current
        current = current.__cause__ or current.__context__
        seen += 1
    return None


def _require_credentials() -> str:
    value = os.environ.get(CRED_ENV)
    if not value:
        raise AuthError(f"缺 {CRED_ENV}")
    stripped = value.strip()
    if stripped.startswith("{"):
        return value
    path = Path(value).expanduser()
    if not path.exists():
        raise AuthError(f"缺 {CRED_ENV}")
    return value


def _make_client() -> Any:
    from play_store_mcp.client import PlayStoreClient, PlayStoreClientError

    creds = _require_credentials()
    try:
        client = PlayStoreClient(credentials_json=creds)
        return client
    except PlayStoreClientError as exc:
        raise AuthError(str(exc)) from exc


def _tools_catalog() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in SPECS.values():
        fn = tool_function(spec.name)
        doc = (fn.__doc__ or "").strip().split("\n", 1)[0]
        rows.append(
            {
                "command": spec.command,
                "aliases": [spec.alias],
                "mcp_tool": spec.name,
                "kind": spec.kind,
                "risk": spec.risk,
                "params": function_params_schema(fn),
                "description": doc,
            }
        )
    rows.extend(BROWSER_CAPABILITIES)
    return rows


def _format_output(stream: TextIO, value: object, fmt: str) -> None:
    if fmt == "ndjson":
        print_ndjson(stream, value)
    elif fmt == "table":
        print_table(stream, value)
    else:
        print_json(stream, value)


def _merge_kwargs(ns: Namespace, spec: ToolSpec, argv: list[str]) -> dict[str, Any]:
    fn = tool_function(spec.name)
    import inspect

    sig = inspect.signature(fn)
    kwargs: dict[str, Any] = {}
    for name in sig.parameters:
        if name == "package_name":
            pkg = default_package(ns)
            if pkg:
                kwargs["package_name"] = pkg
            continue
        if name == "file_path":
            file_path = _ns_get(ns, "file_path")
            if file_path:
                kwargs["file_path"] = file_path
            continue
        value = _ns_get(ns, name)
        if value is not None:
            kwargs[name] = value

    explicit = _explicit_params(ns, spec, argv)

    def _apply_object(raw: Any, label: str, *, override: bool) -> None:
        parsed = load_json_arg(raw, label)
        if not isinstance(parsed, dict):
            raise UsageError(f"JSON {label} must be an object")
        for key, value in parsed.items():
            param = _param_name(str(key))
            if param in kwargs and param in explicit and kwargs[param] != value:
                flag = next((flag for flag, dest in _FLAG_TO_PARAM.items() if dest == param), str(key).replace("_", "-"))
                raise UsageError(f"--{flag} conflicts with --{label}")
            if override:
                kwargs[param] = value
            else:
                kwargs.setdefault(param, value)

    body_raw = _ns_get(ns, "body")
    if body_raw:
        _apply_object(body_raw, "body", override=True)
    query_raw = _ns_get(ns, "query")
    if query_raw:
        _apply_object(query_raw, "query", override=False)

    unknown = [name for name in kwargs if name not in sig.parameters]
    if unknown:
        raise UsageError("unknown field(s): " + ", ".join(sorted(unknown)))

    for name, param in sig.parameters.items():
        if param.default is inspect.Parameter.empty and name not in kwargs:
            if name == "package_name":
                raise UsageError("--package is required (or set GPCLI_PACKAGE)")
            if name == spec.positional:
                raise UsageError(f"missing positional id {name}")
            if name == "file_path":
                raise UsageError("--file is required")
            raise UsageError(f"missing --{name.replace('_', '-')}")

    limit = _ns_get(ns, "limit")
    if limit is not None and "max_results" in sig.parameters and not _ns_get(ns, "all"):
        kwargs["max_results"] = limit

    return kwargs


def _check_confirm(ns: Namespace, spec: ToolSpec, kwargs: dict[str, Any]) -> None:
    if spec.kind != "write" or spec.risk != "high":
        return
    confirm = _ns_get(ns, "confirm")
    package = kwargs.get("package_name")
    developer_id = kwargs.get("developer_id")
    if not confirm:
        if package:
            raise UsageError("--confirm <package> is required for this command")
        if developer_id:
            raise UsageError("--confirm <developer-id> is required for this command")
        raise UsageError("--confirm is required for this command")
    if package:
        if confirm != package:
            raise UsageError("--confirm does not match --package")
        return
    if developer_id:
        if confirm != developer_id:
            raise UsageError("--confirm does not match --developer-id")
        return
    positional = kwargs.get(spec.positional) if spec.positional else None
    if positional and confirm != positional:
        raise UsageError(f"--confirm does not match {spec.positional}")


def _supports_pagination(spec: ToolSpec) -> bool:
    return spec.kind == "read" and spec.name in PAGEABLE_TOOLS


def _client_call_kwargs(method: Any, kwargs: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    import inspect

    merged = {**kwargs, **extra}
    try:
        sig = inspect.signature(method)
    except (TypeError, ValueError):
        return merged
    if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in sig.parameters.values()):
        return merged
    accepted = set(sig.parameters)
    return {key: value for key, value in merged.items() if key in accepted}


def _paginate_all(
    client: Any,
    spec: ToolSpec,
    kwargs: dict[str, Any],
    limit: int | None,
    verbose: bool,
    stderr: TextIO,
) -> list[Any]:
    method = getattr(client, spec.name)
    items: list[Any] = []
    start = 0
    while True:
        remaining = None if limit is None else max(limit - len(items), 0)
        if remaining == 0:
            break
        fetch = _PAGE_SIZE if remaining is None else min(_PAGE_SIZE, remaining)
        call_kwargs = _client_call_kwargs(
            method, kwargs, {"max_results": fetch, "start_index": start}
        )
        started = time.perf_counter()
        page = method(**call_kwargs)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if verbose:
            stderr.write(f"{spec.http_method} {spec.http_path} 200 {elapsed_ms}ms\n")
        dumped = _to_jsonable(page)
        if not isinstance(dumped, list):
            raise UsageError("--all is not supported for this command")
        items.extend(dumped)
        if len(dumped) < fetch:
            break
        start += len(dumped)
        if start <= 0:
            break
    return items[:limit] if limit is not None else items


def _dry_run(spec: ToolSpec, kwargs: dict[str, Any]) -> dict[str, Any]:
    path = spec.http_path
    try:
        path = spec.http_path.format(**{k: kwargs.get(k, f"{{{k}}}") for k in kwargs})
    except Exception:
        path = spec.http_path
    body = {k: v for k, v in kwargs.items() if k != "file_path"}
    payload: dict[str, Any] = {
        "dry_run": True,
        "method": spec.http_method,
        "path": path,
        "body": redact(body),
    }
    file_path = kwargs.get("file_path")
    if file_path:
        payload["file"] = _file_meta(str(file_path))
    return payload


def _call_tool(spec: ToolSpec, kwargs: dict[str, Any], verbose: bool, stderr: TextIO) -> Any:
    fn = tool_function(spec.name)
    attempts = 0
    backoff = 1.0
    last_exc: BaseException | None = None
    max_attempts = 1 if spec.kind == "write" else READ_RETRY_ATTEMPTS
    while attempts < max_attempts:
        attempts += 1
        started = time.perf_counter()
        try:
            result = fn(**kwargs)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            try:
                _raise_for_failed_result(result)
            except ApiError as failed:
                if verbose:
                    stderr.write(
                        f"{spec.http_method} {spec.http_path} {failed.status or 'err'} {elapsed_ms}ms\n"
                    )
                raise
            if verbose:
                stderr.write(
                    f"{spec.http_method} {spec.http_path} 200 {elapsed_ms}ms\n"
                )
            return result
        except Exception as exc:
            last_exc = exc
            http = _http_error(exc)
            status = int(getattr(http.resp, "status", 0) or 0) if http is not None else None
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if verbose:
                stderr.write(
                    f"{spec.http_method} {spec.http_path} {status or 'err'} {elapsed_ms}ms\n"
                )
            if spec.kind == "write":
                raise
            if http is None or status not in READ_RETRY_STATUSES or attempts >= max_attempts:
                raise
            retry_after = None
            if http is not None:
                retry_after = http.resp.get("retry-after") or http.resp.get("Retry-After")
            try:
                sleep_for = float(retry_after) if retry_after else backoff
            except (TypeError, ValueError):
                sleep_for = backoff
            time.sleep(sleep_for)
            backoff *= 2
    assert last_exc is not None
    raise last_exc


def _classify_exception(exc: BaseException) -> tuple[int, dict[str, Any]]:
    from play_store_mcp.client import PlayStoreClientError

    if isinstance(exc, UsageError):
        return 2, _usage_payload(str(exc))
    if isinstance(exc, AuthError):
        return 4, _auth_payload(str(exc))
    http = _http_error(exc)
    if http is not None:
        return 3, _api_payload(exc)
    if isinstance(exc, PlayStoreClientError):
        message = str(exc).lower()
        if "credential" in message or CRED_ENV.lower() in message or "no valid credentials" in message:
            return 4, _auth_payload(str(exc))
        return 3, _api_payload(ApiError(str(exc)))
    if isinstance(exc, ApiError):
        return 3, _api_payload(exc)
    return 3, _api_payload(exc)


def _status_from_text(text: str) -> int | None:
    match = _HTTP_STATUS_IN_TEXT.search(text)
    if not match:
        return None
    for group in match.groups():
        if group:
            return int(group)
    return None


def _raise_for_failed_result(result: Any) -> None:
    """CLI-only: client methods that swallow HttpError still return success=false."""
    if not isinstance(result, dict) or result.get("success") is not False:
        return
    chunks = [result.get("error"), result.get("message"), result.get("detail")]
    errors = result.get("errors")
    if errors:
        chunks.append(json.dumps(errors, ensure_ascii=False, default=str))
    text = " ".join(str(chunk) for chunk in chunks if chunk)
    status = _status_from_text(text)
    if status is None and isinstance(errors, list):
        for item in errors:
            if isinstance(item, dict):
                raw_status = item.get("status")
                if isinstance(raw_status, int):
                    status = raw_status
                    break
                status = _status_from_text(str(item))
                if status is not None:
                    break
    raise ApiError(text or "API request failed", status=status)


def _auth_check(client: Any) -> dict[str, Any]:
    client._get_service()
    return {"ok": True}


def main(
    argv: list[str] | None = None,
    *,
    client: Any | None = None,
    client_factory: Callable[[], Any] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """gpcli entry. Returns an exit code; the console script wraps sys.exit."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args_list = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    old_out, old_err = sys.stdout, sys.stderr
    try:
        sys.stdout = out
        sys.stderr = err
        ns = parser.parse_args(args_list)
    except UsageError as exc:
        _error(err, _usage_payload(str(exc)))
        return 2
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1
    finally:
        sys.stdout, sys.stderr = old_out, old_err

    from play_store_mcp import tools as tool_mod

    _configure_logging(verbose=bool(getattr(ns, "verbose", False)), stream=err)

    factory = client_factory
    if client is not None:
        factory = lambda: client  # noqa: E731
    if factory is None:
        factory = _make_client

    def bound_client() -> Any:
        try:
            return factory()
        except AuthError:
            raise
        except Exception as exc:
            message = str(exc).lower()
            if "credential" in message or CRED_ENV.lower() in message:
                raise AuthError(str(exc)) from exc
            raise

    tool_mod.configure_client(bound_client)

    try:
        handler = getattr(ns, "handler", None)
        if handler == "tools" or ns.cli_command == "tools":
            print_json(out, _tools_catalog())
            return 0
        if handler == "auth_check" or (ns.cli_command == "auth" and getattr(ns, "verb", None) == "check"):
            _require_credentials()
            result = _auth_check(bound_client())
            print_json(out, result)
            return 0
        if handler == "smoke" or ns.cli_command == "smoke":
            _require_credentials()

            def run_tool(name: str, kwargs: dict[str, Any]) -> Any:
                spec = SPECS[name]
                return _call_tool(spec, kwargs, bool(getattr(ns, "verbose", False)), err)

            report = smoke_plan(run_tool)
            output = Path(ns.output)
            return write_report(output, report, out)

        mcp_tool = getattr(ns, "mcp_tool", None)
        if not mcp_tool:
            raise UsageError("missing command")
        spec = SPECS[mcp_tool]
        kwargs = _merge_kwargs(ns, spec, args_list)
        _check_confirm(ns, spec, kwargs)
        file_path = kwargs.get("file_path")
        if file_path and not Path(str(file_path)).is_file():
            raise UsageError(f"--file not found: {file_path}")

        if spec.kind == "write" and not _ns_get(ns, "yes"):
            payload = _dry_run(spec, kwargs)
            print_json(out, payload)
            return 0

        verbose = bool(_ns_get(ns, "verbose"))
        want_all = bool(_ns_get(ns, "all"))
        if want_all:
            if not _supports_pagination(spec):
                raise UsageError("--all is not supported for this command")
            result = _paginate_all(
                bound_client(), spec, kwargs, _ns_get(ns, "limit"), verbose, err
            )
        else:
            result = _call_tool(spec, kwargs, verbose, err)
        fields_raw = _ns_get(ns, "fields")
        fields = [part.strip() for part in fields_raw.split(",") if part.strip()] if fields_raw else []
        result = apply_fields(result, fields)
        if not want_all:
            result = apply_limit(result, _ns_get(ns, "limit"))
        _format_output(out, result, _ns_get(ns, "format", "json") or "json")
        return 0
    except (UsageError, AuthError, ApiError) as exc:
        code, payload = _classify_exception(exc)
        _error(err, payload)
        return code
    except TypeError as exc:
        message = str(exc)
        if "unexpected keyword" in message or "required positional argument" in message:
            _error(err, _usage_payload(message))
            return 2
        code, payload = _classify_exception(exc)
        _error(err, payload)
        return code
    except Exception as exc:
        code, payload = _classify_exception(exc)
        if os.environ.get("GPCLI_DEBUG"):
            traceback.print_exc(file=err)
        _error(err, payload)
        return code
