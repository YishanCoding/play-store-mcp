"""Command catalog: MCP tool name → resource/verb/kind/risk plus browser gaps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Kind = Literal["read", "write"]
Risk = Literal["normal", "high"]

HIGH_RISK_TOOLS: frozenset[str] = frozenset(
    {
        "deploy_app",
        "deploy_app_multilang",
        "batch_deploy",
        "promote_release",
        "halt_release",
        "update_rollout",
        "refund_order",
        "cancel_subscription_v2",
        "revoke_subscription_v2",
        "defer_subscription",
        "consume_product_purchase",
        "acknowledge_product_purchase",
        "reply_to_review",
        "create_user",
        "update_user",
        "delete_user",
        "create_grant",
        "update_grant",
        "delete_grant",
        "delete_image",
        "delete_all_images",
        "delete_in_app_product",
        "delete_subscription",
        "delete_listing",
        "delete_all_listings",
    }
)

# CLI --all only: these PlayStoreClient methods accept start_index.
# Do not add start_index to the MCP tool functions (schema must stay unchanged).
PAGEABLE_TOOLS: frozenset[str] = frozenset({"get_reviews"})

WRITE_TOOLS: frozenset[str] = HIGH_RISK_TOOLS | frozenset(
    {
        "update_listing",
        "batch_update_listings",
        "update_testers",
        "upload_image",
        "upload_deobfuscation_file",
        "update_app_details_info",
        "create_in_app_product",
        "update_in_app_product",
        "create_subscription",
        "update_subscription",
        "activate_base_plan",
        "deactivate_base_plan",
        "update_country_availability",
    }
)

# name -> (resource, verb, positional param or None)
_COMMANDS: dict[str, tuple[str, str, str | None]] = {
    "deploy_app": ("app", "deploy", None),
    "deploy_app_multilang": ("app", "deploy-multilang", None),
    "promote_release": ("release", "promote", None),
    "get_releases": ("release", "list", None),
    "halt_release": ("release", "halt", None),
    "update_rollout": ("release", "rollout", None),
    "get_app_details": ("app", "get", None),
    "get_reviews": ("review", "list", None),
    "reply_to_review": ("review", "reply", "review_id"),
    "list_subscriptions": ("subscription", "list", None),
    "get_subscription_status": ("subscription", "status", "subscription_id"),
    "list_voided_purchases": ("voided-purchase", "list", None),
    "get_vitals_overview": ("vitals", "get", None),
    "get_vitals_metrics": ("vitals", "query", None),
    "list_in_app_products": ("in-app-product", "list", None),
    "get_in_app_product": ("in-app-product", "get", "sku"),
    "get_listing": ("listing", "get", None),
    "update_listing": ("listing", "update", None),
    "batch_update_listings": ("listing", "batch-update", None),
    "list_all_listings": ("listing", "list", None),
    "get_testers": ("tester", "get", None),
    "update_testers": ("tester", "update", None),
    "get_order": ("order", "get", "order_id"),
    "get_expansion_file": ("expansion-file", "get", None),
    "upload_deobfuscation_file": ("deobfuscation-file", "upload", None),
    "list_bundles": ("bundle", "list", None),
    "list_generated_apks": ("generated-apk", "list", None),
    "validate_package_name": ("package-name", "validate", None),
    "validate_track": ("track", "validate", None),
    "validate_listing_text": ("listing-text", "validate", None),
    "batch_deploy": ("app", "batch-deploy", None),
    "list_images": ("image", "list", None),
    "upload_image": ("image", "upload", None),
    "delete_image": ("image", "delete", "image_id"),
    "delete_all_images": ("image", "delete-all", None),
    "get_app_details_info": ("app-details", "get", None),
    "update_app_details_info": ("app-details", "update", None),
    "get_country_availability": ("country-availability", "get", None),
    "list_users": ("user", "list", None),
    "create_user": ("user", "create", "email"),
    "delete_user": ("user", "delete", "email"),
    "create_grant": ("grant", "create", "email"),
    "delete_grant": ("grant", "delete", "email"),
    "refund_order": ("order", "refund", "order_id"),
    "get_product_purchase": ("product-purchase", "get", None),
    "acknowledge_product_purchase": ("product-purchase", "acknowledge", None),
    "consume_product_purchase": ("product-purchase", "consume", None),
    "get_subscription_purchase_v2": ("subscription-purchase", "get", None),
    "cancel_subscription_v2": ("subscription", "cancel", None),
    "revoke_subscription_v2": ("subscription", "revoke", None),
    "get_crash_rate": ("vitals", "crash-rate", None),
    "get_anr_rate": ("vitals", "anr-rate", None),
    "get_slow_startup_rate": ("vitals", "slow-startup-rate", None),
    "get_slow_rendering_rate": ("vitals", "slow-rendering-rate", None),
    "get_excessive_wakeup_rate": ("vitals", "excessive-wakeup-rate", None),
    "get_stuck_wakelock_rate": ("vitals", "stuck-wakelock-rate", None),
    "get_lmk_rate": ("vitals", "lmk-rate", None),
    "list_vitals_anomalies": ("vitals", "anomalies", None),
    "get_install_stats": ("install-stats", "get", None),
    "get_search_terms": ("search-term", "query", None),
    "get_acquisition_funnel": ("acquisition", "query", None),
    "get_custom_store_listings": ("custom-store-listing", "list", None),
    "get_store_listing_experiments": ("store-listing-experiment", "list", None),
    "get_experiment_report_raw": ("experiment-report", "get", "experiment_id"),
    "create_in_app_product": ("in-app-product", "create", "sku"),
    "update_in_app_product": ("in-app-product", "update", "sku"),
    "delete_in_app_product": ("in-app-product", "delete", "sku"),
    "get_subscription": ("subscription", "get", "product_id"),
    "create_subscription": ("subscription", "create", "product_id"),
    "update_subscription": ("subscription", "update", "product_id"),
    "delete_subscription": ("subscription", "delete", "product_id"),
    "activate_base_plan": ("base-plan", "activate", "base_plan_id"),
    "deactivate_base_plan": ("base-plan", "deactivate", "base_plan_id"),
    "update_country_availability": ("country-availability", "update", None),
    "update_user": ("user", "update", "email"),
    "update_grant": ("grant", "update", "email"),
    "defer_subscription": ("subscription", "defer", "subscription_id"),
    "delete_listing": ("listing", "delete", None),
    "delete_all_listings": ("listing", "delete-all", None),
    "convert_region_prices": ("region-price", "convert", None),
}

_HTTP: dict[str, tuple[str, str]] = {
    "deploy_app": ("POST", "/androidpublisher/v3/applications/{package_name}/edits"),
    "deploy_app_multilang": ("POST", "/androidpublisher/v3/applications/{package_name}/edits"),
    "batch_deploy": ("POST", "/androidpublisher/v3/applications/{package_name}/edits"),
    "promote_release": ("PUT", "/androidpublisher/v3/applications/{package_name}/edits/tracks/{to_track}"),
    "halt_release": ("PUT", "/androidpublisher/v3/applications/{package_name}/edits/tracks/{track}"),
    "update_rollout": ("PUT", "/androidpublisher/v3/applications/{package_name}/edits/tracks/{track}"),
    "reply_to_review": (
        "POST",
        "/androidpublisher/v3/applications/{package_name}/reviews/{review_id}:reply",
    ),
    "refund_order": (
        "POST",
        "/androidpublisher/v3/applications/{package_name}/orders/{order_id}:refund",
    ),
    "update_listing": (
        "PUT",
        "/androidpublisher/v3/applications/{package_name}/edits/listings/{language}",
    ),
    "upload_image": (
        "POST",
        "/androidpublisher/v3/applications/{package_name}/edits/images",
    ),
}


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One MCP tool mapped to a gpcli command."""

    name: str
    resource: str
    verb: str
    kind: Kind
    risk: Risk
    positional: str | None
    http_method: str
    http_path: str

    @property
    def command(self) -> str:
        return f"{self.resource} {self.verb}"

    @property
    def alias(self) -> str:
        return self.name.replace("_", "-")


