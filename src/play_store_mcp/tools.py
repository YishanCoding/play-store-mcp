"""Inspectable Play Store tool functions shared by MCP and gpcli.

This module must not import the MCP server framework. Client construction is
injected via configure_client() so gpcli --help does not load googleapiclient.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager as _contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from play_store_mcp.client import PlayStoreClient

GetClient = Callable[[], Any]
_client_provider: GetClient | None = None
_client_override: ContextVar[GetClient | None] = ContextVar(
    "play_store_mcp_client_override", default=None
)


def configure_client(provider: GetClient) -> None:
    """Install the function used by tools to obtain a PlayStoreClient."""
    global _client_provider
    _client_provider = provider


@_contextmanager
def _scoped_client(provider: GetClient) -> Iterator[None]:
    """Temporarily use `provider` for get_client() in this task, then restore.

    CLI uses this so it never overwrites the process-wide MCP provider.
    """
    token = _client_override.set(provider)
    try:
        yield
    finally:
        _client_override.reset(token)


def get_client() -> PlayStoreClient:
    """Return the configured PlayStoreClient."""
    override = _client_override.get()
    if override is not None:
        return override()
    if _client_provider is None:
        from play_store_mcp.client import PlayStoreClientError

        raise PlayStoreClientError("Play Store client is not configured")
    return _client_provider()


_VITALS_DIMENSIONS_HELP = (
    "Optional list of dimensions to group by. Common values: "
    "versionCode, apiLevel, deviceModel, deviceBrand, countryCode. "
    "Default: no grouping (overall totals only)."
)

_VITALS_PERMISSION_NOTE = (
    "Requires the service account to have 'Performance Analysis' permission "
    "in Play Console (Account settings > Users and permissions)."
)

def deploy_app(
    package_name: str,
    track: str,
    file_path: str,
    release_notes: str | None = None,
    release_notes_language: str = "en-US",
    rollout_percentage: float = 100.0,
) -> dict[str, Any]:
    """Deploy an APK or AAB file to a Play Store track.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        track: Release track - one of: internal, alpha, beta, production
        file_path: Absolute path to APK or AAB file
        release_notes: Optional release notes for this version (string for single language,
                      or use release_notes_multilang for multiple languages)
        release_notes_language: Language code for release notes (default: en-US)
        rollout_percentage: Rollout percentage (0-100). Default 100 for full rollout.

    Returns:
        Deployment result with success status and details
    """
    client = get_client()

    result = client.deploy_app(
        package_name=package_name,
        track=track,
        file_path=file_path,
        release_notes=release_notes,
        release_notes_language=release_notes_language,
        rollout_percentage=rollout_percentage,
    )

    return result.model_dump()

def deploy_app_multilang(
    package_name: str,
    track: str,
    file_path: str,
    release_notes: dict[str, str],
    rollout_percentage: float = 100.0,
) -> dict[str, Any]:
    """Deploy an APK or AAB file with multi-language release notes.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        track: Release track - one of: internal, alpha, beta, production
        file_path: Absolute path to APK or AAB file
        release_notes: Dictionary mapping language codes to release notes
                      (e.g., {"en-US": "Bug fixes", "es-ES": "Corrección de errores"})
        rollout_percentage: Rollout percentage (0-100). Default 100 for full rollout.

    Returns:
        Deployment result with success status and details
    """
    client = get_client()

    result = client.deploy_app(
        package_name=package_name,
        track=track,
        file_path=file_path,
        release_notes=release_notes,
        rollout_percentage=rollout_percentage,
    )

    return result.model_dump()

def promote_release(
    package_name: str,
    from_track: str,
    to_track: str,
    version_code: int,
    rollout_percentage: float = 100.0,
) -> dict[str, Any]:
    """Promote a release from one track to another.

    Args:
        package_name: App package name
        from_track: Source track (internal, alpha, beta)
        to_track: Destination track (alpha, beta, production)
        version_code: Version code to promote
        rollout_percentage: Rollout percentage for target track (0-100)

    Returns:
        Promotion result with success status and details
    """
    client = get_client()

    result = client.promote_release(
        package_name=package_name,
        from_track=from_track,
        to_track=to_track,
        version_code=version_code,
        rollout_percentage=rollout_percentage,
    )

    return result.model_dump()

def get_releases(package_name: str) -> list[dict[str, Any]]:
    """Get release status for all tracks of an app.

    Args:
        package_name: App package name

    Returns:
        List of tracks with their releases and version information
    """
    client = get_client()

    tracks = client.get_releases(package_name)
    return [track.model_dump() for track in tracks]

def halt_release(
    package_name: str,
    track: str,
    version_code: int,
) -> dict[str, Any]:
    """Halt a staged rollout.

    Use this to stop a release that is currently rolling out.
    The release will be marked as halted and users will stop receiving updates.

    Args:
        package_name: App package name
        track: Track containing the release (internal, alpha, beta, production)
        version_code: Version code of the release to halt

    Returns:
        Result with success status and details
    """
    client = get_client()

    result = client.halt_release(
        package_name=package_name,
        track=track,
        version_code=version_code,
    )

    return result.model_dump()

def update_rollout(
    package_name: str,
    track: str,
    version_code: int,
    rollout_percentage: float,
) -> dict[str, Any]:
    """Update the rollout percentage for a staged release.

    Use this to increase or decrease the percentage of users receiving an update.
    Set to 100 to complete the rollout.

    Args:
        package_name: App package name
        track: Track containing the release
        version_code: Version code of the staged release
        rollout_percentage: New rollout percentage (0-100)

    Returns:
        Result with success status and details
    """
    client = get_client()

    result = client.update_rollout(
        package_name=package_name,
        track=track,
        version_code=version_code,
        rollout_percentage=rollout_percentage,
    )

    return result.model_dump()

def get_app_details(
    package_name: str,
    language: str = "en-US",
) -> dict[str, Any]:
    """Get app details including title, description, and developer info.

    Args:
        package_name: App package name
        language: Language code for localized content (default: en-US)

    Returns:
        App details including title, descriptions, and developer information
    """
    client = get_client()

    details = client.get_app_details(package_name, language)
    return details.model_dump()

def get_reviews(
    package_name: str,
    max_results: int = 50,
    translation_language: str | None = None,
) -> list[dict[str, Any]]:
    """Get recent reviews for an app.

    Args:
        package_name: App package name
        max_results: Maximum number of reviews to return (default: 50, max: 100)
        translation_language: Optional language code to translate reviews to

    Returns:
        List of reviews with ratings, comments, and author info
    """
    client = get_client()

    reviews = client.get_reviews(
        package_name=package_name,
        max_results=min(max_results, 100),
        translation_language=translation_language,
    )

    return [review.model_dump() for review in reviews]

def reply_to_review(
    package_name: str,
    review_id: str,
    reply_text: str,
) -> dict[str, Any]:
    """Reply to a user review.

    Args:
        package_name: App package name
        review_id: ID of the review to reply to (from get_reviews)
        reply_text: Text of the reply (will be visible to the reviewer)

    Returns:
        Result with success status
    """
    client = get_client()

    result = client.reply_to_review(
        package_name=package_name,
        review_id=review_id,
        reply_text=reply_text,
    )

    return result.model_dump()

def list_subscriptions(package_name: str) -> list[dict[str, Any]]:
    """List all subscription products for an app.

    Args:
        package_name: App package name

    Returns:
        List of subscription products with their base plans
    """
    client = get_client()

    subscriptions = client.list_subscriptions(package_name)
    return [sub.model_dump() for sub in subscriptions]

def get_subscription_status(
    package_name: str,
    subscription_id: str,
    purchase_token: str,
) -> dict[str, Any]:
    """Get the status of a subscription purchase.

    Args:
        package_name: App package name
        subscription_id: Subscription product ID
        purchase_token: The purchase token from the client app

    Returns:
        Subscription purchase status including expiry and renewal info
    """
    client = get_client()

    status = client.get_subscription_purchase(
        package_name=package_name,
        subscription_id=subscription_id,
        token=purchase_token,
    )

    return status.model_dump()

def list_voided_purchases(
    package_name: str,
    max_results: int = 100,
) -> list[dict[str, Any]]:
    """List voided purchases (refunds, chargebacks).

    Args:
        package_name: App package name
        max_results: Maximum number of results (default: 100)

    Returns:
        List of voided purchases with reason and timing
    """
    client = get_client()

    voided = client.list_voided_purchases(
        package_name=package_name,
        max_results=max_results,
    )

    return [v.model_dump() for v in voided]

def get_vitals_overview(package_name: str) -> dict[str, Any]:
    """Get Android Vitals overview for an app.

    Shows crash rate, ANR rate, and other health metrics.
    Note: Full Vitals API access may require additional setup.

    Args:
        package_name: App package name

    Returns:
        Vitals overview with crash and ANR rates
    """
    client = get_client()

    vitals = client.get_vitals_overview(package_name)
    return vitals.model_dump()

def get_vitals_metrics(
    package_name: str,
    metric_type: str = "crashRate",
) -> list[dict[str, Any]]:
    """Get specific Android Vitals metrics for an app.

    Retrieve detailed metrics like crash rates, ANR rates, etc.
    Note: Full implementation requires Play Developer Reporting API setup.

    Args:
        package_name: App package name
        metric_type: Type of metric to retrieve (crashRate, anrRate, etc.)

    Returns:
        List of vitals metrics with values and benchmarks
    """
    client = get_client()

    metrics = client.get_vitals_metrics(package_name, metric_type)
    return [metric.model_dump() for metric in metrics]

def list_in_app_products(package_name: str) -> list[dict[str, Any]]:
    """List all in-app products for an app.

    Args:
        package_name: App package name

    Returns:
        List of in-app products with SKU, title, description, and pricing
    """
    client = get_client()

    products = client.list_in_app_products(package_name)
    return [product.model_dump() for product in products]

def get_in_app_product(
    package_name: str,
    sku: str,
) -> dict[str, Any]:
    """Get details of a specific in-app product.

    Args:
        package_name: App package name
        sku: Product SKU identifier

    Returns:
        In-app product details including title, description, and pricing
    """
    client = get_client()

    product = client.get_in_app_product(package_name, sku)
    return product.model_dump()

def get_listing(
    package_name: str,
    language: str = "en-US",
) -> dict[str, Any]:
    """Get store listing for a specific language.

    Args:
        package_name: App package name
        language: Language code (e.g., en-US, es-ES, fr-FR)

    Returns:
        Store listing with title, descriptions, and video
    """
    client = get_client()

    listing = client.get_listing(package_name, language)
    return listing.model_dump()

def update_listing(
    package_name: str,
    language: str,
    title: str | None = None,
    full_description: str | None = None,
    short_description: str | None = None,
    video: str | None = None,
) -> dict[str, Any]:
    """Update store listing for a specific language.

    Args:
        package_name: App package name
        language: Language code (e.g., en-US, es-ES, fr-FR)
        title: App title (max 30 characters, optional)
        full_description: Full description (max 4000 characters, optional)
        short_description: Short description (max 80 characters, optional)
        video: YouTube video URL (optional)

    Returns:
        Update result with success status
    """
    client = get_client()

    result = client.update_listing(
        package_name=package_name,
        language=language,
        title=title,
        full_description=full_description,
        short_description=short_description,
        video=video,
    )
    return result.model_dump()

def batch_update_listings(
    package_name: str,
    updates: list[dict[str, Any]],
    commit: bool = False,
) -> dict[str, Any]:
    """Validate or update store listings for multiple languages.

    This tool is dry-run by default: it validates all requested listing text
    locally and does not create a Google Play edit unless commit is explicitly
    set to true.

    Args:
        package_name: App package name
        updates: Items with language plus optional title, short_description,
            full_description, and video fields
        commit: If true, create one edit, update all locales, and commit it

    Returns:
        Batch validation/update result
    """
    client = get_client()

    result = client.batch_update_listings(
        package_name=package_name,
        updates=updates,
        commit=commit,
    )
    return result.model_dump()

def list_all_listings(package_name: str) -> list[dict[str, Any]]:
    """List all store listings for all languages.

    Args:
        package_name: App package name

    Returns:
        List of store listings for all configured languages
    """
    client = get_client()

    listings = client.list_all_listings(package_name)
    return [listing.model_dump() for listing in listings]

def get_testers(
    package_name: str,
    track: str,
) -> dict[str, Any]:
    """Get testers for a specific testing track.

    Args:
        package_name: App package name
        track: Track name (internal, alpha, beta)

    Returns:
        Tester information with list of email addresses
    """
    client = get_client()

    testers = client.get_testers(package_name, track)
    return testers.model_dump()

def update_testers(
    package_name: str,
    track: str,
    tester_emails: list[str],
    google_group_emails: list[str] | None = None,
) -> dict[str, Any]:
    """Update the list of testers for a track.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        track: Track name - one of: internal, alpha, beta
        tester_emails: List of individual tester email addresses
        google_group_emails: Optional list of Google Group email addresses

    Returns:
        Update result with success status
    """
    client = get_client()

    result = client.update_testers(
        package_name=package_name,
        track=track,
        tester_emails=tester_emails,
        google_group_emails=google_group_emails or [],
    )
    return result.model_dump()

def get_order(
    package_name: str,
    order_id: str,
) -> dict[str, Any]:
    """Get detailed order/transaction information.

    Args:
        package_name: App package name
        order_id: Order ID to retrieve

    Returns:
        Order details including product, purchase state, and token
    """
    client = get_client()

    order = client.get_order(package_name, order_id)
    return order.model_dump()

def get_expansion_file(
    package_name: str,
    version_code: int,
    expansion_file_type: str = "main",
) -> dict[str, Any]:
    """Get APK expansion file information.

    Expansion files are used for large apps (especially games) that exceed
    the 100MB APK size limit.

    Args:
        package_name: App package name
        version_code: APK version code
        expansion_file_type: Type of expansion file (main or patch)

    Returns:
        Expansion file information including size and references
    """
    client = get_client()

    expansion_file = client.get_expansion_file(package_name, version_code, expansion_file_type)
    return expansion_file.model_dump()

def upload_deobfuscation_file(
    package_name: str,
    version_code: int,
    file_path: str,
    deobfuscation_file_type: str = "proguard",
) -> dict[str, Any]:
    """Upload a ProGuard/R8 deobfuscation mapping file for an APK version.

    This enables human-readable crash stack traces in Play Console.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        version_code: APK version code to associate the mapping with
        file_path: Absolute path to the mapping.txt file on this machine
        deobfuscation_file_type: 'proguard' (default) or 'nativeCode'

    Returns:
        Upload result with success status
    """
    client = get_client()
    result = client.upload_deobfuscation_file(
        package_name=package_name,
        version_code=version_code,
        file_path=file_path,
        deobfuscation_file_type=deobfuscation_file_type,
    )
    return result.model_dump()

def list_bundles(package_name: str) -> list[dict[str, Any]]:
    """List all AAB bundles uploaded for an app.

    Args:
        package_name: App package name (e.g., com.example.myapp)

    Returns:
        List of bundle info with version_code, sha1, sha256
    """
    client = get_client()
    bundles = client.list_bundles(package_name)
    return [b.model_dump() for b in bundles]

def list_generated_apks(
    package_name: str,
    bundle_version_code: int,
) -> list[dict[str, Any]]:
    """List APKs generated from a specific AAB bundle.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        bundle_version_code: Version code of the bundle to query

    Returns:
        List of generated APK info with download_id, variant_id, sdk versions
    """
    client = get_client()
    apks = client.list_generated_apks(
        package_name=package_name,
        bundle_version_code=bundle_version_code,
    )
    return [a.model_dump() for a in apks]

def validate_package_name(package_name: str) -> dict[str, Any]:
    """Validate package name format before using it in other operations.

    Args:
        package_name: Package name to validate (e.g., com.example.myapp)

    Returns:
        Validation result with any errors found
    """
    client = get_client()

    errors = client.validate_package_name(package_name)
    return {
        "valid": len(errors) == 0,
        "errors": [error.model_dump() for error in errors],
        "package_name": package_name,
    }

def validate_track(track: str) -> dict[str, Any]:
    """Validate track name before using it in deployment operations.

    Args:
        track: Track name to validate (internal, alpha, beta, production)

    Returns:
        Validation result with any errors found
    """
    client = get_client()

    errors = client.validate_track(track)
    return {
        "valid": len(errors) == 0,
        "errors": [error.model_dump() for error in errors],
        "track": track,
    }

def validate_listing_text(
    title: str | None = None,
    short_description: str | None = None,
    full_description: str | None = None,
) -> dict[str, Any]:
    """Validate store listing text lengths before updating.

    Args:
        title: App title (max 30 characters)
        short_description: Short description (max 80 characters)
        full_description: Full description (max 4000 characters)

    Returns:
        Validation result with any errors found
    """
    client = get_client()

    errors = client.validate_listing_text(title, short_description, full_description)
    return {
        "valid": len(errors) == 0,
        "errors": [error.model_dump() for error in errors],
    }

def batch_deploy(
    package_name: str,
    file_path: str,
    tracks: list[str],
    release_notes: str | None = None,
    rollout_percentages: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Deploy an app to multiple tracks in a single operation.

    This is useful for deploying to internal and alpha tracks simultaneously,
    or for promoting to multiple testing tracks at once.

    Args:
        package_name: App package name
        file_path: Absolute path to APK or AAB file
        tracks: List of tracks to deploy to (e.g., ["internal", "alpha"])
        release_notes: Optional release notes for all tracks
        rollout_percentages: Optional dict mapping track names to rollout percentages

    Returns:
        Batch deployment result with individual results for each track
    """
    client = get_client()

    result = client.batch_deploy(
        package_name=package_name,
        file_path=file_path,
        tracks=tracks,
        release_notes=release_notes,
        rollout_percentages=rollout_percentages,
    )
    return result.model_dump()

