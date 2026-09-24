"""Tests for gpcli — drives the shipped entry, not a reimplementation."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from play_store_mcp.cli import main
from play_store_mcp.cli.catalog import BROWSER_CAPABILITIES, SPECS
from play_store_mcp.cli.parser import build_parser
from play_store_mcp.models import ReviewReplyResult


def _mcp_tool_count() -> int:
    server = Path(__file__).resolve().parents[1] / "src" / "play_store_mcp" / "server.py"
    text = server.read_text(encoding="utf-8")
    return len(re.findall(r"^@mcp\.tool\(\)", text, flags=re.M))


def _tool_fn_count() -> int:
    import inspect

    import play_store_mcp.tools as tools

    skip = {"configure_client", "get_client"}
    names = [
        name
        for name, obj in vars(tools).items()
        if inspect.isfunction(obj) and name not in skip and not name.startswith("_")
    ]
    return len(names)


def _run(
    argv: list[str],
    *,
    client: Any | None = None,
    env: dict[str, str] | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    if env is not None and monkeypatch is not None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
    code = main(argv, client=client, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.fixture(autouse=True)
def _reset_client_provider() -> Any:
    import play_store_mcp.tools as tool_mod

    previous = tool_mod._client_provider
    yield
    tool_mod._client_provider = previous


def test_cli_import_does_not_load_mcp_server() -> None:
    import subprocess
    import sys

    probe = r"""
import sys
class Probe:
    def find_spec(self, name, path=None, target=None):
        if isinstance(name, str) and (name == "mcp" or name.startswith("mcp.")):
            raise ImportError(f"blocked {name}")
        return None
sys.meta_path.insert(0, Probe())
from play_store_mcp.cli import main
from play_store_mcp.cli.parser import build_parser
build_parser()
assert callable(main)
assert not any(m == "mcp" or m.startswith("mcp.") for m in sys.modules)
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_tool_count_matches_server_decorators() -> None:
    assert _tool_fn_count() == _mcp_tool_count()
    assert len(SPECS) == _mcp_tool_count()


def test_full_mapping_covers_every_mcp_tool() -> None:
    import inspect

    import play_store_mcp.tools as tools

    skip = {"configure_client", "get_client"}
    names = {
        name
        for name, obj in vars(tools).items()
        if inspect.isfunction(obj) and name not in skip and not name.startswith("_")
    }
    assert names == set(SPECS)


def test_generated_flags_include_package_and_kebab() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "--package" in help_text
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(["review", "reply", "-h"], stdout=stdout, stderr=stderr)
    text = stdout.getvalue() + stderr.getvalue()
    assert code == 0
    assert "--package" in text
    assert "--reply-text" in text


def test_fields_keeps_only_requested_paths() -> None:
    client = MagicMock()
    review = MagicMock()
    review.model_dump.return_value = {
        "review_id": "r1",
        "comment": "hello",
        "author_name": "x",
        "star_rating": 5,
    }
    client.get_reviews.return_value = [review]
    code, out, err = _run(
        [
            "review",
            "list",
            "--package",
            "com.example.app",
            "--limit",
            "5",
            "--fields",
            "reviewId,comments",
        ],
        client=client,
    )
    assert code == 0, err
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert len(payload) <= 5
    assert payload[0].keys() == {"reviewId", "comments"}
    assert payload[0]["reviewId"] == "r1"
    assert payload[0]["comments"] == "hello"


def test_write_without_yes_is_dry_run_zero_client_calls() -> None:
    client = MagicMock()
    code, out, err = _run(
        [
            "review",
            "reply",
            "rev-1",
            "--package",
            "com.example.app",
            "--reply-text",
            "Thanks",
            "--confirm",
            "com.example.app",
        ],
        client=client,
    )
    assert code == 0, err
    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert "method" in payload and "path" in payload and "body" in payload
    client.reply_to_review.assert_not_called()
    assert client.mock_calls == []


def test_write_with_yes_does_not_retry_http() -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.status = 429
    resp.get.return_value = "1"
    error = HttpError(resp, b'{"error":{"message":"rate","status":"RESOURCE_EXHAUSTED"}}')
    client.reply_to_review.side_effect = error
    code, out, err = _run(
        [
            "review",
            "reply",
            "rev-1",
            "--package",
            "com.example.app",
            "--reply-text",
            "Thanks",
            "--yes",
            "--confirm",
            "com.example.app",
        ],
        client=client,
    )
    assert code == 3, err
    payload = json.loads(err)
    assert payload["error"]["type"] == "api"
    assert payload["error"]["status"] == 429
    assert client.reply_to_review.call_count == 1


