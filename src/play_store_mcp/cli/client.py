"""gpcli-only PlayStoreClient extensions.

These behaviors are intentionally NOT part of `play_store_mcp/client.py`,
which is shared with the MCP server and must stay byte-identical to
origin/main. gpcli needs two things the shared client does not provide:

  1. Real HTTP status codes on failures the shared client swallows into a
     plain message string (so `gpcli` can set a meaningful process exit
     code / `error.status`).
  2. True token-based pagination for `--all` (the shared client's
     `get_reviews` only supports `startIndex` offset pagination, which the
     Play Developer API does not reliably honor).

`CliPlayStoreClient` is only ever constructed by `cli._make_client()`; the
MCP server always uses the base `PlayStoreClient` and never sees this
module.
"""

from __future__ import annotations

from typing import Any

from googleapiclient.errors import HttpError

from play_store_mcp.client import PlayStoreClient, PlayStoreClientError
from play_store_mcp.models import AppDetails, ListingBatchUpdateResult, Review


class CliPlayStoreClient(PlayStoreClient):
    """PlayStoreClient subclass used only by gpcli."""

    # ------------------------------------------------------------------
    # R2-F01 / P3: surface real failures instead of swallowing them.
    # ------------------------------------------------------------------

    def get_app_details(self, package_name: str, language: str = "en-US") -> AppDetails:
        """Same as the base client, but does not swallow a listing fetch error.

        The base `PlayStoreClient.get_app_details` catches `HttpError` on the
        listings().get() call and silently falls back to an empty listing.
        gpcli wants that failure to surface as an API error (exit code 3)
        instead of returning a details object with blank title/description.
        """
        service = self._get_service()
        edit_id = self._create_edit(package_name)
        try:
            details = (
                service.edits().details().get(packageName=package_name, editId=edit_id).execute()
            )
            listing = (
                service.edits()
                .listings()
                .get(packageName=package_name, editId=edit_id, language=language)
                .execute()
            )
            return AppDetails(
                package_name=package_name,
                title=listing.get("title"),
                short_description=listing.get("shortDescription"),
                full_description=listing.get("fullDescription"),
                default_language=details.get("defaultLanguage"),
                developer_name=None,
                developer_email=details.get("contactEmail"),
                developer_website=details.get("contactWebsite"),
            )
        finally:
            self._delete_edit(package_name, edit_id)

    def batch_update_listings(
        self,
        package_name: str,
        updates: list[dict[str, Any]],
        commit: bool = False,
    ) -> ListingBatchUpdateResult:
        """Same as the base client, but attaches the HTTP status on failure.

        Validation-only / dry-run behavior (commit=False, or validation
        failures before any network call) is delegated to the base client
        unchanged. Only the real-commit network path is duplicated here, so
        the CLI can attach `status` to the error the base client reports as
        a bare message string.
        """
        if not commit:
            return super().batch_update_listings(package_name, updates, commit=commit)

        errors: list[dict[str, Any]] = []
        validated_languages: list[str] = []
        normalized_updates: list[dict[str, Any]] = []

        for index, update in enumerate(updates):
            language = update.get("language")
            if not isinstance(language, str) or not language:
                errors.append(
                    {
                        "index": index,
                        "language": language,
                        "field": "language",
                        "message": "language is required",
                    }
                )
                continue

            provided_fields = {
                field: update[field]
                for field in ("title", "short_description", "full_description", "video")
                if field in update
            }
            if not provided_fields:
                errors.append(
                    {
                        "index": index,
                        "language": language,
                        "field": "updates",
                        "message": "At least one listing field must be provided",
                    }
                )
                continue

            validation_errors = self.validate_listing_text(
                title=update.get("title"),
                short_description=update.get("short_description"),
                full_description=update.get("full_description"),
            )
            if validation_errors:
                for validation_error in validation_errors:
                    error_data = validation_error.model_dump()
                    error_data["index"] = index
                    error_data["language"] = language
                    errors.append(error_data)
                continue

            validated_languages.append(language)
            normalized_updates.append({"language": language, **provided_fields})

        if errors:
            return ListingBatchUpdateResult(
                success=False,
                package_name=package_name,
                commit=False,
                validated_languages=validated_languages,
                errors=errors,
                message="Listing batch validation failed; no edit was created.",
            )

        service = self._get_service()
        edit_id = self._create_edit(package_name)
        updated_languages: list[str] = []

        try:
            for update in normalized_updates:
                language = update["language"]
                try:
                    current_listing = (
                        service.edits()
                        .listings()
                        .get(packageName=package_name, editId=edit_id, language=language)
                        .execute()
                    )
                except HttpError as listing_error:
                    status = int(getattr(listing_error.resp, "status", 0) or 0)
                    if status == 404:
                        current_listing = {}
                    else:
                        raise

                update_body = {
                    "title": update.get("title", current_listing.get("title", "")),
                    "fullDescription": update.get(
                        "full_description", current_listing.get("fullDescription", "")
                    ),
                    "shortDescription": update.get(
                        "short_description", current_listing.get("shortDescription", "")
                    ),
                }
                if "video" in update:
                    update_body["video"] = update["video"]
                elif current_listing.get("video") is not None:
                    update_body["video"] = current_listing["video"]

                service.edits().listings().update(
                    packageName=package_name,
                    editId=edit_id,
                    language=language,
                    body=update_body,
                ).execute()
                updated_languages.append(language)

            self._commit_edit(package_name, edit_id)
            return ListingBatchUpdateResult(
                success=True,
                package_name=package_name,
                commit=True,
                edit_id=edit_id,
                validated_languages=validated_languages,
                updated_languages=updated_languages,
                message=f"Successfully updated {len(updated_languages)} listing(s).",
            )
        except HttpError as e:
            self._logger.exception("Failed to batch update listings", error=str(e))
            self._delete_edit(package_name, edit_id)
            status = int(getattr(e.resp, "status", 0) or 0) or None
            return ListingBatchUpdateResult(
                success=False,
                package_name=package_name,
                commit=False,
                edit_id=edit_id,
                validated_languages=validated_languages,
                updated_languages=updated_languages,
                errors=[{"message": str(e), "status": status}],
                message="Failed to batch update listings; edit was deleted.",
            )
        except Exception as e:
            self._logger.exception("Failed to batch update listings", error=str(e))
            self._delete_edit(package_name, edit_id)
            return ListingBatchUpdateResult(
                success=False,
                package_name=package_name,
                commit=False,
                edit_id=edit_id,
                validated_languages=validated_languages,
                updated_languages=updated_languages,
                errors=[{"message": f"Failed to batch update listings: {e}"}],
                message="Failed to batch update listings; edit was deleted.",
            )

    # ------------------------------------------------------------------
    # R2-F02 / R2-F03: real token pagination for --all.
    # ------------------------------------------------------------------

    def list_reviews_page(
        self,
        package_name: str,
        page_token: str | None = None,
        max_results: int = 100,
        translation_language: str | None = None,
    ) -> dict[str, Any]:
        """One raw `reviews().list()` page, unfiltered.

        Unlike `get_reviews`, this:
          - does not drop reviews without a `userComment` (that filtering
            happens in the CLI's own `--all` loop, after paging, so it
            cannot cause pagination to stop early);
          - pages with the API's own opaque `token` /
            `tokenPagination.nextPageToken`, not a `startIndex` offset the
            server is free to ignore.
        """
        self._logger.info("Fetching reviews page", package_name=package_name)
        service = self._get_service()
        request_kwargs: dict[str, Any] = {
            "packageName": package_name,
            "maxResults": max_results,
        }
        if page_token:
            request_kwargs["token"] = page_token
        if translation_language:
            request_kwargs["translationLanguage"] = translation_language
        try:
            return service.reviews().list(**request_kwargs).execute()
        except HttpError as e:
            self._logger.exception("Failed to fetch reviews page", error=str(e))
            raise PlayStoreClientError(f"Failed to fetch reviews: {e.reason}") from e


def review_from_raw(review_data: dict[str, Any]) -> Review | None:
    """Convert one raw Play Developer API review entry to a `Review`.

    Mirrors `PlayStoreClient.get_reviews`'s per-item conversion (including
    dropping entries with no `userComment`) so `--all` output matches the
    non-paged output shape exactly.
    """
    comments = review_data.get("comments") or []
    user_comment: dict[str, Any] | None = None
    dev_comment: dict[str, Any] | None = None
    for comment in comments:
        if "userComment" in comment:
            user_comment = comment["userComment"]
        if "developerComment" in comment:
            dev_comment = comment["developerComment"]

    if not user_comment:
        return None

    android_version = user_comment.get("androidOsVersion")
    return Review(
        review_id=review_data.get("reviewId", ""),
        author_name=review_data.get("authorName", "Anonymous"),
        star_rating=user_comment.get("starRating", 0),
        comment=user_comment.get("text", ""),
        language=user_comment.get("reviewerLanguage", "en"),
        device=user_comment.get("device"),
        android_version=str(android_version) if android_version is not None else None,
        app_version_code=user_comment.get("appVersionCode"),
        app_version_name=user_comment.get("appVersionName"),
        developer_reply=dev_comment.get("text") if dev_comment else None,
    )