def list_images(
    package_name: str,
    language: str,
    image_type: str,
) -> list[dict[str, Any]]:
    """List store listing images for a given language and image type.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        language: Language code (e.g., en-US)
        image_type: One of: phoneScreenshots, sevenInchScreenshots,
                    tenInchScreenshots, tvScreenshots, wearScreenshots,
                    icon, featureGraphic, tvBanner, promoGraphic

    Returns:
        List of images with id, url, sha1, sha256
    """
    client = get_client()
    results = client.list_images(package_name=package_name, language=language, image_type=image_type)
    return [r.model_dump() for r in results]

def upload_image(
    package_name: str,
    language: str,
    image_type: str,
    file_path: str,
) -> dict[str, Any]:
    """Upload a store listing image (screenshot, icon, feature graphic, etc.).

    Args:
        package_name: App package name (e.g., com.example.myapp)
        language: Language code (e.g., en-US)
        image_type: One of: phoneScreenshots, sevenInchScreenshots,
                    tenInchScreenshots, tvScreenshots, wearScreenshots,
                    icon, featureGraphic, tvBanner, promoGraphic
        file_path: Absolute path to the image file (PNG or JPEG)

    Returns:
        Upload result with success status and image ID
    """
    client = get_client()
    result = client.upload_image(
        package_name=package_name,
        language=language,
        image_type=image_type,
        file_path=file_path,
    )
    return result.model_dump()

