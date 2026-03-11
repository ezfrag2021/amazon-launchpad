"""SP-API Catalog & Listings helpers for fetching existing ASIN data.

Provides functions to retrieve current listing content (title, bullets,
description, images, attributes) from Amazon SP-API for the ASIN
improvement workflow.

Uses the same credential resolution pattern as sp_api_fees.py.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Credential resolution (shared pattern with sp_api_fees.py)
# ---------------------------------------------------------------------------
def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def _load_optional_external_env() -> None:
    """Best-effort load of shared SP-API env files."""
    try:
        from dotenv import load_dotenv  # type: ignore[import]
    except Exception:  # noqa: BLE001
        return

    candidates = [
        Path("/mnt/amazon-bi/.env"),
        Path("/mnt/amazon-bi/.env.local"),
    ]
    for env_path in candidates:
        if env_path.exists():
            load_dotenv(env_path, override=False)


def _resolve_credentials() -> dict[str, str]:
    """Resolve SP-API credentials from environment variables.

    Returns
    -------
    dict[str, str]
        Credentials dict suitable for sp_api client instantiation.

    Raises
    ------
    RuntimeError
        If required credentials are missing.
    """
    _load_optional_external_env()

    refresh_token = _env_first(
        "SP_API_REFRESH_TOKEN",
        "SPAPI_REFRESH_TOKEN",
        "AMZ_SP_API_REFRESH_TOKEN",
    )
    lwa_app_id = _env_first("SP_API_LWA_APP_ID", "SPAPI_LWA_CLIENT_ID", "LWA_APP_ID")
    lwa_client_secret = _env_first(
        "SP_API_LWA_CLIENT_SECRET",
        "SPAPI_LWA_CLIENT_SECRET",
        "LWA_CLIENT_SECRET",
    )
    aws_access_key = _env_first("SP_API_AWS_ACCESS_KEY", "AWS_ACCESS_KEY_ID")
    aws_secret_key = _env_first("SP_API_AWS_SECRET_KEY", "AWS_SECRET_ACCESS_KEY")
    role_arn = _env_first("SP_API_ROLE_ARN")

    missing = []
    if not refresh_token:
        missing.append("SP_API_REFRESH_TOKEN")
    if not lwa_app_id:
        missing.append("SP_API_LWA_APP_ID")
    if not lwa_client_secret:
        missing.append("SP_API_LWA_CLIENT_SECRET")
    if not aws_access_key:
        missing.append("SP_API_AWS_ACCESS_KEY")
    if not aws_secret_key:
        missing.append("SP_API_AWS_SECRET_KEY")
    if missing:
        raise RuntimeError(
            "SP-API credentials not configured. Missing: " + ", ".join(missing)
        )

    assert refresh_token is not None
    assert lwa_app_id is not None
    assert lwa_client_secret is not None
    assert aws_access_key is not None
    assert aws_secret_key is not None

    credentials: dict[str, str] = {
        "refresh_token": refresh_token,
        "lwa_app_id": lwa_app_id,
        "lwa_client_secret": lwa_client_secret,
        "aws_access_key": aws_access_key,
        "aws_secret_key": aws_secret_key,
    }
    if role_arn:
        credentials["role_arn"] = role_arn

    return credentials


def _resolve_marketplace_enum(code: str) -> Any:
    """Convert marketplace code to sp_api Marketplaces enum."""
    from sp_api.base import Marketplaces  # type: ignore[import]

    normalized = (code or "").strip().upper()
    if normalized == "UK":
        normalized = "GB"
    if not normalized or not hasattr(Marketplaces, normalized):
        raise ValueError(f"Unsupported SP-API marketplace code: {code}")
    return getattr(Marketplaces, normalized)


# ---------------------------------------------------------------------------
# Marketplace ID mapping (for Catalog Items API)
# ---------------------------------------------------------------------------
_MARKETPLACE_IDS: dict[str, str] = {
    "US": "ATVPDKIKX0DER",
    "UK": "A1F83G8C2ARO7P",
    "GB": "A1F83G8C2ARO7P",
    "DE": "A1PA6795UKMFR9",
    "FR": "A13V1IB3VIYZZH",
    "IT": "APJ6JRA9NG5V4",
    "ES": "A1RKKUPIHCS9HS",
    "CA": "A2EUQ1WTGCTBG2",
    "AU": "A39IBJ37TRP1C6",
    "JP": "A1VC38T7YXB528",
    "MX": "A1AM78C64UM0Y8",
    "IN": "A21TJRUUN4KGV",
    "NL": "A1805IZSGTT6HS",
    "SE": "A2NODRKZP88ZB9",
    "PL": "A1C3SOZNH6GQ1W",
    "BE": "AMEN7PMS3EDWL",
}


def _get_marketplace_id(code: str) -> str:
    """Get Amazon marketplace ID for a marketplace code."""
    normalized = (code or "").strip().upper()
    if normalized in _MARKETPLACE_IDS:
        return _MARKETPLACE_IDS[normalized]
    raise ValueError(f"Unknown marketplace code: {code}")


# ---------------------------------------------------------------------------
# Catalog Items API — fetch listing content
# ---------------------------------------------------------------------------
def fetch_catalog_item(
    asin: str,
    marketplace: str,
) -> dict[str, Any]:
    """Fetch catalog item data for an ASIN using SP-API Catalog Items API.

    Retrieves: title, bullet points, description, images, product types,
    sales rankings, and item dimensions.

    Parameters
    ----------
    asin : str
        The Amazon ASIN to look up.
    marketplace : str
        Marketplace code (e.g. 'UK', 'US', 'DE').

    Returns
    -------
    dict[str, Any]
        Structured catalog data with keys:
        - asin, marketplace, title, bullets, description
        - images (list of dicts with url, variant, width, height)
        - product_type, brand, category
        - price, currency
        - raw_payload (full API response for debugging)

    Raises
    ------
    ImportError
        If python-amazon-sp-api is not installed.
    RuntimeError
        If SP-API credentials are missing or API call fails.
    """
    catalog_version_2022: Any = None

    try:
        from sp_api.api import CatalogItemsV20220401 as CatalogItemsClient  # type: ignore[import]

        uses_v2022_client = True
    except ImportError:
        try:
            # Compatibility fallback for older python-amazon-sp-api releases.
            from sp_api.api import CatalogItems as CatalogItemsClient  # type: ignore[import]
            from sp_api.api import CatalogItemsVersion  # type: ignore[import]

            catalog_version_2022 = CatalogItemsVersion.V_2022_04_01

            uses_v2022_client = False
        except ImportError as exc:
            raise ImportError(
                "python-amazon-sp-api is not installed (or missing CatalogItems client). "
                "Run: pip install 'python-amazon-sp-api>=1.9,<2'"
            ) from exc

    credentials = _resolve_credentials()
    marketplace_enum = _resolve_marketplace_enum(marketplace)
    marketplace_id = _get_marketplace_id(marketplace)

    catalog_kwargs: dict[str, Any] = {
        "marketplace": marketplace_enum,
        "credentials": credentials,
    }
    if not uses_v2022_client:
        catalog_kwargs["version"] = catalog_version_2022

    catalog_api = CatalogItemsClient(**catalog_kwargs)

    included_data = [
        "summaries",
        "attributes",
        "images",
        "productTypes",
        "salesRanks",
        "dimensions",
    ]

    # Fetch with all available includedData
    try:
        if uses_v2022_client:
            resp = catalog_api.get_catalog_item(
                asin=asin,
                includedData=included_data,
                marketplaceIds=marketplace_id,
            )
        else:
            # Older clients route to the same endpoint via CatalogItems.
            resp = catalog_api.get_catalog_item(
                asin=asin,
                includedData=included_data,
                marketplaceIds=marketplace_id,
            )
    except Exception as exc:
        raise RuntimeError(
            f"SP-API CatalogItems call failed for {asin} in {marketplace}: {exc}"
        ) from exc

    payload = getattr(resp, "payload", resp)
    if isinstance(payload, dict) and "payload" in payload:
        payload = payload["payload"]

    return _parse_catalog_response(payload, asin, marketplace)


def _parse_catalog_response(
    payload: Any,
    asin: str,
    marketplace: str,
) -> dict[str, Any]:
    """Parse the CatalogItems API response into a clean structure."""
    result: dict[str, Any] = {
        "asin": asin,
        "marketplace": marketplace,
        "title": "",
        "bullets": [],
        "description": "",
        "backend_keywords": "",
        "images": [],
        "product_type": "",
        "brand": "",
        "category": "",
        "price": None,
        "currency": "",
        "raw_payload": payload,
    }

    if not isinstance(payload, dict):
        return result

    # --- Summaries (title, brand, product type) ---
    summaries = payload.get("summaries") or []
    if isinstance(summaries, list):
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            if not result["title"]:
                result["title"] = str(summary.get("itemName") or "").strip()
            if not result["brand"]:
                result["brand"] = str(summary.get("brand") or "").strip()
            if not result["product_type"]:
                result["product_type"] = str(summary.get("productType") or "").strip()
            if not result["category"]:
                classification = summary.get("classification") or {}
                result["category"] = str(
                    classification.get("displayName") or ""
                ).strip()

    # --- Attributes (bullet points, description) ---
    attributes = payload.get("attributes") or {}
    if isinstance(attributes, dict):
        # Bullet points
        bullet_points = attributes.get("bullet_point") or []
        if isinstance(bullet_points, list):
            for bp in bullet_points:
                if isinstance(bp, dict):
                    val = bp.get("value") or ""
                    if val:
                        result["bullets"].append(str(val).strip())
                elif isinstance(bp, str) and bp.strip():
                    result["bullets"].append(bp.strip())

        # Description
        descriptions = attributes.get("product_description") or []
        if isinstance(descriptions, list):
            for desc in descriptions:
                if isinstance(desc, dict):
                    val = desc.get("value") or ""
                    if val:
                        result["description"] = str(val).strip()
                        break
                elif isinstance(desc, str) and desc.strip():
                    result["description"] = desc.strip()
                    break
        elif isinstance(descriptions, str) and descriptions.strip():
            result["description"] = descriptions.strip()

        # Brand fallback from attributes
        if not result["brand"]:
            brand_attrs = attributes.get("brand") or []
            if isinstance(brand_attrs, list) and brand_attrs:
                first = brand_attrs[0]
                if isinstance(first, dict):
                    result["brand"] = str(first.get("value") or "").strip()
                elif isinstance(first, str):
                    result["brand"] = first.strip()

        # Item name fallback from attributes
        if not result["title"]:
            title_attrs = attributes.get("item_name") or []
            if isinstance(title_attrs, list) and title_attrs:
                first = title_attrs[0]
                if isinstance(first, dict):
                    result["title"] = str(first.get("value") or "").strip()
                elif isinstance(first, str):
                    result["title"] = first.strip()

        # Backend search terms (generic_keyword attribute)
        generic_keywords = attributes.get("generic_keyword") or []
        if isinstance(generic_keywords, list):
            kw_parts: list[str] = []
            for gk in generic_keywords:
                if isinstance(gk, dict):
                    val = str(gk.get("value") or "").strip()
                    if val:
                        kw_parts.append(val)
                elif isinstance(gk, str) and gk.strip():
                    kw_parts.append(gk.strip())
            if kw_parts:
                result["backend_keywords"] = " ".join(kw_parts)
        elif isinstance(generic_keywords, str) and generic_keywords.strip():
            result["backend_keywords"] = generic_keywords.strip()

        # Price from list_price attribute
        list_price = attributes.get("list_price") or []
        if isinstance(list_price, list) and list_price:
            price_obj = list_price[0]
            if isinstance(price_obj, dict):
                result["price"] = _safe_float(price_obj.get("value"))
                result["currency"] = str(price_obj.get("currency") or "").strip()

    # --- Images ---
    images_data = payload.get("images") or []
    if isinstance(images_data, list):
        for image_set in images_data:
            if not isinstance(image_set, dict):
                continue
            image_list = image_set.get("images") or []
            if isinstance(image_list, list):
                for img in image_list:
                    if not isinstance(img, dict):
                        continue
                    url = str(img.get("link") or "").strip()
                    if url:
                        result["images"].append(
                            {
                                "url": url,
                                "variant": str(img.get("variant") or "").strip(),
                                "width": _safe_int(img.get("width")),
                                "height": _safe_int(img.get("height")),
                            }
                        )

    # --- Product Types ---
    product_types = payload.get("productTypes") or []
    if isinstance(product_types, list) and product_types and not result["product_type"]:
        first_pt = product_types[0]
        if isinstance(first_pt, dict):
            result["product_type"] = str(first_pt.get("productType") or "").strip()

    # --- Sales Ranks (extract category from rankings) ---
    sales_ranks = payload.get("salesRanks") or []
    if isinstance(sales_ranks, list) and not result["category"]:
        for rank_set in sales_ranks:
            if isinstance(rank_set, dict):
                display_group = rank_set.get("displayGroupRanks") or []
                if isinstance(display_group, list) and display_group:
                    first_rank = display_group[0]
                    if isinstance(first_rank, dict):
                        result["category"] = str(first_rank.get("title") or "").strip()
                        break

    return result


def _safe_float(value: Any) -> float | None:
    """Safely convert a value to float."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _safe_int(value: Any) -> int | None:
    """Safely convert a value to int."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Listings Items API — fetch detailed listing data
# ---------------------------------------------------------------------------
def fetch_listings_item(
    seller_id: str,
    sku: str,
    marketplace: str,
) -> dict[str, Any]:
    """Fetch listing data for a seller's SKU using SP-API Listings Items API.

    This provides the seller-specific listing data including their current
    listing content. Requires seller_id and SKU.

    Parameters
    ----------
    seller_id : str
        The seller's Amazon Merchant ID.
    sku : str
        The seller's SKU for the product.
    marketplace : str
        Marketplace code (e.g. 'UK', 'US', 'DE').

    Returns
    -------
    dict[str, Any]
        Listing item data including attributes and fulfillment info.
    """
    try:
        from sp_api.api import ListingsItems  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "python-amazon-sp-api is not installed. "
            "Run: pip install 'python-amazon-sp-api>=1.9,<2'"
        ) from exc

    credentials = _resolve_credentials()
    marketplace_enum = _resolve_marketplace_enum(marketplace)
    marketplace_id = _get_marketplace_id(marketplace)

    listings_api = ListingsItems(marketplace=marketplace_enum, credentials=credentials)

    try:
        resp = listings_api.get_listings_item(
            sellerId=seller_id,
            sku=sku,
            marketplaceIds=[marketplace_id],
            includedData=["summaries", "attributes", "issues"],
        )
    except Exception as exc:
        raise RuntimeError(
            f"SP-API ListingsItems call failed for SKU {sku}: {exc}"
        ) from exc

    payload = getattr(resp, "payload", resp)
    return payload if isinstance(payload, dict) else {"raw": payload}


# ---------------------------------------------------------------------------
# Convenience: fetch everything we can for an ASIN
# ---------------------------------------------------------------------------
def fetch_asin_listing_data(
    asin: str,
    marketplace: str,
) -> dict[str, Any]:
    """Fetch all available listing data for an ASIN.

    This is the primary entry point for the improvement workflow.
    Uses the Catalog Items API (no seller ID required) to get:
    - Title, bullets, description
    - Images
    - Product type, brand, category
    - Price

    Parameters
    ----------
    asin : str
        The Amazon ASIN to look up.
    marketplace : str
        Marketplace code (e.g. 'UK', 'US', 'DE').

    Returns
    -------
    dict[str, Any]
        Complete listing data with keys:
        - asin, marketplace, title, bullets, description
        - images, product_type, brand, category
        - price, currency
        - fetch_success (bool), fetch_error (str or None)
    """
    try:
        catalog_data = fetch_catalog_item(asin, marketplace)
        catalog_data["fetch_success"] = True
        catalog_data["fetch_error"] = None

        # Validate we got meaningful data
        if not catalog_data.get("title"):
            catalog_data["fetch_success"] = False
            catalog_data["fetch_error"] = (
                f"ASIN {asin} returned no title from the Catalog API. "
                "The ASIN may not exist in the {marketplace} marketplace."
            )

        return catalog_data

    except Exception as exc:
        logger.error("Failed to fetch ASIN %s in %s: %s", asin, marketplace, exc)
        return {
            "asin": asin,
            "marketplace": marketplace,
            "title": "",
            "bullets": [],
            "description": "",
            "backend_keywords": "",
            "images": [],
            "product_type": "",
            "brand": "",
            "category": "",
            "price": None,
            "currency": "",
            "raw_payload": None,
            "fetch_success": False,
            "fetch_error": str(exc),
        }
