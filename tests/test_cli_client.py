"""Tests for the gpcli-only CliPlayStoreClient (src/play_store_mcp/cli/client.py).

These exercise CliPlayStoreClient directly against a mocked Google API
service (same style as tests/test_client_extended.py), not through
`gpcli` main(), so they cover the actual HttpError-handling and
pagination code paths gpcli.main()'s tests bypass by injecting a
MagicMock client.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from play_store_mcp.cli.client import CliPlayStoreClient, review_from_raw
from play_store_mcp.client import PlayStoreClient, PlayStoreClientError


def _make_http_error(status: int, reason: str = "error") -> HttpError:
    resp = MagicMock()
    resp.status = status
    resp.reason = reason
    return HttpError(resp=resp, content=reason.encode())


@pytest.fixture
def cli_client(
    _mock_credentials: MagicMock,
    _mock_service: MagicMock,
    tmp_path: Any,
) -> CliPlayStoreClient:
    creds_file = tmp_path / "service-account.json"
    creds_file.write_text('{"type": "service_account"}')
    return CliPlayStoreClient(credentials_path=str(creds_file))


# ---------------------------------------------------------------------------
# get_app_details: R2 P3 — surface listing fetch errors instead of swallowing
# ---------------------------------------------------------------------------


class TestGetAppDetailsRaises:
    def test_listing_http_error_propagates(
        self, cli_client: CliPlayStoreClient, _mock_service: MagicMock
    ) -> None:
        mock_edits = _mock_service.edits.return_value
        mock_edits.insert.return_value.execute.return_value = {"id": "edit-123"}
        mock_edits.details.return_value.get.return_value.execute.return_value = {
            "defaultLanguage": "en-US",
        }
        mock_edits.listings.return_value.get.return_value.execute.side_effect = (
            _make_http_error(404)
        )
        mock_edits.delete.return_value.execute.return_value = None

        with pytest.raises(HttpError):
            cli_client.get_app_details("com.example.app", "fr-FR")

        # The edit is still cleaned up even though the call raised.
        mock_edits.delete.assert_called_once()


# ---------------------------------------------------------------------------
# batch_update_listings: R2-F01 — HTTP status only on the CLI subclass
# ---------------------------------------------------------------------------


class TestBatchUpdateListingsMcpUnaffected:
    """R2-F01: the shared client.py path (MCP) must be byte-identical to
    origin/main. Drives the *same* mocked service through both the base
    PlayStoreClient (what the MCP server uses) and CliPlayStoreClient
    (what gpcli uses) and asserts they diverge only in the CLI's added
    `status` field."""

    def test_mcp_path_has_no_status_field_cli_path_does(
        self,
        _mock_credentials: MagicMock,
        _mock_service: MagicMock,
        tmp_path: Any,
    ) -> None:
        creds_file = tmp_path / "service-account.json"
        creds_file.write_text('{"type": "service_account"}')

        def _boom() -> None:
            mock_edits = _mock_service.edits.return_value
            mock_edits.insert.return_value.execute.return_value = {"id": "edit-1"}
            mock_edits.listings.return_value.get.return_value.execute.return_value = {}
            mock_edits.listings.return_value.update.return_value.execute.side_effect = (
                _make_http_error(403, "denied")
            )
            mock_edits.delete.return_value.execute.return_value = None

        updates = [{"language": "en-US", "title": "Hello"}]

        # MCP path: unmodified base client.
        _boom()
        mcp_client = PlayStoreClient(credentials_path=str(creds_file))
        mcp_result = mcp_client.batch_update_listings(
            "com.example.app", updates, commit=True
        )
        assert mcp_result.errors == [
            {"message": "Failed to batch update listings: denied"}
        ]

        # CLI path: the subclass, same mock, same scenario.
        _boom()
        cli_client_ = CliPlayStoreClient(credentials_path=str(creds_file))
        cli_result = cli_client_.batch_update_listings(
            "com.example.app", updates, commit=True
        )
        assert cli_result.errors is not None
        assert cli_result.errors[0]["status"] == 403
        assert "denied" in cli_result.errors[0]["message"]


class TestBatchUpdateListingsStatus:
    def test_commit_http_error_includes_status(
        self, cli_client: CliPlayStoreClient, _mock_service: MagicMock
    ) -> None:
        mock_edits = _mock_service.edits.return_value
        mock_edits.insert.return_value.execute.return_value = {"id": "edit-1"}
        mock_edits.listings.return_value.get.return_value.execute.return_value = {}
        mock_edits.listings.return_value.update.return_value.execute.side_effect = (
            _make_http_error(403, "denied")
        )
        mock_edits.delete.return_value.execute.return_value = None

        result = cli_client.batch_update_listings(
            "com.example.app",
            [{"language": "en-US", "title": "Hello"}],
            commit=True,
        )

        assert result.success is False
        assert result.errors is not None
        assert result.errors[0]["status"] == 403
        assert "denied" in result.errors[0]["message"]
        mock_edits.delete.assert_called_once()

    def test_validation_failure_never_creates_edit(
        self, cli_client: CliPlayStoreClient, _mock_service: MagicMock
    ) -> None:
        mock_edits = _mock_service.edits.return_value

        result = cli_client.batch_update_listings(
            "com.example.app",
            [{"language": "en-US"}],  # no title/description/video
            commit=True,
        )

        assert result.success is False
        mock_edits.insert.assert_not_called()

    def test_commit_success_matches_base_shape(
        self, cli_client: CliPlayStoreClient, _mock_service: MagicMock
    ) -> None:
        mock_edits = _mock_service.edits.return_value
        mock_edits.insert.return_value.execute.return_value = {"id": "edit-1"}
        mock_edits.listings.return_value.get.return_value.execute.return_value = {}
        mock_edits.listings.return_value.update.return_value.execute.return_value = {}
        mock_edits.commit.return_value.execute.return_value = {}

        result = cli_client.batch_update_listings(
            "com.example.app",
            [{"language": "en-US", "title": "Hello"}],
            commit=True,
        )

        assert result.success is True
        assert result.commit is True
        assert result.updated_languages == ["en-US"]


# ---------------------------------------------------------------------------
# list_reviews_page / review_from_raw: R2-F02 / R2-F03
# ---------------------------------------------------------------------------


class TestListReviewsPage:
    def test_forwards_page_token_and_max_results(
        self, cli_client: CliPlayStoreClient, _mock_service: MagicMock
    ) -> None:
        _mock_service.reviews.return_value.list.return_value.execute.return_value = {
            "reviews": [],
            "tokenPagination": {"nextPageToken": "next-tok"},
        }

        page = cli_client.list_reviews_page(
            "com.example.app", page_token="tok-1", max_results=50
        )

        assert page["tokenPagination"]["nextPageToken"] == "next-tok"
        _, kwargs = _mock_service.reviews.return_value.list.call_args
        assert kwargs["packageName"] == "com.example.app"
        assert kwargs["maxResults"] == 50
        assert kwargs["token"] == "tok-1"

    def test_no_token_key_on_first_page(
        self, cli_client: CliPlayStoreClient, _mock_service: MagicMock
    ) -> None:
        _mock_service.reviews.return_value.list.return_value.execute.return_value = {
            "reviews": []
        }
        cli_client.list_reviews_page("com.example.app")
        _, kwargs = _mock_service.reviews.return_value.list.call_args
        assert "token" not in kwargs

    def test_http_error_wrapped_as_client_error(
        self, cli_client: CliPlayStoreClient, _mock_service: MagicMock
    ) -> None:
        _mock_service.reviews.return_value.list.return_value.execute.side_effect = (
            _make_http_error(500, "boom")
        )
        with pytest.raises(PlayStoreClientError):
            cli_client.list_reviews_page("com.example.app")


class TestReviewFromRaw:
    def test_drops_reviews_without_user_comment(self) -> None:
        assert review_from_raw({"reviewId": "r1", "comments": []}) is None
        assert (
            review_from_raw(
                {"reviewId": "r1", "comments": [{"developerComment": {"text": "hi"}}]}
            )
            is None
        )

    def test_builds_review_with_developer_reply(self) -> None:
        review = review_from_raw(
            {
                "reviewId": "r1",
                "authorName": "Bob",
                "comments": [
                    {
                        "userComment": {
                            "text": "great",
                            "starRating": 5,
                            "reviewerLanguage": "en",
                            "androidOsVersion": 13,
                        }
                    },
                    {"developerComment": {"text": "thanks"}},
                ],
            }
        )
        assert review is not None
        assert review.review_id == "r1"
        assert review.comment == "great"
        assert review.android_version == "13"
        assert review.developer_reply == "thanks"