def delete_image(
    package_name: str,
    language: str,
    image_type: str,
    image_id: str,
) -> dict[str, Any]:
    """Delete a specific store listing image by ID.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        language: Language code (e.g., en-US)
        image_type: Image type (e.g., phoneScreenshots, icon)
        image_id: ID of the image to delete (from list_images)

    Returns:
        Delete result with success status
    """
    client = get_client()
    result = client.delete_image(
        package_name=package_name,
        language=language,
        image_type=image_type,
        image_id=image_id,
    )
    return result.model_dump()

def delete_all_images(
    package_name: str,
    language: str,
    image_type: str,
) -> dict[str, Any]:
    """Delete all store listing images of a given type and language.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        language: Language code (e.g., en-US)
        image_type: Image type to clear (e.g., phoneScreenshots, featureGraphic)

    Returns:
        Delete result with success status
    """
    client = get_client()
    result = client.delete_all_images(
        package_name=package_name,
        language=language,
        image_type=image_type,
    )
    return result.model_dump()

def get_app_details_info(package_name: str) -> dict[str, Any]:
    """Get app details including default language and developer contact info.

    Args:
        package_name: App package name (e.g., com.example.myapp)

    Returns:
        App details with defaultLanguage, contactEmail, contactPhone, contactWebsite
    """
    client = get_client()
    result = client.get_app_details_info(package_name=package_name)
    return result.model_dump()

