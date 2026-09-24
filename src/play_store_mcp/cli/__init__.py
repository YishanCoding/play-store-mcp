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


def _duplicate_flag_values(argv: list[str], flag: str) -> list[str]:
    """Distinct values passed for `flag` (e.g. `--package`) across argv.

    `flag` can legally appear both before and after the subcommand (global
    vs. subcommand parser), and argparse silently lets the later occurrence
    win. Returns the distinct values seen, in first-seen order, so the
    caller can reject >1 distinct value instead of silently picking the
    last one.
    """
    values: list[str] = []
    seen: set[str] = set()
    i = 0
    prefix = flag + "="
    while i < len(argv):
        token = argv[i]
        if token == flag and i + 1 < len(argv):
            value = argv[i + 1]
            i += 2
        elif token.startswith(prefix):
            value = token[len(prefix) :]
            i += 1
        else:
            i += 1
            continue
        if value not in seen:
            seen.add(value)
            values.append(value)
    return values


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
    from play_store_mcp.cli.client import CliPlayStoreClient
    from play_store_mcp.client import PlayStoreClientError

    creds = _require_credentials()
    try:
        client = CliPlayStoreClient(credentials_json=creds)
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
    # --confirm always arrives as a str (argparse); body/query values such as
    # developer_id may come in as JSON numbers. Compare as strings so
    # `--confirm 1` matches a body developer_id of 1 (int).
    confirm_str = str(confirm)
    if package:
        if confirm_str != str(package):
            raise UsageError("--confirm does not match --package")
        return
    if developer_id:
        if confirm_str != str(developer_id):
            raise UsageError("--confirm does not match --developer-id")
        return
    positional = kwargs.get(spec.positional) if spec.positional else None
    if positional and confirm_str != str(positional):
        raise UsageError(f"--confirm does not match {spec.positional}")


def _supports_pagination(spec: ToolSpec) -> bool:
    return spec.kind == "read" and spec.name in PAGEABLE_TOOLS


_MAX_ALL_PAGES = 100


def _paginate_all(
    client: Any,
    spec: ToolSpec,
    kwargs: dict[str, Any],
    limit: int | None,
    verbose: bool,
    stderr: TextIO,
) -> list[Any]:
    """Fetch every page for a `--all` command.

    Only `get_reviews` is in PAGEABLE_TOOLS today. It pages through the raw
    Play Developer API `reviews().list()` response using the server's own
    `tokenPagination.nextPageToken` (never a `startIndex` offset, which the
    server is free to ignore — R2-F02), and only applies the
    "drop reviews without userComment" filter after paging, on each page's
    raw items, so a filtered-out review on a page can never look like an
    empty/short page and end the loop early (R2-F03). Duplicate review ids
    across pages are skipped, and pagination stops with an error after
    `_MAX_ALL_PAGES` pages so a misbehaving server can't hang the CLI.
    """
    from play_store_mcp.cli.client import review_from_raw

    if spec.name != "get_reviews":
        raise UsageError("--all is not supported for this command")

    page_kwargs = {
        key: value
        for key, value in kwargs.items()
        if key in {"package_name", "translation_language"}
    }
    items: list[Any] = []
    seen_ids: set[str] = set()
    token: str | None = None
    pages = 0
    while True:
        pages += 1
        if pages > _MAX_ALL_PAGES:
            raise ApiError(
                f"--all exceeded {_MAX_ALL_PAGES} pages of reviews without reaching "
                "the end of results; aborting"
            )
        started = time.perf_counter()
        page = client.list_reviews_page(
            page_token=token, max_results=_PAGE_SIZE, **page_kwargs
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if verbose:
            stderr.write(f"{spec.http_method} {spec.http_path} 200 {elapsed_ms}ms\n")
        if not isinstance(page, dict):
            raise UsageError("--all is not supported for this command")
        raw_reviews = page.get("reviews") or []
        for review_data in raw_reviews:
            review_id = review_data.get("reviewId") if isinstance(review_data, dict) else None
            if review_id:
                if review_id in seen_ids:
                    continue
                seen_ids.add(review_id)
            review = review_from_raw(review_data) if isinstance(review_data, dict) else None
            if review is None:
                continue
            items.append(_to_jsonable(review))
            if limit is not None and len(items) >= limit:
                return items
        token = (page.get("tokenPagination") or {}).get("nextPageToken")
        if not token:
            break
    return items


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

    dup = _duplicate_flag_values(args_list, "--package")
    if len(dup) > 1:
        _error(err, _usage_payload(f"--package given multiple times with different values: {dup}"))
        return 2

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
        want_all = bool(_ns_get(ns, "all"))
        if want_all and spec.kind == "write":
            raise UsageError("--all is not supported for write commands")
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
