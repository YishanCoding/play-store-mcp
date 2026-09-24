"""Round-4 contracts for PR #7 (F01–F07). Mock client only; no live --yes."""

from __future__ import annotations

import io
import json
import socket
from typing import Any
from unittest.mock import MagicMock, call

import pytest
from googleapiclient.errors import HttpError

import play_store_mcp.cli as cli
import play_store_mcp.tools as tools
from play_store_mcp.cli.client import CliPlayStoreClient
from play_store_mcp.models import ReviewReplyResult

PKG = "com.example.app"


@pytest.fixture(autouse=True)
def isolate(monkeypatch: pytest.MonkeyPatch) -> Any:
    previous = tools._client_provider
    monkeypatch.delenv("GPCLI_PACKAGE", raising=False)
    monkeypatch.delenv("GOOGLE_PLAY_STORE_CREDENTIALS", raising=False)
    monkeypatch.setattr(cli, "_configure_logging", lambda **kwargs: None)

    def denied(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("A real network connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    yield
    tools._client_provider = previous


def run(argv: list[str], client: Any) -> tuple[int, Any, str]:
    out, err = io.StringIO(), io.StringIO()
    rc = cli.main(argv, client=client, stdout=out, stderr=err)
    return rc, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()


def model(data: dict[str, Any]) -> MagicMock:
    obj = MagicMock()
    obj.model_dump.return_value = data
    return obj


def http(status: int) -> HttpError:
    resp = MagicMock()
    resp.status = status
    resp.reason = "error"
    resp.get.return_value = "0"
    return HttpError(
        resp,
        json.dumps({"error": {"code": status, "message": "denied"}}).encode(),
        uri="https://mock.invalid/",
    )


def client_with_service() -> tuple[CliPlayStoreClient, MagicMock, list[Any]]:
    c = object.__new__(CliPlayStoreClient)
    c._logger = MagicMock()
    s = MagicMock()
    trace: list[Any] = []
    c._get_service = MagicMock(return_value=s)
    c._create_edit = MagicMock(side_effect=lambda p: trace.append(("create", p)) or "edit-1")
    c._commit_edit = MagicMock(side_effect=lambda p, e: trace.append(("commit", p, e)))
    c._delete_edit = MagicMock(side_effect=lambda p, e: trace.append(("delete", p, e)))
    c.validate_listing_text = MagicMock(return_value=[])
    return c, s, trace


def raw(review_id: str, valid: bool = True) -> dict[str, Any]:
    return {
        "reviewId": review_id,
        "comments": (
            [{"userComment": {"text": "hello", "starRating": 5, "reviewerLanguage": "en"}}]
            if valid
            else []
        ),
    }


def review_api(pages: list[Any]) -> tuple[CliPlayStoreClient, MagicMock]:
    c, s, _trace = client_with_service()
    it = iter(pages)

    def execute() -> Any:
        page = next(it)
        if isinstance(page, BaseException):
            raise page
        return page

    s.reviews.return_value.list.return_value.execute.side_effect = execute
    return c, s.reviews.return_value.list


def test_F01_query_rollout_matches_body_and_flag() -> None:
    observed: dict[str, Any] = {}
    for style, option in (
        ("query", ["--query", '{"rollout_percentage":1}']),
        ("body", ["--body", '{"rollout_percentage":1}']),
        ("flag", ["--rollout-percentage", "1"]),
    ):
        c, s, trace = client_with_service()
        tracks = s.edits.return_value.tracks.return_value
        tracks.get.return_value.execute.side_effect = (
            lambda: trace.append(("get",)) or {"releases": [{"versionCodes": ["1"]}]}
        )
        tracks.update.return_value.execute.side_effect = lambda: trace.append(("update",)) or {}
        argv = [
            "release",
            "promote",
            "--package",
            PKG,
            "--from-track",
            "internal",
            "--to-track",
            "production",
            "--version-code",
            "1",
            "--yes",
            "--confirm",
            PKG,
            *option,
        ]
        rc, out, err = run(argv, c)
        assert (rc, err) == (0, "")
        sent = tracks.update.call_args.kwargs
        assert sent["packageName"] == PKG and sent["editId"] == "edit-1" and sent["track"] == "production"
        tracks.get.assert_called_once_with(packageName=PKG, editId="edit-1", track="internal")
        assert [t[0] for t in trace] == ["create", "get", "update", "commit"]
        observed[style] = sent["body"]["releases"][0]
    for style in ("query", "body", "flag"):
        assert observed[style]["status"] == "inProgress"
        assert observed[style]["userFraction"] == 0.01


@pytest.mark.parametrize("variant", ["body", "query", "duplicate"])
def test_F02_abbreviated_package_is_unrecognized(variant: str) -> None:
    c = MagicMock()
    c.update_listing.return_value = model({"success": True})
    suffix = ["--language", "en-US", "--title", "T"]
    if variant == "duplicate":
        argv = ["--package", "com.a", "listing", "update", "--pack", "com.b", *suffix]
    else:
        argv = ["--pack", "com.a", "listing", "update", *suffix, "--" + variant, '{"package_name":"com.b"}']
    rc, _out, err = run([*argv, "--yes"], c)
    assert rc == 2
    assert "unrecognized" in err.lower() or "pack" in err
    assert c.mock_calls == []
    rc_dry, _dry, _ = run(argv, c)
    assert rc_dry == 2 and c.mock_calls == []
    full = ["--package" if x == "--pack" else x for x in argv]
    rc_full, _, _ = run([*full, "--yes"], c)
    assert rc_full == 2 and c.mock_calls == []


def test_F03_listing_get_503_aborts_without_update_or_commit() -> None:
    c, s, trace = client_with_service()
    listings = s.edits.return_value.listings.return_value

    def get_result() -> dict[str, Any]:
        trace.append(("get",))
        raise http(503)

    listings.get.return_value.execute.side_effect = get_result
    listings.update.return_value.execute.side_effect = lambda: trace.append(("update",)) or {}
    argv = [
        "listing",
        "batch-update",
        "--package",
        PKG,
        "--updates",
        '[{"language":"en-US","title":"New"}]',
        "--commit",
        "true",
        "--yes",
    ]
    rc, out, err = run(argv, c)
    assert rc == 3 and out is None
    assert json.loads(err)["error"]["status"] == 503
    listings.update.assert_not_called()
    c._commit_edit.assert_not_called()
    c._delete_edit.assert_called_once_with(PKG, "edit-1")
    assert [t[0] for t in trace] == ["create", "get", "delete"]


def test_F03_listing_get_success_keeps_existing_fields() -> None:
    c, s, trace = client_with_service()
    listings = s.edits.return_value.listings.return_value
    existing = {
        "title": "Old",
        "shortDescription": "KEEP short",
        "fullDescription": "KEEP full",
        "video": "https://www.youtube.com/watch?v=example",
    }

    def get_result() -> dict[str, Any]:
        trace.append(("get",))
        return existing

    listings.get.return_value.execute.side_effect = get_result
    listings.update.return_value.execute.side_effect = lambda: trace.append(("update",)) or {}
    rc, out, err = run(
        [
            "listing",
            "batch-update",
            "--package",
            PKG,
            "--updates",
            '[{"language":"en-US","title":"New"}]',
            "--commit",
            "true",
            "--yes",
        ],
        c,
    )
    assert (rc, err) == (0, "") and out["success"] is True and out["commit"] is True
    sent = listings.update.call_args.kwargs
    assert sent["body"]["shortDescription"] == "KEEP short"
    assert "video" in sent["body"]
    assert [t[0] for t in trace] == ["create", "get", "update", "commit"]


def test_control_batch_update_error_is_reported() -> None:
    c, s, _trace = client_with_service()
    listings = s.edits.return_value.listings.return_value
    listings.get.return_value.execute.return_value = {}
    listings.update.return_value.execute.side_effect = http(403)
    rc, out, err = run(
        [
            "listing",
            "batch-update",
            "--package",
            PKG,
            "--updates",
            '[{"language":"en-US","title":"New"}]',
            "--commit",
            "true",
            "--yes",
        ],
        c,
    )
    assert rc == 3 and out is None
    assert json.loads(err)["error"]["status"] == 403
    c._commit_edit.assert_not_called()
    c._delete_edit.assert_called_once_with(PKG, "edit-1")


def test_F04_filtered_id_does_not_drop_later_valid_review() -> None:
    pages = [
        {"reviews": [raw("r1", False)], "tokenPagination": {"nextPageToken": "T2"}},
        {"reviews": [raw("r1"), raw("r2")]},
    ]
    c, request = review_api(pages)
    rc, out, err = run(["review", "list", "--package", PKG, "--all"], c)
    assert (rc, err) == (0, "")
    ids = [r["review_id"] for r in out]
    assert ids == ["r1", "r2"]
    assert request.call_args_list == [
        call(packageName=PKG, maxResults=100),
        call(packageName=PKG, maxResults=100, token="T2"),
    ]


def test_F05_zero_limit_is_usage_error_zero_requests() -> None:
    c, request = review_api(
        [{"reviews": [raw("r1"), raw("r2")], "tokenPagination": {"nextPageToken": "T2"}}]
    )
    rc, out, err = run(["review", "list", "--package", PKG, "--all", "--limit", "0"], c)
    assert rc == 2 and out is None
    assert json.loads(err)["error"]["type"] == "usage"
    request.assert_not_called()


def test_F06_cli_does_not_replace_shared_mcp_provider() -> None:
    import play_store_mcp.server as server

    mcp_client = MagicMock()
    cli_client = object.__new__(CliPlayStoreClient)
    mcp_client.get_reviews.return_value = [model({"review_id": "MCP"})]
    cli_client.get_reviews = MagicMock(return_value=[model({"review_id": "CLI"})])
    tools.configure_client(lambda: mcp_client)
    before = server.get_reviews(PKG)
    rc, out, err = run(
        ["listing", "update", "--package", PKG, "--language", "en-US", "--title", "T"],
        cli_client,
    )
    assert (rc, err) == (0, "") and out["dry_run"] is True
    cli_client.get_reviews.assert_not_called()
    after = server.get_reviews(PKG)
    assert before == [{"review_id": "MCP"}] and after == [{"review_id": "MCP"}]
    assert tools.get_client() is mcp_client
    mcp_client.get_reviews.assert_called()
    cli_client.get_reviews.assert_not_called()


@pytest.mark.parametrize("placement", ["before", "between", "after", "alias"])
def test_control_write_guard_and_confirmation_all_placements(placement: str) -> None:
    c = MagicMock()
    c.reply_to_review.return_value = ReviewReplyResult(success=True, review_id="r1", message="ok")
    flags = ["--package", PKG, "--confirm", PKG]
    tail = ["r1", "--reply-text", "Thanks"]
    paths = {
        "before": [*flags, "review", "reply", *tail],
        "between": ["review", *flags, "reply", *tail],
        "after": ["review", "reply", *tail, *flags],
        "alias": [*flags, "reply-to-review", *tail],
    }
    argv = paths[placement]
    rc, out, err = run(argv, c)
    assert (rc, err) == (0, "") and out["dry_run"] is True and c.mock_calls == []
    rc, _, err = run(["--yes", *argv], c)
    assert (rc, err) == (0, "")
    c.reply_to_review.assert_called_once_with(package_name=PKG, review_id="r1", reply_text="Thanks")
    c.reset_mock()
    bad = [*argv, "--yes", "--confirm", "com.other"]
    rc, _, _ = run(bad, c)
    assert rc == 2 and c.mock_calls == []


@pytest.mark.parametrize("opt", ["--body", "--query"])
def test_control_explicit_and_positional_json_conflicts(opt: str) -> None:
    for obj in ({"package_name": "com.other"}, {"order_id": "O2"}):
        c = MagicMock()
        rc, _, _ = run(
            ["order", "refund", "O1", "--package", PKG, "--confirm", PKG, "--yes", opt, json.dumps(obj)],
            c,
        )
        assert rc == 2 and c.mock_calls == []


def test_F07_all_read_retries_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    c, request = review_api([http(503), {"reviews": [raw("r1")]}])
    rc, out, err = run(["review", "list", "--package", PKG, "--all"], c)
    assert (rc, err) == (0, "")
    assert [r["review_id"] for r in out] == ["r1"]
    assert request.call_count == 2
    control = MagicMock()
    control.get_reviews.side_effect = [http(503), [model({"review_id": "r1"})]]
    plain_rc, _, _ = run(["review", "list", "--package", PKG], control)
    assert plain_rc == 0 and control.get_reviews.call_count == 2