def update_app_details_info(
    package_name: str,
    default_language: str | None = None,
    contact_email: str | None = None,
    contact_phone: str | None = None,
    contact_website: str | None = None,
) -> dict[str, Any]:
    """Update app details such as default language and developer contact info.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        default_language: Default language code (e.g., en-US, zh-CN)
        contact_email: Developer contact email shown on Play Store
        contact_phone: Developer contact phone shown on Play Store
        contact_website: Developer contact website shown on Play Store

    Returns:
        Update result with success status
    """
    client = get_client()
    result = client.update_app_details_info(
        package_name=package_name,
        default_language=default_language,
        contact_email=contact_email,
        contact_phone=contact_phone,
        contact_website=contact_website,
    )
    return result.model_dump()

def get_country_availability(
    package_name: str,
    track: str,
) -> dict[str, Any]:
    """Get the list of countries where a release track is available.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        track: Release track - one of: internal, alpha, beta, production

    Returns:
        Country availability with list of country codes and rest_of_world flag
    """
    client = get_client()
    result = client.get_country_availability(package_name=package_name, track=track)
    return result.model_dump()

def list_users(developer_id: str) -> list[dict[str, Any]]:
    """List all users in a Google Play developer account.

    Args:
        developer_id: Developer account ID (numeric ID from Play Console URL,
                      e.g., the number after 'developers/' in console.play.google.com)

    Returns:
        List of users with email, access_state, and app-level grants
    """
    client = get_client()
    results = client.list_users(developer_id=developer_id)
    return [r.model_dump() for r in results]

def create_user(
    developer_id: str,
    email: str,
    access_state: str = "accessGranted",
) -> dict[str, Any]:
    """Add a user to the developer account.

    Args:
        developer_id: Developer account ID (numeric ID from Play Console URL)
        email: User's Google account email address
        access_state: Account-level access. One of:
                      accessGranted (default), accessExpired, accessRevoked

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.create_user(
        developer_id=developer_id,
        email=email,
        access_state=access_state,
    )
    return result.model_dump()

def delete_user(
    developer_id: str,
    email: str,
) -> dict[str, Any]:
    """Remove a user from the developer account.

    Args:
        developer_id: Developer account ID (numeric ID from Play Console URL)
        email: User's Google account email address to remove

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.delete_user(developer_id=developer_id, email=email)
    return result.model_dump()

def create_grant(
    developer_id: str,
    email: str,
    package_name: str,
    app_level_permissions: list[str],
) -> dict[str, Any]:
    """Grant a user app-level permissions on a specific app.

    Args:
        developer_id: Developer account ID (numeric ID from Play Console URL)
        email: User's Google account email address
        package_name: App package name to grant access to
        app_level_permissions: List of permissions. Valid values:
                               canAccessStats, canManageProductionRelease,
                               canManageTestTracks, canManageStorePresence,
                               canReplyToReviews

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.create_grant(
        developer_id=developer_id,
        email=email,
        package_name=package_name,
        app_level_permissions=app_level_permissions,
    )
    return result.model_dump()

def delete_grant(
    developer_id: str,
    email: str,
    package_name: str,
) -> dict[str, Any]:
    """Revoke a user's app-level permissions on a specific app.

    Args:
        developer_id: Developer account ID (numeric ID from Play Console URL)
        email: User's Google account email address
        package_name: App package name to revoke access from

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.delete_grant(
        developer_id=developer_id,
        email=email,
        package_name=package_name,
    )
    return result.model_dump()

def refund_order(
    package_name: str,
    order_id: str,
    revoke: bool = False,
) -> dict[str, Any]:
    """Refund a user's order (purchase or subscription).

    Args:
        package_name: App package name (e.g., com.example.myapp)
        order_id: The order ID to refund
        revoke: If True, also revokes the user's entitlement to the item.
                Use with caution - this removes access immediately.

    Returns:
        Refund result with success status
    """
    client = get_client()
    result = client.refund_order(
        package_name=package_name,
        order_id=order_id,
        revoke=revoke,
    )
    return result.model_dump()

def get_product_purchase(
    package_name: str,
    product_id: str,
    token: str,
) -> dict[str, Any]:
    """Get the status of a one-time in-app product purchase.

    Use this to verify if a purchase is valid, consumed, or acknowledged.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        product_id: The product SKU (in-app product ID)
        token: The purchase token returned from the client-side purchase

    Returns:
        Purchase status including purchase_state (0=purchased, 1=canceled, 2=pending),
        consumption_state, acknowledged, order_id
    """
    client = get_client()
    result = client.get_product_purchase(
        package_name=package_name,
        product_id=product_id,
        token=token,
    )
    return result.model_dump()

