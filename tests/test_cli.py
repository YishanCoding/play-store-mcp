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


def _review_item(review_id: str) -> MagicMock:
    item = MagicMock()
    item.model_dump.return_value = {
        "review_id": review_id,
        "comment": "hello",
        "author_name": "x",
        "star_rating": 5,
    }
    return item


@pytest.mark.parametrize("package_before", [True, False])
def test_f01_package_flag_both_positions(package_before: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GPCLI_PACKAGE", "com.b")
    client = MagicMock()
    pkg_flags = ["--package", "com.a"]
    cmd = ["listing", "update", "--language", "en-US", "--title", "T"]
    argv = [*pkg_flags, *cmd] if package_before else [*cmd, *pkg_flags]
    code, out, err = _run(argv, client=client, monkeypatch=monkeypatch)
    assert code == 0, err
    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert "/applications/com.a/" in payload["path"]
    assert "com.b" not in payload["path"]
    assert client.mock_calls == []


@pytest.mark.parametrize("yes_before", [True, False])
def test_f01_yes_flag_both_positions(yes_before: bool) -> None:
    client = MagicMock()
    client.reply_to_review.return_value = ReviewReplyResult(
        success=True, review_id="rev-1", message="ok"
    )
    yes_flags = ["--yes", "--confirm", "com.example.app"]
    cmd = [
        "review",
        "reply",
        "rev-1",
        "--package",
        "com.example.app",
        "--reply-text",
        "Thanks",
    ]
    argv = [*yes_flags, *cmd] if yes_before else [*cmd, *yes_flags]
    code, out, err = _run(argv, client=client)
    assert code == 0, err
    client.reply_to_review.assert_called_once()
    kwargs = client.reply_to_review.call_args.kwargs
    assert kwargs["package_name"] == "com.example.app"
    assert kwargs["review_id"] == "rev-1"


@pytest.mark.parametrize("limit_before", [True, False])
def test_f01_limit_flag_both_positions(limit_before: bool) -> None:
    client = MagicMock()
    client.get_reviews.return_value = [_review_item(f"r{i}") for i in range(5)]
    limit_flags = ["--limit", "2"]
    cmd = ["review", "list", "--package", "com.example.app"]
    argv = [*limit_flags, *cmd] if limit_before else [*cmd, *limit_flags]
    code, out, err = _run(argv, client=client)
    assert code == 0, err
    payload = json.loads(out)
    assert len(payload) == 2
    assert client.get_reviews.call_args.kwargs["max_results"] == 2


@pytest.mark.parametrize("fields_before", [True, False])
def test_f01_fields_flag_both_positions(fields_before: bool) -> None:
    client = MagicMock()
    client.get_reviews.return_value = [_review_item("r1")]
    field_flags = ["--fields", "reviewId,comments"]
    cmd = ["review", "list", "--package", "com.example.app"]
    argv = [*field_flags, *cmd] if fields_before else [*cmd, *field_flags]
    code, out, err = _run(argv, client=client)
    assert code == 0, err
    payload = json.loads(out)
    assert payload[0].keys() == {"reviewId", "comments"}


def test_f02_user_confirm_mismatch_exit_2_no_request() -> None:
    client = MagicMock()
    code, out, err = _run(
        [
            "user",
            "delete",
            "a@b.c",
            "--developer-id",
            "1",
            "--confirm",
            "anything",
            "--yes",
        ],
        client=client,
    )
    assert code == 2, err
    payload = json.loads(err)
    assert payload["error"]["type"] == "usage"
    assert "developer-id" in payload["error"]["message"]
    client.delete_user.assert_not_called()
    assert client.mock_calls == []


def test_f02_user_confirm_matches_developer_id() -> None:
    client = MagicMock()
    code, out, err = _run(
        ["user", "delete", "a@b.c", "--developer-id", "1", "--confirm", "1"],
        client=client,
    )
    assert code == 0, err
    payload = json.loads(out)
    assert payload["dry_run"] is True
    client.delete_user.assert_not_called()


def test_f03_body_package_name_conflicts_with_package_flag() -> None:
    client = MagicMock()
    code, out, err = _run(
        [
            "listing",
            "update",
            "--package",
            "com.a",
            "--language",
            "en-US",
            "--title",
            "T",
            "--body",
            json.dumps({"package_name": "com.b"}),
        ],
        client=client,
    )
    assert code == 2, err
    payload = json.loads(err)
    assert payload["error"]["type"] == "usage"
    client.update_listing.assert_not_called()
    assert client.mock_calls == []


def test_f04_body_conflicts_with_positional() -> None:
    client = MagicMock()
    code, out, err = _run(
        [
            "order",
            "refund",
            "O1",
            "--package",
            "p",
            "--confirm",
            "p",
            "--body",
            json.dumps({"order_id": "O2"}),
        ],
        client=client,
    )
    assert code == 2, err
    payload = json.loads(err)
    assert payload["error"]["type"] == "usage"
    client.refund_order.assert_not_called()
    assert client.mock_calls == []


def test_f05_all_paginates_reviews() -> None:
    client = MagicMock()

    def side_effect(**kwargs: Any) -> list[MagicMock]:
        start = int(kwargs.get("start_index") or 0)
        size = int(kwargs.get("max_results") or 100)
        total = 150
        return [_review_item(f"r{i}") for i in range(start, min(start + size, total))]

    client.get_reviews.side_effect = side_effect
    code, out, err = _run(
        ["review", "list", "--package", "com.example.app", "--all"],
        client=client,
    )
    assert code == 0, err
    payload = json.loads(out)
    assert len(payload) == 150
    assert client.get_reviews.call_count == 2
    first = client.get_reviews.call_args_list[0].kwargs
    second = client.get_reviews.call_args_list[1].kwargs
    assert first["start_index"] == 0
    assert first["max_results"] == 100
    assert second["start_index"] == 100
    assert second["max_results"] == 100
    assert first["package_name"] == "com.example.app"


def test_f05_all_with_limit_is_cap() -> None:
    client = MagicMock()

    def side_effect(**kwargs: Any) -> list[MagicMock]:
        start = int(kwargs.get("start_index") or 0)
        size = int(kwargs.get("max_results") or 100)
        return [_review_item(f"r{i}") for i in range(start, start + size)]

    client.get_reviews.side_effect = side_effect
    code, out, err = _run(
        ["--all", "--limit", "5", "review", "list", "--package", "com.example.app"],
        client=client,
    )
    assert code == 0, err
    payload = json.loads(out)
    assert len(payload) == 5
    assert client.get_reviews.call_args.kwargs["max_results"] == 5
    assert client.get_reviews.call_args.kwargs["start_index"] == 0


def test_f05_all_unsupported_command_exit_2() -> None:
    client = MagicMock()
    code, out, err = _run(
        ["app", "get", "--package", "com.example.app", "--all"],
        client=client,
    )
    assert code == 2, err
    payload = json.loads(err)
    assert payload["error"]["type"] == "usage"
    assert "--all" in payload["error"]["message"]
    client.get_app_details.assert_not_called()
    assert client.mock_calls == []


def test_pageable_tools_match_client_start_index() -> None:
    import inspect

    from play_store_mcp.cli.catalog import PAGEABLE_TOOLS
    from play_store_mcp.client import PlayStoreClient

    found: set[str] = set()
    for name in SPECS:
        method = getattr(PlayStoreClient, name, None)
        if method is None:
            continue
        try:
            sig = inspect.signature(method)
        except (TypeError, ValueError):
            continue
        if "start_index" in sig.parameters:
            found.add(name)
    assert found == set(PAGEABLE_TOOLS)


def test_unknown_body_key_exit_2() -> None:
    client = MagicMock()
    code, out, err = _run(
        [
            "listing",
            "update",
            "--package",
            "com.a",
            "--language",
            "en-US",
            "--title",
            "T",
            "--body",
            json.dumps({"not_a_param": 1}),
        ],
        client=client,
    )
    assert code == 2, err
    payload = json.loads(err)
    assert payload["error"]["type"] == "usage"
    assert client.mock_calls == []


def test_missing_file_dry_run_exit_2() -> None:
    client = MagicMock()
    code, out, err = _run(
        [
            "app",
            "deploy",
            "--package",
            "com.a",
            "--track",
            "internal",
            "--file",
            "/no/such/file.aab",
            "--confirm",
            "com.a",
        ],
        client=client,
    )
    assert code == 2, err
    payload = json.loads(err)
    assert payload["error"]["type"] == "usage"
    assert client.mock_calls == []


def test_batch_update_listings_errors_include_http_status() -> None:
    client = MagicMock()
    result = MagicMock()
    result.model_dump.return_value = {
        "success": False,
        "message": "Failed to batch update listings; edit was deleted.",
        "errors": [
            {
                "message": (
                    '<HttpError 403 when requesting https://androidpublisher.googleapis.com/ '
                    'returned "Forbidden">'
                ),
                "status": 403,
            }
        ],
    }
    client.batch_update_listings.return_value = result
    code, out, err = _run(
        [
            "listing",
            "batch-update",
            "--package",
            "com.a",
            "--yes",
            "--updates",
            json.dumps([{"language": "en-US", "title": "Hello"}]),
        ],
        client=client,
    )
    assert code == 3, err
    payload = json.loads(err)
    assert payload["error"]["type"] == "api"
    assert payload["error"]["status"] == 403
    assert "HttpError 403" in payload["error"]["detail"]
    client.batch_update_listings.assert_called_once()
