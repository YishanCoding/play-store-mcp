"""Play Store MCP Server - Main server implementation."""

from __future__ import annotations

import argparse
import base64
import binascii
import functools
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from play_store_mcp import tools as _tools
from play_store_mcp.client import PlayStoreClient, PlayStoreClientError

# Configure structured logging to stderr (stdout is reserved for MCP JSON-RPC)
log_level = os.environ.get("PLAY_STORE_MCP_LOG_LEVEL", "INFO")
numeric_level = getattr(logging, log_level.upper(), logging.INFO)
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
)
logger = structlog.get_logger(__name__)


def get_client_from_context() -> PlayStoreClient:
    """Get PlayStoreClient from request context.

    Checks for credentials in request headers first (X-Google-Credentials or
    X-Google-Credentials-Base64), then falls back to the shared client from
    lifespan context.

    Returns:
        PlayStoreClient instance

    Raises:
        PlayStoreClientError: If credentials are invalid or client cannot be created
    """
    ctx = mcp.get_context()

    # Check for per-request credentials in headers
    if hasattr(ctx, "request_context") and hasattr(ctx.request_context, "request"):
        request = ctx.request_context.request
        if request is not None and hasattr(request, "headers"):
            headers = request.headers

            # Try X-Google-Credentials header (JSON string or object)
            if "x-google-credentials" in headers:
                creds_str = headers["x-google-credentials"]
                try:
                    creds_json = json.loads(creds_str)
                    return PlayStoreClient(credentials_json=creds_json)
                except json.JSONDecodeError as e:
                    raise PlayStoreClientError(f"Invalid JSON in X-Google-Credentials header: {e}")

            # Try X-Google-Credentials-Base64 header
            if "x-google-credentials-base64" in headers:
                creds_b64 = headers["x-google-credentials-base64"]
                try:
                    creds_bytes = base64.b64decode(creds_b64)
                    creds_str = creds_bytes.decode("utf-8")
                    creds_json = json.loads(creds_str)
                    return PlayStoreClient(credentials_json=creds_json)
                except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as e:
                    raise PlayStoreClientError(
                        f"Invalid base64 or JSON in X-Google-Credentials-Base64 header: {e}"
                    )

    # Fall back to shared client from lifespan
    if hasattr(ctx, "request_context") and hasattr(ctx.request_context, "lifespan_context"):
        client: PlayStoreClient | None = ctx.request_context.lifespan_context.get("client")
        if client:
            return client

    raise PlayStoreClientError(
        "No credentials provided. Set X-Google-Credentials or X-Google-Credentials-Base64 header, "
        "or configure server with GOOGLE_PLAY_STORE_CREDENTIALS environment variable."
    )


@asynccontextmanager
async def lifespan(_server: FastMCP):  # type: ignore[no-untyped-def]
    """Lifespan context manager for the MCP server.

    Initializes the PlayStoreClient and makes it available via server context.
    """
    logger.info("Initializing Play Store MCP Server")

    # Create a shared state dict that will be accessible from custom routes
    shared_state: dict[str, Any] = {"client": None, "credentials_updated": False}

    try:
        client = PlayStoreClient()
        # Validate credentials on startup
        _ = client._get_service()
        logger.info("Play Store client initialized successfully")
        shared_state["client"] = client
    except PlayStoreClientError as e:
        logger.warning("Play Store client initialization failed", error=str(e))
        shared_state["client"] = PlayStoreClient()  # Create anyway, will error on use

    # Store shared state in the server instance for access from custom routes
    _server._shared_state = shared_state  # type: ignore[attr-defined]

    yield shared_state

    logger.info("Shutting down Play Store MCP Server")


# Initialize the MCP server
mcp = FastMCP(
    "Play Store MCP Server",
    lifespan=lifespan,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False  # Disable for public deployments
    ),
)

_tools.configure_client(get_client_from_context)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:  # noqa: ARG001
    """Health check endpoint for monitoring and load balancers."""
    return JSONResponse({"status": "healthy", "service": "play-store-mcp"})