def acknowledge_product_purchase(
    package_name: str,
    product_id: str,
    token: str,
    developer_payload: str = "",
) -> dict[str, Any]:
    """Acknowledge a one-time in-app product purchase.

    Must be called within 3 days of purchase, otherwise the purchase is automatically refunded.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        product_id: The product SKU (in-app product ID)
        token: The purchase token returned from the client-side purchase
        developer_payload: Optional string to attach to the purchase (max 100 chars)

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.acknowledge_product_purchase(
        package_name=package_name,
        product_id=product_id,
        token=token,
        developer_payload=developer_payload,
    )
    return result.model_dump()

def consume_product_purchase(
    package_name: str,
    product_id: str,
    token: str,
) -> dict[str, Any]:
    """Consume a one-time in-app product purchase.

    Use this after granting entitlement for consumable products so the user can
    buy the same product again.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        product_id: The product SKU (in-app product ID)
        token: The purchase token returned from the client-side purchase

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.consume_product_purchase(
        package_name=package_name,
        product_id=product_id,
        token=token,
    )
    return result.model_dump()

def get_subscription_purchase_v2(
    package_name: str,
    token: str,
) -> dict[str, Any]:
    """Get subscription purchase details using the latest v2 API.

    Provides richer subscription state information than the v1 API.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        token: The purchase token returned from the client-side subscription

    Returns:
        Subscription state including subscription_state, expiry_time,
        product_id, base_plan_id, offer_id, auto_renewing
    """
    client = get_client()
    result = client.get_subscription_purchase_v2(
        package_name=package_name,
        token=token,
    )
    return result.model_dump()

def cancel_subscription_v2(
    package_name: str,
    token: str,
) -> dict[str, Any]:
    """Cancel a subscription (user keeps access until end of billing period).

    Args:
        package_name: App package name (e.g., com.example.myapp)
        token: The purchase token of the subscription to cancel

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.cancel_subscription_v2(package_name=package_name, token=token)
    return result.model_dump()

def revoke_subscription_v2(
    package_name: str,
    token: str,
) -> dict[str, Any]:
    """Revoke a subscription (cancels AND immediately expires access).

    Use for fraud, abuse, or policy violations. The user loses access immediately.

    Args:
        package_name: App package name (e.g., com.example.myapp)
        token: The purchase token of the subscription to revoke

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.revoke_subscription_v2(package_name=package_name, token=token)
    return result.model_dump()