def test_body_at_file(tmp_path: Path) -> None:
    body = tmp_path / "body.json"
    body.write_text(json.dumps({"reply_text": "from-file"}), encoding="utf-8")
    client = MagicMock()
    code, out, err = _run(
        [
            "review",
            "reply",
            "rev-1",
            "--package",
            "com.example.app",
            "--body",
            f"@{body}",
            "--confirm",
            "com.example.app",
        ],
        client=client,
    )
    assert code == 0, err
    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert payload["body"]["reply_text"] == "from-file"


def test_confirm_mismatch_exits_2_no_client_call() -> None:
    client = MagicMock()
    code, out, err = _run(
        [
            "release",
            "promote",
            "--package",
            "com.example.app",
            "--from-track",
            "internal",
            "--to-track",
            "production",
            "--version-code",
            "1",
            "--yes",
        ],
        client=client,
    )
    assert code == 2, err
    payload = json.loads(err)
    assert payload["error"]["type"] == "usage"
    client.promote_release.assert_not_called()


def test_usage_error_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GPCLI_PACKAGE", raising=False)
    monkeypatch.delenv("GOOGLE_PLAY_STORE_CREDENTIALS", raising=False)
    code, out, err = _run(["review", "list"], monkeypatch=monkeypatch)
    assert code == 2, err
    payload = json.loads(err)
    assert payload["error"]["type"] == "usage"


def test_swallowed_write_failure_exit_3() -> None:
    """Client reply_to_review swallows HttpError into success=False; CLI must still exit 3."""
    client = MagicMock()
    client.reply_to_review.return_value = ReviewReplyResult(
        success=False,
        review_id="rev-1",
        message="Failed to reply: Not Found",
        error=(
            '<HttpError 404 when requesting https://androidpublisher.googleapis.com/'
            'androidpublisher/v3/applications/com.example.app/reviews/rev-1:reply?alt=json '
            'returned "Not Found">'
        ),
    )
    code, out, err = _run(
        [
            "review",
            "reply",
            "rev-1",
            "--package",
            "com.example.app",
            "--reply-text",
            "Thanks",
            "--yes",
            "--confirm",
            "com.example.app",
        ],
        client=client,
    )
    assert code == 3, err
    assert out == ""
    payload = json.loads(err)
    assert payload["error"]["type"] == "api"
    assert payload["error"]["status"] == 404
    client.reply_to_review.assert_called_once()


def test_api_httperror_exit_3() -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.status = 404
    resp.get.return_value = None
    client.get_reviews.side_effect = HttpError(
        resp, b'{"error":{"message":"not found","status":"NOT_FOUND"}}'
    )
    code, out, err = _run(
        ["review", "list", "--package", "com.no.such.app"],
        client=client,
    )
    assert code == 3, err
    payload = json.loads(err)
    assert payload["error"]["type"] == "api"
    assert payload["error"]["status"] == 404


def test_missing_credentials_exit_4(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_PLAY_STORE_CREDENTIALS", raising=False)
    monkeypatch.delenv("GPCLI_PACKAGE", raising=False)
    code, out, err = _run(["auth", "check"], monkeypatch=monkeypatch)
    assert code == 4
    payload = json.loads(err)
    assert payload["error"]["type"] == "auth"
    assert "GOOGLE_PLAY_STORE_CREDENTIALS" in payload["error"]["message"]


def test_tools_json_includes_browser_capabilities() -> None:
    code, out, err = _run(["tools", "--json"])
    assert code == 0, err
    rows = json.loads(out)
    mcp_rows = [row for row in rows if row.get("mcp_tool")]
    assert len(mcp_rows) == _mcp_tool_count()
    for row in mcp_rows:
        assert row["command"]
        assert "aliases" in row
        assert row["kind"] in {"read", "write"}
        assert row["risk"] in {"normal", "high"}
        assert "params" in row
    null_cmd = [row for row in rows if row.get("command") is None]
    assert len(null_cmd) == 3
    capabilities = {row["capability"] for row in null_cmd}
    assert capabilities == {
        "设置隐私政策 URL",
        "查看 Android 审核的真实状态",
        "自定义商店页（CSL）上传图片",
    }
    assert len(BROWSER_CAPABILITIES) == 3
