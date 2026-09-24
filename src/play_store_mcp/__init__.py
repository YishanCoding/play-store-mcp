"""Play Store MCP Server - Google Play Developer API integration via MCP."""

from __future__ import annotations

from typing import Any

__version__ = "0.1.0"
__all__ = ["__version__", "main"]


def __getattr__(name: str) -> Any:
    if name == "main":
        from play_store_mcp.server import main as mcp_main

        return mcp_main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