@mcp.custom_route("/credentials", methods=["POST"])
async def update_credentials(request: Request) -> JSONResponse:
    """Update Google Play Store credentials via HTTP POST.

    This endpoint allows remote clients to provide credentials when using
    streamable-http transport. Accepts JSON credentials in the request body.

    Request body should be one of:
    - {"credentials": {...}} - Service account JSON object
    - {"credentials": "..."} - Service account JSON string
    - {"credentials_base64": "..."} - Base64-encoded service account JSON
    - {"credentials_path": "..."} - Path to credentials file

    Returns:
        JSON response with success status
    """
    try:
        body = await request.json()

        credentials = body.get("credentials")
        credentials_base64 = body.get("credentials_base64")
        credentials_path = body.get("credentials_path")

        if not credentials and not credentials_base64 and not credentials_path:
            return JSONResponse(
                {
                    "success": False,
                    "error": "Missing 'credentials', 'credentials_base64', or 'credentials_path' in request body",
                },
                status_code=400,
            )

        # Create new client with provided credentials
        if credentials_base64:
            # Decode base64 credentials
            try:
                decoded = base64.b64decode(credentials_base64).decode("utf-8")
                credentials_dict = json.loads(decoded)
                new_client = PlayStoreClient(credentials_json=credentials_dict)
            except (binascii.Error, UnicodeDecodeError) as e:
                return JSONResponse(
                    {"success": False, "error": f"Invalid base64 encoding: {e}"},
                    status_code=400,
                )
            except json.JSONDecodeError:
                return JSONResponse(
                    {"success": False, "error": "Invalid JSON in base64-decoded credentials"},
                    status_code=400,
                )
        elif credentials:
            if isinstance(credentials, str):
                # Validate it's valid JSON
                try:
                    json.loads(credentials)
                except json.JSONDecodeError:
                    return JSONResponse(
                        {"success": False, "error": "Invalid JSON in credentials string"},
                        status_code=400,
                    )
                new_client = PlayStoreClient(credentials_json=credentials)
            elif isinstance(credentials, dict):
                new_client = PlayStoreClient(credentials_json=credentials)
            else:
                return JSONResponse(
                    {"success": False, "error": "credentials must be a string or object"},
                    status_code=400,
                )
        else:
            new_client = PlayStoreClient(credentials_path=credentials_path)

        # Validate credentials by attempting to get service
        try:
            _ = new_client._get_service()
        except PlayStoreClientError as e:
            return JSONResponse(
                {"success": False, "error": f"Invalid credentials: {e}"},
                status_code=401,
            )

        # Update the client in the shared state
        if hasattr(mcp, "_shared_state"):
            mcp._shared_state["client"] = new_client  # type: ignore[attr-defined]
            mcp._shared_state["credentials_updated"] = True  # type: ignore[attr-defined]

        logger.info("Credentials updated successfully via HTTP endpoint")

        return JSONResponse(
            {"success": True, "message": "Credentials updated successfully"},
            status_code=200,
        )

    except json.JSONDecodeError:
        return JSONResponse(
            {"success": False, "error": "Invalid JSON in request body"},
            status_code=400,
        )
    except Exception as e:
        logger.exception("Error updating credentials", error=str(e))
        return JSONResponse(
            {"success": False, "error": f"Internal error: {e}"},
            status_code=500,
        )

# =============================================================================
# MCP tool wrappers (signatures preserved via functools.wraps)
# =============================================================================

@mcp.tool()
@functools.wraps(_tools.deploy_app)
def deploy_app(*args: Any, **kwargs: Any) -> Any:
    return _tools.deploy_app(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.deploy_app_multilang)
def deploy_app_multilang(*args: Any, **kwargs: Any) -> Any:
    return _tools.deploy_app_multilang(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.promote_release)
def promote_release(*args: Any, **kwargs: Any) -> Any:
    return _tools.promote_release(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_releases)
