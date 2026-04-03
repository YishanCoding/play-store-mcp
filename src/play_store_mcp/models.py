"""Pydantic models for Play Store MCP Server."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic needs this at runtime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Track(StrEnum):
    """Release track options."""

    INTERNAL = "internal"
    ALPHA = "alpha"
    BETA = "beta"
    PRODUCTION = "production"


class ReleaseStatus(StrEnum):
    """Release status options."""

    DRAFT = "draft"
    IN_PROGRESS = "inProgress"
    HALTED = "halted"
    COMPLETED = "completed"


class Release(BaseModel):
    """Represents an app release on a track."""

    package_name: str = Field(..., description="App package name")
    track: str = Field(..., description="Release track")
    status: str = Field(..., description="Release status")
    version_codes: list[int] = Field(default_factory=list, description="Version codes in release")
    version_name: str | None = Field(None, description="Version name")
    rollout_percentage: float = Field(100.0, description="Rollout percentage (0-100)")
    release_notes: dict[str, str] = Field(
        default_factory=dict, description="Release notes by language"
    )


class TrackInfo(BaseModel):
    """Information about a release track."""

    track: str = Field(..., description="Track name")
    releases: list[Release] = Field(default_factory=list, description="Releases on this track")


class DeploymentResult(BaseModel):
    """Result of a deployment operation."""

    success: bool = Field(..., description="Whether deployment succeeded")
    edit_id: str | None = Field(None, description="Edit ID for the operation")
    package_name: str = Field(..., description="App package name")
    track: str = Field(..., description="Target track")
    version_code: int | None = Field(None, description="Deployed version code")
    message: str = Field(..., description="Status message")
    error: str | None = Field(None, description="Error details if failed")


class AppInfo(BaseModel):
    """Basic app information."""

    package_name: str = Field(..., description="App package name")
    title: str | None = Field(None, description="App title")
    default_language: str | None = Field(None, description="Default language")


class AppDetails(BaseModel):
    """Detailed app information."""

    package_name: str = Field(..., description="App package name")
    title: str | None = Field(None, description="App title")
    short_description: str | None = Field(None, description="Short description")
    full_description: str | None = Field(None, description="Full description")
    default_language: str | None = Field(None, description="Default language")
    developer_name: str | None = Field(None, description="Developer name")
    developer_email: str | None = Field(None, description="Developer email")
    developer_website: str | None = Field(None, description="Developer website")


class Review(BaseModel):
    """User review."""

    review_id: str = Field(..., description="Review ID")
    author_name: str = Field(..., description="Author name")
    star_rating: int = Field(..., description="Star rating (1-5)")
    comment: str = Field(..., description="Review comment")
    language: str = Field(..., description="Review language")
    device: str | None = Field(None, description="Device name")
    android_version: str | None = Field(None, description="Android OS version")
    app_version_code: int | None = Field(None, description="App version code")
    app_version_name: str | None = Field(None, description="App version name")
    last_modified: datetime | None = Field(None, description="Last modification time")
    developer_reply: str | None = Field(None, description="Developer reply if present")
    developer_reply_time: datetime | None = Field(None, description="Reply time")


class ReviewReplyResult(BaseModel):
    """Result of replying to a review."""

    success: bool = Field(..., description="Whether reply succeeded")
    review_id: str = Field(..., description="Review ID")
    message: str = Field(..., description="Status message")
    error: str | None = Field(None, description="Error details if failed")


class SubscriptionProduct(BaseModel):
    """Subscription product definition."""

    product_id: str = Field(..., description="Subscription product ID")
    package_name: str = Field(..., description="App package name")
    status: str | None = Field(None, description="Subscription status")
    base_plans: list[dict[str, Any]] = Field(
        default_factory=list, description="Base plan definitions"
    )


class SubscriptionPurchase(BaseModel):
    """Subscription purchase status."""

    package_name: str = Field(..., description="App package name")
    subscription_id: str = Field(..., description="Subscription product ID")
    purchase_token: str = Field(..., description="Purchase token")
    order_id: str | None = Field(None, description="Order ID")
    start_time: datetime | None = Field(None, description="Subscription start time")
    expiry_time: datetime | None = Field(None, description="Subscription expiry time")
    auto_renewing: bool = Field(False, description="Whether auto-renewing")
    cancel_reason: int | None = Field(None, description="Cancellation reason code")
    payment_state: int | None = Field(None, description="Payment state code")
    price_currency: str | None = Field(None, description="Price currency code")
    price_amount_micros: int | None = Field(None, description="Price amount in micros")


class VoidedPurchase(BaseModel):
    """Voided purchase record."""

    package_name: str = Field(..., description="App package name")
    purchase_token: str = Field(..., description="Original purchase token")
    order_id: str | None = Field(None, description="Order ID")
    voided_time: datetime | None = Field(None, description="Time of voiding")
    voided_reason: int | None = Field(None, description="Reason for voiding")
    voided_source: int | None = Field(None, description="Source of voiding")


class VitalsOverview(BaseModel):
    """Android Vitals overview metrics."""

    package_name: str = Field(..., description="App package name")
    crash_rate: float | None = Field(None, description="User-perceived crash rate")
    anr_rate: float | None = Field(None, description="User-perceived ANR rate")
    excessive_wakeups: float | None = Field(None, description="Excessive wakeups rate")
    stuck_wake_locks: float | None = Field(None, description="Stuck wake locks rate")
    freshness_info: str | None = Field(None, description="Data freshness information")


class VitalsMetric(BaseModel):
    """Specific vitals metric data."""

    metric_type: str = Field(..., description="Type of metric")
    value: float | None = Field(None, description="Metric value")
    benchmark: float | None = Field(None, description="Benchmark threshold")
    is_below_threshold: bool | None = Field(None, description="Whether below bad threshold")
    dimension: str | None = Field(None, description="Dimension (e.g., device, version)")
    dimension_value: str | None = Field(None, description="Dimension value")


class InAppProduct(BaseModel):
    """In-app product definition."""

    sku: str = Field(..., description="Product SKU")
    package_name: str = Field(..., description="App package name")
    product_type: str = Field(..., description="Product type (managed_product or subscription)")
    status: str | None = Field(None, description="Product status")
    default_language: str | None = Field(None, description="Default language")
    title: str | None = Field(None, description="Product title")
    description: str | None = Field(None, description="Product description")
    default_price: dict[str, Any] | None = Field(None, description="Default price information")


class Listing(BaseModel):
    """Store listing for a specific language."""

    language: str = Field(..., description="Language code (e.g., en-US)")
    title: str | None = Field(None, description="App title")
    full_description: str | None = Field(None, description="Full description")
    short_description: str | None = Field(None, description="Short description")
    video: str | None = Field(None, description="YouTube video URL")


class ListingUpdateResult(BaseModel):
    """Result of updating a store listing."""

    success: bool = Field(..., description="Whether update succeeded")
    package_name: str = Field(..., description="App package name")
    language: str = Field(..., description="Language code")
    message: str = Field(..., description="Status message")
    error: str | None = Field(None, description="Error details if failed")


class TesterInfo(BaseModel):
    """Information about testers for a track."""

    track: str = Field(..., description="Track name")
    tester_emails: list[str] = Field(
        default_factory=list, description="List of tester email addresses"
    )


class Order(BaseModel):
    """Order/transaction information."""

    order_id: str = Field(..., description="Order ID")
    package_name: str = Field(..., description="App package name")
    product_id: str | None = Field(None, description="Product ID")
    purchase_time: datetime | None = Field(None, description="Purchase timestamp")
    purchase_state: int | None = Field(None, description="Purchase state")
    purchase_token: str | None = Field(None, description="Purchase token")
    quantity: int | None = Field(None, description="Quantity purchased")


class ExpansionFile(BaseModel):
    """APK expansion file information."""

    version_code: int = Field(..., description="Version code")
    expansion_file_type: str = Field(..., description="Expansion file type (main or patch)")
    file_size: int | None = Field(None, description="File size in bytes")
    references_version: int | None = Field(None, description="Referenced version code")


class BatchDeploymentResult(BaseModel):
    """Result of batch deployment to multiple tracks."""

    success: bool = Field(..., description="Whether all deployments succeeded")
    results: list[DeploymentResult] = Field(
        default_factory=list, description="Individual deployment results"
    )
    successful_count: int = Field(0, description="Number of successful deployments")
    failed_count: int = Field(0, description="Number of failed deployments")
    message: str = Field(..., description="Overall status message")


class ValidationError(BaseModel):
    """Validation error details."""

    field: str = Field(..., description="Field that failed validation")
    message: str = Field(..., description="Error message")
    value: Any | None = Field(None, description="Invalid value")


# =============================================================================
# Images API Models
# =============================================================================


class ImageInfo(BaseModel):
    """Store listing image information."""

    image_id: str = Field(..., description="Image ID")
    url: str = Field(..., description="Image URL")
    sha1: str | None = Field(None, description="SHA1 hash of image")
    sha256: str | None = Field(None, description="SHA256 hash of image")


class ImageUploadResult(BaseModel):
    """Result of an image upload or delete operation."""

    success: bool = Field(..., description="Whether operation succeeded")
    package_name: str = Field(..., description="App package name")
    language: str = Field(..., description="Language code")
    image_type: str = Field(..., description="Image type")
    image_id: str | None = Field(None, description="Image ID (for upload)")
    message: str = Field(..., description="Status message")
    error: str | None = Field(None, description="Error details if failed")


# =============================================================================
# App Details Models
# =============================================================================


class AppDetailsInfo(BaseModel):
    """App details from edits.details API."""

    package_name: str = Field(..., description="App package name")
    default_language: str | None = Field(None, description="Default language code")
    contact_email: str | None = Field(None, description="Developer contact email")
    contact_phone: str | None = Field(None, description="Developer contact phone")
    contact_website: str | None = Field(None, description="Developer contact website")


class AppDetailsUpdateResult(BaseModel):
    """Result of updating app details."""

    success: bool = Field(..., description="Whether update succeeded")
    package_name: str = Field(..., description="App package name")
    message: str = Field(..., description="Status message")
    error: str | None = Field(None, description="Error details if failed")


# =============================================================================
# Country Availability Models
# =============================================================================


class CountryAvailability(BaseModel):
    """Country availability for a release track."""

    package_name: str = Field(..., description="App package name")
    track: str = Field(..., description="Release track")
    countries: list[str] = Field(default_factory=list, description="List of country codes")
    rest_of_world: bool = Field(False, description="Whether available in rest of world")


# =============================================================================
# Users & Grants Models
# =============================================================================


class GrantInfo(BaseModel):
    """App-level grant for a user."""

    package_name: str = Field(..., description="App package name")
    app_level_permissions: list[str] = Field(
        default_factory=list, description="App-level permissions granted"
    )


class UserInfo(BaseModel):
    """Developer account user information."""

    name: str | None = Field(None, description="Resource name of user")
    email: str = Field(..., description="User email address")
    access_state: str | None = Field(None, description="Account-level access state")
    grants: list[GrantInfo] = Field(default_factory=list, description="App-level grants")


class UserOperationResult(BaseModel):
    """Result of a user or grant operation."""

    success: bool = Field(..., description="Whether operation succeeded")
    email: str = Field(..., description="User email address")
    message: str = Field(..., description="Status message")
    error: str | None = Field(None, description="Error details if failed")


# =============================================================================
# Orders & Purchases Models
# =============================================================================


class RefundResult(BaseModel):
    """Result of an order refund operation."""

    success: bool = Field(..., description="Whether refund succeeded")
    order_id: str = Field(..., description="Order ID")
    package_name: str = Field(..., description="App package name")
    revoked: bool = Field(False, description="Whether entitlement was revoked")
    message: str = Field(..., description="Status message")
    error: str | None = Field(None, description="Error details if failed")


class ProductPurchase(BaseModel):
    """One-time in-app product purchase status."""

    package_name: str = Field(..., description="App package name")
    product_id: str = Field(..., description="Product ID")
    purchase_token: str = Field(..., description="Purchase token")
    purchase_time: datetime | None = Field(None, description="Purchase time")
    purchase_state: int | None = Field(None, description="0=purchased, 1=canceled, 2=pending")
    consumption_state: int | None = Field(None, description="0=not consumed, 1=consumed")
    developer_payload: str | None = Field(None, description="Developer payload")
    order_id: str | None = Field(None, description="Order ID")
    acknowledged: bool = Field(False, description="Whether purchase was acknowledged")
    quantity: int | None = Field(None, description="Purchase quantity")


class SubscriptionPurchaseV2(BaseModel):
    """Subscription purchase status (v2 API)."""

    package_name: str = Field(..., description="App package name")
    purchase_token: str = Field(..., description="Purchase token")
    subscription_state: str | None = Field(None, description="Subscription state")
    latest_order_id: str | None = Field(None, description="Latest order ID")
    start_time: datetime | None = Field(None, description="Subscription start time")
    expiry_time: datetime | None = Field(None, description="Current period expiry time")
    auto_renewing: bool = Field(False, description="Whether auto-renewing")
    product_id: str | None = Field(None, description="Subscription product ID")
    base_plan_id: str | None = Field(None, description="Base plan ID")
    offer_id: str | None = Field(None, description="Offer ID if applicable")
