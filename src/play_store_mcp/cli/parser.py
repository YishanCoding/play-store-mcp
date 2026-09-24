"""Generate argparse subcommands from tool signatures via inspect."""

from __future__ import annotations

import argparse
import inspect
import json
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Union, get_args, get_origin

from play_store_mcp import tools as tool_mod
from play_store_mcp.cli.catalog import SPECS, ToolSpec


class UsageError(Exception):
    """CLI usage / flag error (exit 2)."""


class CliParser(argparse.ArgumentParser):
    """ArgumentParser that raises UsageError instead of exiting.

    Abbreviation is disabled at every layer so ``--pack`` cannot stand in
    for ``--package`` and skip dest-normalized conflict checks.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message)


def _parse_limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid --limit: {value}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("--limit must be >= 1")
    return parsed


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Union and type(None) in args:
        rest = tuple(a for a in args if a is not type(None))
        if len(rest) == 1:
            return rest[0], True
        return annotation, True
    return annotation, False


def _is_list(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return origin in {list, Sequence}


def _is_dict(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return origin in {dict, Mapping}


def _parse_bool(value: str) -> bool:
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean: {value}")


def _parse_json_or_csv(value: str) -> Any:
    if value.startswith(("[", "{")):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise argparse.ArgumentTypeError(f"invalid JSON: {exc}") from exc
    if "," in value:
        return [part.strip() for part in value.split(",") if part.strip()]
    return [value]


def annotation_schema(annotation: Any) -> dict[str, Any]:
    """JSON Schema fragment for a function annotation."""
    annotation, optional = _unwrap_optional(annotation)
    args = get_args(annotation)
    schema: dict[str, Any]
    if annotation is inspect.Parameter.empty or annotation is Any:
        schema = {}
    elif annotation is str:
        schema = {"type": "string"}
    elif annotation is int:
        schema = {"type": "integer"}
    elif annotation is float:
        schema = {"type": "number"}
    elif annotation is bool:
        schema = {"type": "boolean"}
    elif _is_list(annotation):
        item = annotation_schema(args[0]) if args else {}
        schema = {"type": "array", "items": item}
    elif _is_dict(annotation):
        schema = {"type": "object"}
    else:
        schema = {"type": "string"}
    if optional:
        schema["nullable"] = True
    return schema


def function_params_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    sig = inspect.signature(fn)
    hints = _hints(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        schema = annotation_schema(hints.get(name, param.annotation))
        if name == "package_name":
            properties["package"] = schema
            key = "package"
        elif name == "file_path":
            properties["file"] = schema
            key = "file"
        else:
            properties[name.replace("_", "-")] = schema
            key = name.replace("_", "-")
        if param.default is inspect.Parameter.empty:
            required.append(key)
    out: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        out["required"] = required
    return out


def _hints(fn: Callable[..., Any]) -> dict[str, Any]:
    try:
        return dict(inspect.get_annotations(fn, eval_str=True))
    except Exception:
        return {}


def tool_function(name: str) -> Callable[..., Any]:
    fn = getattr(tool_mod, name, None)
    if not callable(fn):
        raise TypeError(f"missing tool function {name}")
    return fn


def _add_global_flags(parser: argparse.ArgumentParser, *, with_defaults: bool) -> None:
    """Shared flags. Defaults live only on the top-level parser.

    Subparsers must use default=SUPPRESS so a flag given *before* the
    subcommand is not overwritten by the child parser's default.
    """
    default: dict[str, Any] = {} if with_defaults else {"default": argparse.SUPPRESS}
    format_default = {"default": "json"} if with_defaults else {"default": argparse.SUPPRESS}
    parser.add_argument("--format", choices=["json", "table", "ndjson"], **format_default)
    parser.add_argument("--fields", help="comma-separated field paths", **default)
    parser.add_argument("--limit", type=_parse_limit, **default)
    parser.add_argument("--all", action="store_true", **default)
    parser.add_argument("--yes", action="store_true", help="execute write commands", **default)
    parser.add_argument("--verbose", action="store_true", **default)
    parser.add_argument("--package", help="app package name (or GPCLI_PACKAGE)", **default)
    parser.add_argument("--body", help="JSON object or @file.json", **default)
    parser.add_argument("--query", help="JSON object or @file.json", **default)
    parser.add_argument("--file", dest="file_path", help="local file path", **default)
    parser.add_argument(
        "--confirm",
        help="confirmation token: package name, or --developer-id when the command has no package",
        **default,
    )
    parser.add_argument("--profile", help="reserved; not implemented", **default)


def global_parent() -> argparse.ArgumentParser:
    parent = CliParser(add_help=False)
    _add_global_flags(parent, with_defaults=True)
    return parent


def subcommand_parent() -> argparse.ArgumentParser:
    """Parent for resource/verb/alias parsers: same flags, no defaults."""
    parent = CliParser(add_help=False)
    _add_global_flags(parent, with_defaults=False)
    return parent


def _add_tool_flags(parser: argparse.ArgumentParser, spec: ToolSpec) -> None:
    fn = tool_function(spec.name)
    sig = inspect.signature(fn)
    hints = _hints(fn)
    parser.set_defaults(mcp_tool=spec.name, resource=spec.resource, verb=spec.verb)
    for name, param in sig.parameters.items():
        if name in {"package_name", "file_path"}:
            continue
        annotation, _ = _unwrap_optional(hints.get(name, param.annotation))
        kwargs: dict[str, Any] = {}
        if spec.positional == name:
            is_required = param.default is inspect.Parameter.empty
            nargs = None if is_required else "?"
            parser.add_argument(
                name,
                nargs=nargs,
                default=argparse.SUPPRESS,
                help=f"{name} (positional id)",
            )
            continue
        flag = "--" + name.replace("_", "-")
        if annotation is bool:
            kwargs["type"] = _parse_bool
        elif annotation is int:
            kwargs["type"] = int
        elif annotation is float:
            kwargs["type"] = float
        elif _is_list(annotation) or _is_dict(annotation):
            kwargs["type"] = _parse_json_or_csv
        kwargs["default"] = argparse.SUPPRESS
        parser.add_argument(flag, dest=name, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    """Build the gpcli parser from inspect.signature of each tool function."""
    parent = global_parent()
    child = subcommand_parent()
    parser = CliParser(
        prog="gpcli",
        description="Google Play Console CLI",
        parents=[parent],
    )
    sub = parser.add_subparsers(dest="cli_command", parser_class=CliParser)

    tools_p = sub.add_parser("tools", parents=[child], help="list commands as JSON")
    tools_p.add_argument("--json", action="store_true", default=True)
    tools_p.set_defaults(mcp_tool=None, handler="tools")

    auth_p = sub.add_parser("auth", parents=[child], help="credential helpers")
    auth_sub = auth_p.add_subparsers(dest="verb", parser_class=CliParser)
    check_p = auth_sub.add_parser("check", parents=[child], help="validate credentials")
    check_p.set_defaults(mcp_tool=None, handler="auth_check", resource="auth", verb="check")

    smoke_p = sub.add_parser("smoke", parents=[child], help="read-only live acceptance")
    smoke_p.add_argument("--output", required=True, help="JSON report path")
    smoke_p.set_defaults(mcp_tool=None, handler="smoke")

    grouped: dict[str, argparse._SubParsersAction[argparse.ArgumentParser]] = {}
    for spec in SPECS.values():
        if spec.resource not in grouped:
            resource_parser = sub.add_parser(spec.resource, parents=[child], help=spec.resource)
            grouped[spec.resource] = resource_parser.add_subparsers(
                dest="verb", parser_class=CliParser
            )
        verb_parser = grouped[spec.resource].add_parser(
            spec.verb,
            parents=[child],
            help=tool_function(spec.name).__doc__.split("\n", 1)[0] if tool_function(spec.name).__doc__ else spec.name,
        )
        _add_tool_flags(verb_parser, spec)

        alias_parser = sub.add_parser(
            spec.alias,
            parents=[child],
            help=f"alias of {spec.command}",
        )
        _add_tool_flags(alias_parser, spec)

    return parser


def load_json_arg(value: str, label: str) -> Any:
    """Load inline JSON or @file.json."""
    try:
        if value.startswith("@"):
            from pathlib import Path

            text = Path(value[1:]).expanduser().read_text(encoding="utf-8")
            parsed: Any = json.loads(text)
        else:
            parsed = json.loads(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise UsageError(f"Unable to load JSON {label}: {exc}") from exc
    return parsed


def default_package(ns: argparse.Namespace) -> str | None:
    pkg = getattr(ns, "package", None)
    if pkg is None or pkg is argparse.SUPPRESS:
        pkg = os.environ.get("GPCLI_PACKAGE")
    return pkg or None
