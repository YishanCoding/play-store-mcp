"""Stdout formatting, --fields projection, and credential redaction."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

SENSITIVE = {
    "accesstoken",
    "authorization",
    "clientsecret",
    "credential",
    "credentials",
    "privatekey",
    "privatekeypath",
    "refreshtoken",
    "token",
    "purchasetoken",
}

_CAMEL = re.compile(r"^[a-z]+(?:[A-Z][a-z0-9]*)*$")


def _snake(name: str) -> str:
    if "_" in name:
        return name
    out: list[str] = []
    for char in name:
        if char.isupper() and out:
            out.append("_")
        out.append(char.lower())
    return "".join(out)


def redact(value: object) -> object:
    """Replace credential-like keys with a placeholder."""
    if isinstance(value, Mapping):
        output: dict[str, object] = {}
        for key, item in value.items():
            normalized = "".join(c for c in str(key).lower() if c.isalnum())
            output[str(key)] = "***REDACTED***" if normalized in SENSITIVE else redact(item)
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(item) for item in value]
    return value


def _get_path(obj: Any, path: str) -> Any:
    variants = [path, _snake(path)]
    if path == "comments":
        variants.extend(["comment", "comments"])
    if path == "reviewId":
        variants.extend(["review_id", "reviewId"])
    if isinstance(obj, Mapping):
        for key in variants:
            if key in obj:
                cur: Any = obj[key]
                return cur
        # dotted path
        if "." in path:
            head, rest = path.split(".", 1)
            nested = _get_path(obj, head)
            return _get_path(nested, rest)
    return None


def apply_fields(data: Any, fields: list[str]) -> Any:
    """Keep only the requested field paths. Output keys use the requested names."""
    if not fields:
        return data
    if isinstance(data, list):
        return [apply_fields(item, fields) for item in data]
    if not isinstance(data, Mapping):
        return data
    out: dict[str, Any] = {}
    for field in fields:
        out[field] = _get_path(data, field)
    return out


def apply_limit(data: Any, limit: int | None) -> Any:
    if limit is None or not isinstance(data, list):
        return data
    return data[:limit]


def print_json(stream: TextIO, value: object) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    stream.write("\n")


def print_ndjson(stream: TextIO, value: object) -> None:
    rows = value if isinstance(value, list) else [value]
    for row in rows:
        stream.write(json.dumps(row, ensure_ascii=False, default=str))
        stream.write("\n")


def print_table(stream: TextIO, value: object) -> None:
    rows: list[dict[str, Any]]
    if isinstance(value, list) and value and isinstance(value[0], Mapping):
        rows = [dict(r) for r in value]
    elif isinstance(value, Mapping):
        rows = [dict(value)]
    else:
        print_json(stream, value)
        return
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    widths = {key: len(key) for key in keys}
    str_rows: list[dict[str, str]] = []
    for row in rows:
        rendered: dict[str, str] = {}
        for key in keys:
            cell = row.get(key, "")
            text = cell if isinstance(cell, str) else json.dumps(cell, ensure_ascii=False, default=str)
            rendered[key] = text
            widths[key] = max(widths[key], len(text))
        str_rows.append(rendered)
    header = "  ".join(key.ljust(widths[key]) for key in keys)
    stream.write(header + "\n")
    stream.write("  ".join("-" * widths[key] for key in keys) + "\n")
    for row in str_rows:
        stream.write("  ".join(row[key].ljust(widths[key]) for key in keys) + "\n")