def _default_http(name: str, resource: str, kind: Kind) -> tuple[str, str]:
    if name in _HTTP:
        return _HTTP[name]
    if kind == "read":
        method = "GET"
    elif name.startswith("delete"):
        method = "DELETE"
    elif name.startswith(("update", "activate", "deactivate")):
        method = "PUT"
    else:
        method = "POST"
    return method, f"/androidpublisher/v3/{resource}"


def _build_specs() -> dict[str, ToolSpec]:
    specs: dict[str, ToolSpec] = {}
    for name, (resource, verb, positional) in _COMMANDS.items():
        kind: Kind = "write" if name in WRITE_TOOLS else "read"
        risk: Risk = "high" if name in HIGH_RISK_TOOLS else "normal"
        method, path = _default_http(name, resource, kind)
        specs[name] = ToolSpec(
            name=name,
            resource=resource,
            verb=verb,
            kind=kind,
            risk=risk,
            positional=positional,
            http_method=method,
            http_path=path,
        )
    return specs


SPECS: dict[str, ToolSpec] = _build_specs()


def spec_by_command() -> dict[tuple[str, str], ToolSpec]:
    return {(s.resource, s.verb): s for s in SPECS.values()}


def spec_by_alias() -> dict[str, ToolSpec]:
    return {s.alias: s for s in SPECS.values()}


BROWSER_CAPABILITIES: list[dict[str, Any]] = [
    {
        "command": None,
        "capability": "设置隐私政策 URL",
        "owner": "opencli-plugin-play-console set-privacy-policy",
        "reason": "Play Developer API 不提供该字段",
        "kind": "write",
        "risk": "normal",
        "mcp_tool": None,
        "aliases": [],
        "params": {},
        "description": "Set the store listing privacy policy URL in Play Console.",
    },
    {
        "command": None,
        "capability": "查看 Android 审核的真实状态",
        "owner": "jujubit-app-review-monitor",
        "reason": "API 只给轨道/发布状态，看不到 Console 页面上的审核状态",
        "kind": "read",
        "risk": "normal",
        "mcp_tool": None,
        "aliases": [],
        "params": {},
        "description": "Read the true Play Console review status that the API does not expose.",
    },
    {
        "command": None,
        "capability": "自定义商店页（CSL）上传图片",
        "owner": None,
        "reason": "not implemented",
        "kind": "write",
        "risk": "normal",
        "mcp_tool": None,
        "aliases": [],
        "params": {},
        "description": "Upload images for a Custom Store Listing.",
    },
]
