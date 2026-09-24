"""gpcli — Google Play Console command line."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import sys
import time
import traceback
from argparse import Namespace
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from play_store_mcp.cli.catalog import BROWSER_CAPABILITIES, SPECS, ToolSpec
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
from play_store_mcp.cli.smoke import plan as smoke_plan
from play_store_mcp.cli.smoke import write_report

CRED_ENV = "GOOGLE_PLAY_STORE_CREDENTIALS"
READ_RETRY_STATUSES = {429, 500, 502, 503}
READ_RETRY_ATTEMPTS = 3


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


def _file_meta(path: str) -> dict[str, Any]:
    file_path = Path(path)
    data = file_path.read_bytes()
    mime, _ = mimetypes.guess_type(path)
    return {
        "name": file_path.name,
        "bytes": len(data),
        "mime": mime,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


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
            file_path = getattr(ns, "file_path", None)
            if file_path:
                kwargs["file_path"] = file_path
            continue
        if hasattr(ns, name):
            value = getattr(ns, name)
            if value is not None:
                kwargs[name] = value

    explicit = set()
    for token in argv:
        if token.startswith("--") and token != "--":
            explicit.add(token[2:].split("=", 1)[0].replace("-", "_"))

    body_raw = getattr(ns, "body", None)
    if body_raw:
        body = load_json_arg(body_raw, "body")
        if not isinstance(body, dict):
            raise UsageError("JSON body must be an object")
        for key, value in body.items():
            param = key.replace("-", "_")
            if param in kwargs and (param in explicit or key in explicit):
                flag_val = kwargs[param]
                if flag_val != value:
                    raise UsageError(f"--{key} conflicts with --body")
            kwargs[param] = value

    query_raw = getattr(ns, "query", None)
    if query_raw:
        query = load_json_arg(query_raw, "query")
        if not isinstance(query, dict):
            raise UsageError("JSON query must be an object")
        for key, value in query.items():
            param = key.replace("-", "_")
            if param in kwargs and param in explicit and kwargs[param] != value:
                raise UsageError(f"--{key} conflicts with --query")
            kwargs.setdefault(param, value)

    # Fill required params that are still missing
    for name, param in sig.parameters.items():
        if param.default is inspect.Parameter.empty and name not in kwargs:
            if name == "package_name":
                raise UsageError("--package is required (or set GPCLI_PACKAGE)")
            if name == spec.positional:
                raise UsageError(f"missing positional id {name}")
            if name == "file_path":
                raise UsageError("--file is required")
            raise UsageError(f"missing --{name.replace('_', '-')}")

    if getattr(ns, "limit", None) is not None and "max_results" in sig.parameters:
        kwargs["max_results"] = ns.limit

    return kwargs


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

        if spec.kind == "write":
            if spec.risk == "high":
                confirm = getattr(ns, "confirm", None)
                package = kwargs.get("package_name")
                if not confirm:
                    raise UsageError("--confirm <package> is required for this command")
                if package and confirm != package:
                    raise UsageError("--confirm does not match --package")
                if not package and confirm != kwargs.get(spec.positional):
                    # still require an explicit confirm value; already present
                    pass
            if not getattr(ns, "yes", False):
                payload = _dry_run(spec, kwargs)
                print_json(out, payload)
                return 0

        result = _call_tool(spec, kwargs, bool(getattr(ns, "verbose", False)), err)
        fields_raw = getattr(ns, "fields", None)
        fields = [part.strip() for part in fields_raw.split(",") if part.strip()] if fields_raw else []
        result = apply_fields(result, fields)
        result = apply_limit(result, getattr(ns, "limit", None))
        _format_output(out, redact(result) if False else result, getattr(ns, "format", "json") or "json")
        return 0
    except (UsageError, AuthError, ApiError) as exc:
        code, payload = _classify_exception(exc)
        _error(err, payload)
        return code
    except Exception as exc:
        code, payload = _classify_exception(exc)
        if os.environ.get("GPCLI_DEBUG"):
            traceback.print_exc(file=err)
        _error(err, payload)
        return code