def get_releases(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_releases(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.halt_release)
def halt_release(*args: Any, **kwargs: Any) -> Any:
    return _tools.halt_release(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.update_rollout)
def update_rollout(*args: Any, **kwargs: Any) -> Any:
    return _tools.update_rollout(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_app_details)
def get_app_details(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_app_details(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_reviews)
def get_reviews(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_reviews(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.reply_to_review)
def reply_to_review(*args: Any, **kwargs: Any) -> Any:
    return _tools.reply_to_review(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.list_subscriptions)
def list_subscriptions(*args: Any, **kwargs: Any) -> Any:
    return _tools.list_subscriptions(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_subscription_status)
def get_subscription_status(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_subscription_status(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.list_voided_purchases)
def list_voided_purchases(*args: Any, **kwargs: Any) -> Any:
    return _tools.list_voided_purchases(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_vitals_overview)
def get_vitals_overview(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_vitals_overview(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_vitals_metrics)
def get_vitals_metrics(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_vitals_metrics(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.list_in_app_products)
def list_in_app_products(*args: Any, **kwargs: Any) -> Any:
    return _tools.list_in_app_products(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_in_app_product)
def get_in_app_product(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_in_app_product(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_listing)
def get_listing(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_listing(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.update_listing)
def update_listing(*args: Any, **kwargs: Any) -> Any:
    return _tools.update_listing(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.batch_update_listings)
def batch_update_listings(*args: Any, **kwargs: Any) -> Any:
    return _tools.batch_update_listings(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.list_all_listings)
def list_all_listings(*args: Any, **kwargs: Any) -> Any:
    return _tools.list_all_listings(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_testers)
def get_testers(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_testers(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.update_testers)
def update_testers(*args: Any, **kwargs: Any) -> Any:
    return _tools.update_testers(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_order)
def get_order(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_order(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_expansion_file)
def get_expansion_file(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_expansion_file(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.upload_deobfuscation_file)
def upload_deobfuscation_file(*args: Any, **kwargs: Any) -> Any:
    return _tools.upload_deobfuscation_file(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.list_bundles)
def list_bundles(*args: Any, **kwargs: Any) -> Any:
    return _tools.list_bundles(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.list_generated_apks)
def list_generated_apks(*args: Any, **kwargs: Any) -> Any:
    return _tools.list_generated_apks(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.validate_package_name)
def validate_package_name(*args: Any, **kwargs: Any) -> Any:
    return _tools.validate_package_name(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.validate_track)
def validate_track(*args: Any, **kwargs: Any) -> Any:
    return _tools.validate_track(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.validate_listing_text)
def validate_listing_text(*args: Any, **kwargs: Any) -> Any:
    return _tools.validate_listing_text(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.batch_deploy)
def batch_deploy(*args: Any, **kwargs: Any) -> Any:
    return _tools.batch_deploy(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.list_images)
def list_images(*args: Any, **kwargs: Any) -> Any:
    return _tools.list_images(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.upload_image)
def upload_image(*args: Any, **kwargs: Any) -> Any:
    return _tools.upload_image(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.delete_image)
def delete_image(*args: Any, **kwargs: Any) -> Any:
    return _tools.delete_image(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.delete_all_images)
def delete_all_images(*args: Any, **kwargs: Any) -> Any:
    return _tools.delete_all_images(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_app_details_info)
def get_app_details_info(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_app_details_info(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.update_app_details_info)
def update_app_details_info(*args: Any, **kwargs: Any) -> Any:
    return _tools.update_app_details_info(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_country_availability)
def get_country_availability(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_country_availability(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.list_users)
def list_users(*args: Any, **kwargs: Any) -> Any:
    return _tools.list_users(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.create_user)
def create_user(*args: Any, **kwargs: Any) -> Any:
    return _tools.create_user(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.delete_user)
def delete_user(*args: Any, **kwargs: Any) -> Any:
    return _tools.delete_user(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.create_grant)
def create_grant(*args: Any, **kwargs: Any) -> Any:
    return _tools.create_grant(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.delete_grant)
def delete_grant(*args: Any, **kwargs: Any) -> Any:
    return _tools.delete_grant(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.refund_order)
def refund_order(*args: Any, **kwargs: Any) -> Any:
    return _tools.refund_order(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_product_purchase)
def get_product_purchase(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_product_purchase(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.acknowledge_product_purchase)
def acknowledge_product_purchase(*args: Any, **kwargs: Any) -> Any:
    return _tools.acknowledge_product_purchase(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.consume_product_purchase)
def consume_product_purchase(*args: Any, **kwargs: Any) -> Any:
    return _tools.consume_product_purchase(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_subscription_purchase_v2)
def get_subscription_purchase_v2(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_subscription_purchase_v2(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.cancel_subscription_v2)
def cancel_subscription_v2(*args: Any, **kwargs: Any) -> Any:
    return _tools.cancel_subscription_v2(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.revoke_subscription_v2)
def revoke_subscription_v2(*args: Any, **kwargs: Any) -> Any:
    return _tools.revoke_subscription_v2(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_crash_rate)
def get_crash_rate(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_crash_rate(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_anr_rate)
def get_anr_rate(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_anr_rate(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_slow_startup_rate)
def get_slow_startup_rate(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_slow_startup_rate(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_slow_rendering_rate)
def get_slow_rendering_rate(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_slow_rendering_rate(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_excessive_wakeup_rate)
def get_excessive_wakeup_rate(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_excessive_wakeup_rate(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_stuck_wakelock_rate)
def get_stuck_wakelock_rate(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_stuck_wakelock_rate(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_lmk_rate)
def get_lmk_rate(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_lmk_rate(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.list_vitals_anomalies)
def list_vitals_anomalies(*args: Any, **kwargs: Any) -> Any:
    return _tools.list_vitals_anomalies(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_install_stats)
def get_install_stats(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_install_stats(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_search_terms)
def get_search_terms(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_search_terms(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_acquisition_funnel)
def get_acquisition_funnel(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_acquisition_funnel(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_custom_store_listings)
def get_custom_store_listings(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_custom_store_listings(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_store_listing_experiments)
def get_store_listing_experiments(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_store_listing_experiments(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_experiment_report_raw)
def get_experiment_report_raw(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_experiment_report_raw(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.create_in_app_product)
def create_in_app_product(*args: Any, **kwargs: Any) -> Any:
    return _tools.create_in_app_product(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.update_in_app_product)
def update_in_app_product(*args: Any, **kwargs: Any) -> Any:
    return _tools.update_in_app_product(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.delete_in_app_product)
def delete_in_app_product(*args: Any, **kwargs: Any) -> Any:
    return _tools.delete_in_app_product(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.get_subscription)
def get_subscription(*args: Any, **kwargs: Any) -> Any:
    return _tools.get_subscription(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.create_subscription)
def create_subscription(*args: Any, **kwargs: Any) -> Any:
    return _tools.create_subscription(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.update_subscription)
def update_subscription(*args: Any, **kwargs: Any) -> Any:
    return _tools.update_subscription(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.delete_subscription)
def delete_subscription(*args: Any, **kwargs: Any) -> Any:
    return _tools.delete_subscription(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.activate_base_plan)
def activate_base_plan(*args: Any, **kwargs: Any) -> Any:
    return _tools.activate_base_plan(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.deactivate_base_plan)
def deactivate_base_plan(*args: Any, **kwargs: Any) -> Any:
    return _tools.deactivate_base_plan(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.update_country_availability)
def update_country_availability(*args: Any, **kwargs: Any) -> Any:
    return _tools.update_country_availability(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.update_user)
def update_user(*args: Any, **kwargs: Any) -> Any:
    return _tools.update_user(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.update_grant)
def update_grant(*args: Any, **kwargs: Any) -> Any:
    return _tools.update_grant(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.defer_subscription)
def defer_subscription(*args: Any, **kwargs: Any) -> Any:
    return _tools.defer_subscription(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.delete_listing)
def delete_listing(*args: Any, **kwargs: Any) -> Any:
    return _tools.delete_listing(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.delete_all_listings)
def delete_all_listings(*args: Any, **kwargs: Any) -> Any:
    return _tools.delete_all_listings(*args, **kwargs)

@mcp.tool()
@functools.wraps(_tools.convert_region_prices)
def convert_region_prices(*args: Any, **kwargs: Any) -> Any:
    return _tools.convert_region_prices(*args, **kwargs)

def main(argv: list[str] | None = None) -> None:
    """Run the Play Store MCP Server."""
    parser = argparse.ArgumentParser(description="Play Store MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="Transport protocol (default: stdio, or set MCP_TRANSPORT env var)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MCP_HOST", "0.0.0.0"),  # noqa: S104
        help="Host to bind to for network transports (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_PORT", "8000")),
        help="Port to bind to for network transports (default: 8000)",
    )
    parser.add_argument(
        "--credentials",
        default=os.environ.get("GOOGLE_PLAY_STORE_CREDENTIALS"),
        help="Path to service account JSON key or JSON content (default: GOOGLE_PLAY_STORE_CREDENTIALS env var)",
    )
    args = parser.parse_args(argv)

    if args.credentials:
        os.environ["GOOGLE_PLAY_STORE_CREDENTIALS"] = args.credentials

    logger.info(
        "Starting Play Store MCP Server",
        transport=args.transport,
        host=args.host if args.transport != "stdio" else None,
        port=args.port if args.transport != "stdio" else None,
    )

    if args.transport != "stdio":
        mcp.settings.host = args.host
        mcp.settings.port = args.port

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
