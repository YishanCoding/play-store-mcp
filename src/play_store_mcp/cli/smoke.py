"""Read-only live smoke checks for gpcli."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from play_store_mcp.cli.catalog import SPECS

SmokeFn = Callable[[], Any]


def _package(default: str = "com.vast.jujubit") -> str:
    import os

    return os.environ.get("GPCLI_PACKAGE") or default


def plan(run_tool: Callable[[str, dict[str, Any]], Any]) -> list[dict[str, Any]]:
    """Fixed list of read-only checks. `run_tool` invokes an MCP tool by name."""
    package = _package()
    steps: list[tuple[str, str, dict[str, Any]]] = [
        ("package-name validate", "validate_package_name", {"package_name": package}),
        ("review list", "get_reviews", {"package_name": package, "max_results": 1}),
        ("listing get", "get_listing", {"package_name": package, "language": "en-US"}),
        ("release list", "get_releases", {"package_name": package}),
        ("app get", "get_app_details", {"package_name": package}),
    ]
    report: list[dict[str, Any]] = []
    for title, name, kwargs in steps:
        spec = SPECS[name]
        item: dict[str, Any] = {
            "name": title,
            "mcp_tool": name,
            "command": spec.command,
            "kind": spec.kind,
            "read_only": spec.kind == "read",
            "passed": False,
        }
        if spec.kind != "read":
            item["error"] = "smoke refused to run a write tool"
            report.append(item)
            continue
        try:
            run_tool(name, kwargs)
            item["passed"] = True
        except Exception as exc:
            item["error"] = str(exc)
            item["passed"] = False
        report.append(item)
    return report


def write_report(path: Path, report: list[dict[str, Any]], stream: TextIO) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "items": report,
        "passed": all(item.get("passed") and item.get("read_only") for item in report),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    stream.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    return 0 if payload["passed"] else 1