def get_crash_rate(
    package_name: str,
    start_date: str,
    end_date: str,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    f"""Query daily crash rate from Play Developer Reporting API.

    Returns crashRate, crashRate7dUserWeighted, crashRate28dUserWeighted, userPerceivedCrashRate, and distinctUsers
    per day over the requested period.

    {_VITALS_PERMISSION_NOTE}

    Args:
        package_name: App package name (e.g., com.example.myapp)
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format (exclusive — use tomorrow's date to include today)
        dimensions: {_VITALS_DIMENSIONS_HELP}

    Returns:
        VitalsQueryResult with list of data_points containing date, metrics, dimensions
    """
    client = get_client()
    result = client.get_crash_rate(
        package_name=package_name,
        start_date=start_date,
        end_date=end_date,
        dimensions=dimensions,
    )
    return result.model_dump()

def get_anr_rate(
    package_name: str,
    start_date: str,
    end_date: str,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    f"""Query daily ANR (Application Not Responding) rate.

    Returns anrRate (ratio of sessions with ANR), anrCount, and distinctUsers per day.

    {_VITALS_PERMISSION_NOTE}

    Args:
        package_name: App package name (e.g., com.example.myapp)
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format (exclusive)
        dimensions: {_VITALS_DIMENSIONS_HELP}

    Returns:
        VitalsQueryResult with list of data_points
    """
    client = get_client()
    result = client.get_anr_rate(
        package_name=package_name,
        start_date=start_date,
        end_date=end_date,
        dimensions=dimensions,
    )
    return result.model_dump()

def get_slow_startup_rate(
    package_name: str,
    start_date: str,
    end_date: str,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    f"""Query daily slow startup rate.

    Returns slowStartupRate (ratio of slow cold starts), slowStartupCount, distinctUsers.
    A cold start is considered slow if it takes > 5 seconds.

    {_VITALS_PERMISSION_NOTE}

    Args:
        package_name: App package name
        start_date: Start date YYYY-MM-DD
        end_date: End date YYYY-MM-DD (exclusive)
        dimensions: {_VITALS_DIMENSIONS_HELP}

    Returns:
        VitalsQueryResult with list of data_points
    """
    client = get_client()
    result = client.get_slow_startup_rate(
        package_name=package_name,
        start_date=start_date,
        end_date=end_date,
        dimensions=dimensions,
    )
    return result.model_dump()

def get_slow_rendering_rate(
    package_name: str,
    start_date: str,
    end_date: str,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    f"""Query daily slow rendering (frame drop) rate. GAMES ONLY.

    This metric is only accessible for apps categorized as games in Play Console.
    Non-game apps will receive a 403 error.

    Returns slowRenderingRate20Fps (frames rendered below 20fps) and
    slowRenderingRate30Fps (frames rendered below 30fps), plus distinctUsers.

    {_VITALS_PERMISSION_NOTE}

    Args:
        package_name: App package name
        start_date: Start date YYYY-MM-DD
        end_date: End date YYYY-MM-DD (exclusive)
        dimensions: {_VITALS_DIMENSIONS_HELP}

    Returns:
        VitalsQueryResult with list of data_points
    """
    client = get_client()
    result = client.get_slow_rendering_rate(
        package_name=package_name,
        start_date=start_date,
        end_date=end_date,
        dimensions=dimensions,
    )
    return result.model_dump()

def get_excessive_wakeup_rate(
    package_name: str,
    start_date: str,
    end_date: str,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    f"""Query daily excessive wakeup rate.

    Returns excessiveWakeupRate (ratio of hours with > 10 wakeups/hour),
    excessiveWakeupCount, and distinctUsers.

    {_VITALS_PERMISSION_NOTE}

    Args:
        package_name: App package name
        start_date: Start date YYYY-MM-DD
        end_date: End date YYYY-MM-DD (exclusive)
        dimensions: {_VITALS_DIMENSIONS_HELP}

    Returns:
        VitalsQueryResult with list of data_points
    """
    client = get_client()
    result = client.get_excessive_wakeup_rate(
        package_name=package_name,
        start_date=start_date,
        end_date=end_date,
        dimensions=dimensions,
    )
    return result.model_dump()

def get_stuck_wakelock_rate(
    package_name: str,
    start_date: str,
    end_date: str,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    f"""Query daily stuck background wakelock rate.

    Returns stuckBgWakelockRate (ratio of hours where app holds wakelock > 1 hour),
    stuckBgWakelockCount, and distinctUsers.

    {_VITALS_PERMISSION_NOTE}

    Args:
        package_name: App package name
        start_date: Start date YYYY-MM-DD
        end_date: End date YYYY-MM-DD (exclusive)
        dimensions: {_VITALS_DIMENSIONS_HELP}

    Returns:
        VitalsQueryResult with list of data_points
    """
    client = get_client()
    result = client.get_stuck_wakelock_rate(
        package_name=package_name,
        start_date=start_date,
        end_date=end_date,
        dimensions=dimensions,
    )
    return result.model_dump()

def get_lmk_rate(
    package_name: str,
    start_date: str,
    end_date: str,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    f"""Query daily Low Memory Killer (LMK) rate.

    LMK rate measures how often the system kills the app due to memory pressure.
    High values indicate the app consumes too much memory.

    {_VITALS_PERMISSION_NOTE}

    Args:
        package_name: App package name
        start_date: Start date YYYY-MM-DD
        end_date: End date YYYY-MM-DD (exclusive)
        dimensions: {_VITALS_DIMENSIONS_HELP}

    Returns:
        VitalsQueryResult with lmkRate, lmkCount, distinctUsers per day
    """
    client = get_client()
    result = client.get_lmk_rate(
        package_name=package_name,
        start_date=start_date,
        end_date=end_date,
        dimensions=dimensions,
    )
    return result.model_dump()

def list_vitals_anomalies(
    package_name: str,
    filter_str: str | None = None,
) -> list[dict[str, Any]]:
    f"""List automatically detected vitals anomalies for an app.

    Anomalies are significant degradations in any vitals metric automatically
    detected by Google Play. Useful for catching regressions.

    {_VITALS_PERMISSION_NOTE}

    Args:
        package_name: App package name
        filter_str: Optional filter, e.g. "metric = crashRate" or "metric = anrRate"

    Returns:
        List of anomalies with metric_set, dimensions, first_detection_time, last_detected_day
    """
    client = get_client()
    anomalies = client.list_vitals_anomalies(
        package_name=package_name,
        filter_str=filter_str,
    )
    return [a.model_dump() for a in anomalies]

def get_install_stats(
    package_name: str,
    developer_id: str,
    app_id: str,
    start_date: str,
    end_date: str,
    country_codes: list[str] | None = None,
) -> dict[str, Any]:
    """Get install statistics from Play Console via browser session (OpenCLI).

    Returns daily install events, net installs, and active users. Optionally
    breaks down install events by country.

    REQUIREMENT: OpenCLI must be installed and the automation browser must be
    logged into Play Console (play.google.com/console).

    Find developer_id and app_id in the Play Console URL:
    https://play.google.com/console/u/0/developers/{developer_id}/app/{app_id}/statistics

    Args:
        package_name: App package name (e.g. com.example.app)
        developer_id: Numeric developer account ID from Play Console URL
        app_id: Numeric app ID from Play Console URL
        start_date: Start date YYYY-MM-DD
        end_date: End date YYYY-MM-DD
        country_codes: Optional list of country codes for per-country breakdown (e.g. ["US", "GB"])

    Returns:
        install_events: Total installs including re-installs with daily breakdown
        net_installs: Net installs (installs minus uninstalls)
        active_users: Unique active users
        by_country: Per-country install events (if country_codes provided)
    """
    client = get_client()
    result = client.get_install_stats(
        package_name=package_name,
        developer_id=developer_id,
        app_id=app_id,
        start_date=start_date,
        end_date=end_date,
        country_codes=country_codes,
    )
    return result.model_dump()

def get_search_terms(
    package_name: str,
    developer_id: str,
    app_id: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Get top search terms driving installs from Play Console via browser session (OpenCLI).

    Returns the search terms that brought users to the store listing, sorted by installs.

    REQUIREMENT: OpenCLI must be installed and the automation browser must be
    logged into Play Console (play.google.com/console).

    Find developer_id and app_id in the Play Console URL:
    https://play.google.com/console/u/0/developers/{developer_id}/app/{app_id}/statistics

    Args:
        package_name: App package name (e.g. com.example.app)
        developer_id: Numeric developer account ID from Play Console URL
        app_id: Numeric app ID from Play Console URL
        start_date: Start date YYYY-MM-DD
        end_date: End date YYYY-MM-DD

    Returns:
        terms: List of search terms with installs and store_listing_visitors, sorted by installs desc
    """
    client = get_client()
    result = client.get_search_terms(
        package_name=package_name,
        developer_id=developer_id,
        app_id=app_id,
        start_date=start_date,
        end_date=end_date,
    )
    return result.model_dump()

def get_acquisition_funnel(
    package_name: str,
    developer_id: str,
    app_id: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Get user acquisition funnel from Play Console via browser session (OpenCLI).

    Returns the conversion funnel: impressions → store listing visitors → installers → buyers.

    REQUIREMENT: OpenCLI must be installed and the automation browser must be
    logged into Play Console (play.google.com/console).

    Find developer_id and app_id in the Play Console URL:
    https://play.google.com/console/u/0/developers/{developer_id}/app/{app_id}/grow-overview

    Args:
        package_name: App package name (e.g. com.example.app)
        developer_id: Numeric developer account ID from Play Console URL
        app_id: Numeric app ID from Play Console URL
        start_date: Start date YYYY-MM-DD
        end_date: End date YYYY-MM-DD

    Returns:
        stages: Funnel stages with value and conversion_rate relative to previous stage
    """
    client = get_client()
    result = client.get_acquisition_funnel(
        package_name=package_name,
        developer_id=developer_id,
        app_id=app_id,
        start_date=start_date,
        end_date=end_date,
    )
    return result.model_dump()

def get_custom_store_listings(
    package_name: str,
    developer_id: str,
    app_id: str,
) -> dict[str, Any]:
    """List Custom Store Listings (CSL) for an app via Play Console UI scrape (OpenCLI).

    CSL configuration has NO Android Publisher API surface at all -- confirmed by
    cross-checking the full REST resource index (only v3.edits.listings exists for
    the Main store listing; nothing for custom listings). This reads the Console UI
    directly via OpenCLI browser automation instead.

    REQUIREMENT: OpenCLI must be installed and the automation browser must be
    logged into Play Console (play.google.com/console).

    LIMITATION: returns each CSL's internal name and edit URL only. Per-CSL content
    (title, short/full description, targeting rules) requires opening each edit URL
    individually and was not reliably scrapable via simple selectors as of 2026-07-01
    (Angular Material form fields) -- not yet implemented.

    Find developer_id and app_id in the Play Console URL:
    https://play.google.com/console/u/0/developers/{developer_id}/app/{app_id}/store-listings

    Args:
        package_name: App package name (e.g. com.example.app)
        developer_id: Numeric developer account ID from Play Console URL
        app_id: Numeric app ID from Play Console URL

    Returns:
        listings: List of {name, listing_id, edit_url} for each CSL
    """
    client = get_client()
    result = client.get_custom_store_listings(
        package_name=package_name,
        developer_id=developer_id,
        app_id=app_id,
    )
    return result.model_dump()

def get_store_listing_experiments(
    package_name: str,
    developer_id: str,
    app_id: str,
) -> dict[str, Any]:
    """List Store Listing Experiments (A/B tests) for an app via Play Console RPC capture (OpenCLI).

    Store Listing Experiments have NO Android Publisher API surface at all -- same
    verification as get_custom_store_listings. This decodes an internal, undocumented
    protobuf response captured via OpenCLI network capture. There is no public schema
    for this response, so field semantics beyond experiment_id/name/locale are INFERRED
    from one real reverse-engineered response, not confirmed against an official spec --
    treat dimension_type/status/traffic_split as best-effort, verify against the Console
    UI for anything decision-critical.

    REQUIREMENT: OpenCLI must be installed and the automation browser must be
    logged into Play Console (play.google.com/console).

    Find developer_id and app_id in the Play Console URL:
    https://play.google.com/console/u/0/developers/{developer_id}/app/{app_id}/store-listing-experiments/overview

    Args:
        package_name: App package name (e.g. com.example.app)
        developer_id: Numeric developer account ID from Play Console URL
        app_id: Numeric app ID from Play Console URL

    Returns:
        experiments: List of experiments with experiment_id, name, locale, and
                     best-effort dimension_type/status/start_timestamp/traffic_split
    """
    client = get_client()
    result = client.get_store_listing_experiments(
        package_name=package_name,
        developer_id=developer_id,
        app_id=app_id,
    )
    return result.model_dump()

def get_experiment_report_raw(
    package_name: str,
    developer_id: str,
    app_id: str,
    experiment_id: str,
) -> dict[str, Any]:
    """Fetch raw decodable content of one Store Listing Experiment's report (OpenCLI).

    Companion to get_store_listing_experiments. Does not attempt to parse performance
    metrics (installs/conversion lift per variant) -- no official schema is available
    for that part of the response. Surfaces creative image URLs and readable text runs
    found in the payload, which is enough to confirm what's actually being compared
    (e.g. which screenshot set is the control vs the variant) without guessing at
    metric field numbers.

    REQUIREMENT: OpenCLI must be installed and the automation browser must be
    logged into Play Console (play.google.com/console).

    Args:
        package_name: App package name (e.g. com.example.app)
        developer_id: Numeric developer account ID from Play Console URL
        app_id: Numeric app ID from Play Console URL
        experiment_id: Numeric experiment id (from get_store_listing_experiments)

    Returns:
        image_urls: Creative image URLs found in the report payload
        text_strings: Readable text runs found in the report payload (longest first)
    """
    client = get_client()
    result = client.get_experiment_report_raw(
        package_name=package_name,
        developer_id=developer_id,
        app_id=app_id,
        experiment_id=experiment_id,
    )
    return result.model_dump()

def create_in_app_product(
    package_name: str,
    sku: str,
    product_type: str,
    default_language: str,
    title: str,
    description: str,
    default_price_amount: str,
    default_price_currency: str,
) -> dict[str, Any]:
    """Create a new in-app product (one-time purchase).

    Args:
        package_name: App package name
        sku: Unique product SKU identifier (e.g. "coins_100")
        product_type: "managedProduct" (one-time) or "subscription" (legacy)
        default_language: Default locale code (e.g. "en-US")
        title: Product title shown to users
        description: Product description shown to users
        default_price_amount: Price as decimal string (e.g. "0.99")
        default_price_currency: ISO 4217 currency code (e.g. "USD")

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.create_in_app_product(
        package_name=package_name,
        sku=sku,
        product_type=product_type,
        default_language=default_language,
        title=title,
        description=description,
        default_price_amount=default_price_amount,
        default_price_currency=default_price_currency,
    )
    return result.model_dump()

def update_in_app_product(
    package_name: str,
    sku: str,
    title: str | None = None,
    description: str | None = None,
    default_price_amount: str | None = None,
    default_price_currency: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Update an existing in-app product.

    Args:
        package_name: App package name
        sku: Product SKU identifier
        title: New product title (optional)
        description: New product description (optional)
        default_price_amount: New price as decimal string (optional, e.g. "1.99")
        default_price_currency: Currency code if changing price (optional)
        status: "active" or "inactive" (optional)

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.update_in_app_product(
        package_name=package_name,
        sku=sku,
        title=title,
        description=description,
        default_price_amount=default_price_amount,
        default_price_currency=default_price_currency,
        status=status,
    )
    return result.model_dump()

def delete_in_app_product(
    package_name: str,
    sku: str,
) -> dict[str, Any]:
    """Delete an in-app product permanently.

    Args:
        package_name: App package name
        sku: Product SKU to delete

    Returns:
        Operation result with success status
    """
    client = get_client()
    return client.delete_in_app_product(package_name=package_name, sku=sku)

def get_subscription(
    package_name: str,
    product_id: str,
) -> dict[str, Any]:
    """Get details of a subscription product from the Monetization API.

    Args:
        package_name: App package name
        product_id: Subscription product ID

    Returns:
        Subscription details including state, listings, and base plans
    """
    client = get_client()
    result = client.get_subscription(package_name=package_name, product_id=product_id)
    return result.model_dump()

def create_subscription(
    package_name: str,
    product_id: str,
    default_language: str,
    title: str,
    description: str,
) -> dict[str, Any]:
    """Create a new subscription product via the Monetization API.

    Args:
        package_name: App package name
        product_id: Unique subscription product ID (e.g. "premium_monthly")
        default_language: Default locale code (e.g. "en-US")
        title: Subscription title shown to users
        description: Subscription description shown to users

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.create_subscription(
        package_name=package_name,
        product_id=product_id,
        default_language=default_language,
        title=title,
        description=description,
    )
    return result.model_dump()

def update_subscription(
    package_name: str,
    product_id: str,
    title: str | None = None,
    description: str | None = None,
    default_language: str = "en-US",
) -> dict[str, Any]:
    """Update an existing subscription product's listings.

    Args:
        package_name: App package name
        product_id: Subscription product ID
        title: New title (optional)
        description: New description (optional)
        default_language: Locale to update (default "en-US")

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.update_subscription(
        package_name=package_name,
        product_id=product_id,
        title=title,
        description=description,
        default_language=default_language,
    )
    return result.model_dump()

def delete_subscription(
    package_name: str,
    product_id: str,
) -> dict[str, Any]:
    """Delete a subscription product permanently.

    Args:
        package_name: App package name
        product_id: Subscription product ID to delete

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.delete_subscription(package_name=package_name, product_id=product_id)
    return result.model_dump()

def activate_base_plan(
    package_name: str,
    product_id: str,
    base_plan_id: str,
) -> dict[str, Any]:
    """Activate a base plan for a subscription product.

    Args:
        package_name: App package name
        product_id: Subscription product ID
        base_plan_id: Base plan ID to activate

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.activate_base_plan(
        package_name=package_name, product_id=product_id, base_plan_id=base_plan_id
    )
    return result.model_dump()

def deactivate_base_plan(
    package_name: str,
    product_id: str,
    base_plan_id: str,
) -> dict[str, Any]:
    """Deactivate a base plan for a subscription product.

    Args:
        package_name: App package name
        product_id: Subscription product ID
        base_plan_id: Base plan ID to deactivate

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.deactivate_base_plan(
        package_name=package_name, product_id=product_id, base_plan_id=base_plan_id
    )
    return result.model_dump()

def update_country_availability(
    package_name: str,
    track: str,
    countries: list[str],
    rest_of_world: bool = False,
) -> dict[str, Any]:
    """Set the countries where a release track is available.

    Args:
        package_name: App package name
        track: Track name (internal, alpha, beta, production)
        countries: List of ISO 3166-1 alpha-2 country codes (e.g. ["US", "GB", "JP"])
        rest_of_world: If True, also available in all countries not explicitly listed

    Returns:
        Update result with the countries set and success status
    """
    client = get_client()
    result = client.update_country_availability(
        package_name=package_name,
        track=track,
        countries=countries,
        rest_of_world=rest_of_world,
    )
    return result.model_dump()

def update_user(
    developer_id: str,
    email: str,
    access_state: str,
) -> dict[str, Any]:
    """Update a user's account-level access state.

    Args:
        developer_id: Developer account ID (numeric ID from Play Console URL)
        email: User's Google account email address
        access_state: New access state: accessGranted, accessExpired, or accessRevoked

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.update_user(
        developer_id=developer_id, email=email, access_state=access_state
    )
    return result.model_dump()

def update_grant(
    developer_id: str,
    email: str,
    package_name: str,
    app_level_permissions: list[str],
) -> dict[str, Any]:
    """Update a user's app-level permissions on a specific app.

    Args:
        developer_id: Developer account ID (numeric ID from Play Console URL)
        email: User's Google account email address
        package_name: App package name
        app_level_permissions: New permissions list. Valid values:
                               canAccessStats, canManageProductionRelease,
                               canManageTestTracks, canManageStorePresence,
                               canReplyToReviews

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.update_grant(
        developer_id=developer_id,
        email=email,
        package_name=package_name,
        app_level_permissions=app_level_permissions,
    )
    return result.model_dump()

def defer_subscription(
    package_name: str,
    subscription_id: str,
    purchase_token: str,
    expected_expiry_time_millis: str,
    desired_expiry_time_millis: str,
) -> dict[str, Any]:
    """Defer a subscriber's renewal date (customer service use case).

    Args:
        package_name: App package name
        subscription_id: Subscription product ID
        purchase_token: The purchase token from the client app
        expected_expiry_time_millis: Current expiry time in milliseconds (Unix epoch)
        desired_expiry_time_millis: New desired expiry time in milliseconds (Unix epoch)

    Returns:
        Result with the new expiry time in milliseconds
    """
    client = get_client()
    result = client.defer_subscription(
        package_name=package_name,
        subscription_id=subscription_id,
        token=purchase_token,
        expected_expiry_time_millis=expected_expiry_time_millis,
        desired_expiry_time_millis=desired_expiry_time_millis,
    )
    return result.model_dump()

def delete_listing(
    package_name: str,
    language: str,
) -> dict[str, Any]:
    """Delete the store listing for a specific language locale.

    Args:
        package_name: App package name
        language: Language code to delete (e.g. "fr-FR", "de-DE")

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.delete_listing(package_name=package_name, language=language)
    return result.model_dump()

def delete_all_listings(
    package_name: str,
) -> dict[str, Any]:
    """Delete all store listings for an app. Use with caution.

    Args:
        package_name: App package name

    Returns:
        Operation result with success status
    """
    client = get_client()
    result = client.delete_all_listings(package_name=package_name)
    return result.model_dump()

def convert_region_prices(
    package_name: str,
    price_amount: str,
    currency_code: str,
) -> dict[str, Any]:
    """Convert a base price to all regional equivalents using Google's exchange rates.

    Args:
        package_name: App package name
        price_amount: Base price as decimal string (e.g. "9.99")
        currency_code: ISO 4217 base currency code (e.g. "USD")

    Returns:
        List of converted prices per region with local currency and amount
    """
    client = get_client()
    result = client.convert_region_prices(
        package_name=package_name,
        price_amount=price_amount,
        currency_code=currency_code,
    )
    return result.model_dump()

