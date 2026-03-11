"""
SP-API Listings Items content push service.

Provides a thin, self-contained client for pushing listing content
(title, bullets, description, backend keywords) to Amazon via the
Listings Items API v2021-08-01.

Design:
- No dependency on amazon-bi code; all SP-API logic lives here.
- Token (LWA access token) is cached in-process with a 60-second expiry buffer.
- Seller ID and SKU/product_type are resolved from the shared amazon_dash DB.
- Only EU marketplaces are supported (all Launchpad target markets are EU).
- 429 rate-limit errors are NOT retried automatically — the caller handles them.

Environment variables consumed (all already present in .env):
    SPAPI_LWA_CLIENT_ID
    SPAPI_LWA_CLIENT_SECRET
    SPAPI_REFRESH_TOKEN
    AMAZON_SELLER_ID (optional — falls back to public.seller_accounts in DB)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

# ---------------------------------------------------------------------------
# Marketplace constants
# ---------------------------------------------------------------------------

MARKETPLACE_IDS: dict[str, str] = {
    "GB": "A1F83G8C2ARO7P",
    "UK": "A1F83G8C2ARO7P",  # alias
    "DE": "A1PA6795UKMFR9",
    "FR": "A13V1IB3VIYZZH",
    "IT": "APJ6JRA9NG5V4",
    "ES": "A1RKKUPIHCS9HS",
    "NL": "A1805IZSGTT6HS",
    "SE": "A2NODRKZP88ZB9",
    "PL": "A1C3SOZNH6SQ4A",
}

LANGUAGE_TAGS: dict[str, str] = {
    "GB": "en_GB",
    "UK": "en_GB",
    "DE": "de_DE",
    "FR": "fr_FR",
    "IT": "it_IT",
    "ES": "es_ES",
    "NL": "nl_NL",
    "SE": "sv_SE",
    "PL": "pl_PL",
}

EU_ENDPOINT = "https://sellingpartnerapi-eu.amazon.com"
LWA_TOKEN_ENDPOINT = "https://api.amazon.com/auth/o2/token"
API_VERSION = "2021-08-01"


# ---------------------------------------------------------------------------
# Token cache (module-level, process-scoped)
# ---------------------------------------------------------------------------


@dataclass
class _TokenCache:
    access_token: str = ""
    expires_at: float = 0.0


_token_cache = _TokenCache()
_token_lock = __import__("threading").Lock()


def _get_access_token() -> str:
    """Return a valid LWA access token, refreshing if needed."""
    with _token_lock:
        if _token_cache.access_token and time.time() < _token_cache.expires_at:
            return _token_cache.access_token

        client_id = os.getenv("SPAPI_LWA_CLIENT_ID", "")
        client_secret = os.getenv("SPAPI_LWA_CLIENT_SECRET", "")
        refresh_token = os.getenv("SPAPI_REFRESH_TOKEN", "")

        if not all([client_id, client_secret, refresh_token]):
            raise RuntimeError(
                "SP-API credentials not configured. "
                "Set SPAPI_LWA_CLIENT_ID, SPAPI_LWA_CLIENT_SECRET, SPAPI_REFRESH_TOKEN."
            )

        resp = requests.post(
            LWA_TOKEN_ENDPOINT,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _token_cache.access_token = data["access_token"]
        _token_cache.expires_at = time.time() + data.get("expires_in", 3600) - 60
        logger.debug(
            "SP-API LWA token refreshed; expires in ~%ds", data.get("expires_in", 3600)
        )
        return _token_cache.access_token


def invalidate_token() -> None:
    """Force a token refresh on the next call (e.g. after a 401)."""
    with _token_lock:
        _token_cache.expires_at = 0.0


# ---------------------------------------------------------------------------
# Seller ID resolution
# ---------------------------------------------------------------------------


def get_seller_id() -> str:
    """Return the Amazon Seller ID from the AMAZON_SELLER_ID env var.

    Raises RuntimeError if the variable is not set.
    """
    seller_id = os.getenv("AMAZON_SELLER_ID", "").strip()
    if not seller_id:
        raise RuntimeError(
            "AMAZON_SELLER_ID is not set. Add it to .env: AMAZON_SELLER_ID=<your seller ID>"
        )
    return seller_id


# ---------------------------------------------------------------------------
# product_type lookup from launchpad.asin_snapshots
# ---------------------------------------------------------------------------


@dataclass
class AsinListingMeta:
    product_type: str


def lookup_product_type(
    dsn: str,
    asin: str,
    marketplace: str,
) -> AsinListingMeta | None:
    """Look up product_type for an ASIN+marketplace from launchpad.asin_snapshots.

    Returns None if no row is found.
    """
    import psycopg

    mp_upper = marketplace.upper()
    # Normalise UK→GB is not needed here — asin_snapshots uses 'UK'
    asin_upper = asin.strip().upper()

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT product_type
                FROM launchpad.asin_snapshots
                WHERE asin = %s
                  AND marketplace = %s
                  AND product_type IS NOT NULL
                ORDER BY fetched_at DESC NULLS LAST
                LIMIT 1
                """,
                (asin_upper, mp_upper),
            )
            row = cur.fetchone()

    if not row:
        return None
    return AsinListingMeta(product_type=str(row[0]))


# ---------------------------------------------------------------------------
# Content patch builder
# ---------------------------------------------------------------------------


def build_content_patches(
    marketplace: str,
    title: str | None = None,
    bullets: list[str] | None = None,
    description: str | None = None,
    keywords: str | None = None,
) -> list[dict[str, Any]]:
    """
    Build the patches list for a patchListingItem call.

    Only fields that are not None are included.
    bullets: list of strings (one per bullet point, up to 5)
    keywords: single space-separated string (not comma-separated)
    """
    mp = marketplace.upper()
    # Normalise UK→GB for marketplace_id lookup
    mp_key = "GB" if mp == "UK" else mp
    marketplace_id = MARKETPLACE_IDS.get(mp_key, "")
    lang = LANGUAGE_TAGS.get(mp_key, "en_GB")

    patches: list[dict[str, Any]] = []

    if title is not None:
        patches.append(
            {
                "op": "replace",
                "path": "/attributes/item_name",
                "value": [
                    {
                        "value": title,
                        "language_tag": lang,
                        "marketplace_id": marketplace_id,
                    }
                ],
            }
        )

    if bullets is not None:
        clean_bullets = [b for b in bullets if str(b).strip()]
        patches.append(
            {
                "op": "replace",
                "path": "/attributes/bullet_point",
                "value": [
                    {
                        "value": str(b),
                        "language_tag": lang,
                        "marketplace_id": marketplace_id,
                    }
                    for b in clean_bullets
                ],
            }
        )

    if description is not None:
        patches.append(
            {
                "op": "replace",
                "path": "/attributes/product_description",
                "value": [
                    {
                        "value": description,
                        "language_tag": lang,
                        "marketplace_id": marketplace_id,
                    }
                ],
            }
        )

    if keywords is not None:
        # Normalise: collapse any stray commas to spaces (canonical format is space-separated)
        kw_str = keywords.replace(",", " ")
        # Collapse extra whitespace
        kw_str = " ".join(kw_str.split())
        patches.append(
            {
                "op": "replace",
                "path": "/attributes/generic_keyword",
                "value": [
                    {
                        "value": kw_str,
                        "language_tag": lang,
                        "marketplace_id": marketplace_id,
                    }
                ],
            }
        )

    return patches


# ---------------------------------------------------------------------------
# Client-side validation
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def validate_content(
    title: str | None,
    bullets: list[str] | None,
    description: str | None,
    keywords: str | None,
) -> ValidationResult:
    """Run client-side length checks before hitting the API."""
    errors: list[str] = []

    if title is not None and len(title) > 200:
        errors.append(f"Title is {len(title)} chars — max 200")

    if bullets is not None:
        for i, b in enumerate(bullets, 1):
            if len(str(b)) > 255:
                errors.append(f"Bullet {i} is {len(b)} chars — max 255")

    if description is not None and len(description) > 2000:
        errors.append(f"Description is {len(description)} chars — max 2000")

    if keywords is not None:
        kw_str = keywords.replace(",", " ")
        kw_bytes = len(" ".join(kw_str.split()).encode("utf-8"))
        if kw_bytes > 249:
            errors.append(f"Backend keywords are {kw_bytes} bytes — max 249 bytes")

    return ValidationResult(ok=len(errors) == 0, errors=errors)


# ---------------------------------------------------------------------------
# Push call
# ---------------------------------------------------------------------------


@dataclass
class PushResult:
    status: str  # "VALID" | "INVALID" | "ACCEPTED" | "ERROR"
    submission_id: str = ""
    issues: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""  # set when status == "ERROR" (network/auth failure)


def push_listing_content(
    *,
    seller_id: str,
    sku: str,
    marketplace: str,
    product_type: str,
    title: str | None = None,
    bullets: list[str] | None = None,
    description: str | None = None,
    keywords: str | None = None,
    preview: bool = True,
) -> PushResult:
    """
    Push listing content to Amazon via patchListingItem.

    preview=True  → VALIDATION_PREVIEW (safe dry run, no changes applied)
    preview=False → live submission (changes applied immediately)

    Returns a PushResult describing the outcome.
    """
    mp_key = "GB" if marketplace.upper() == "UK" else marketplace.upper()
    marketplace_id = MARKETPLACE_IDS.get(mp_key)
    if not marketplace_id:
        return PushResult(
            status="ERROR",
            error_message=f"Unsupported marketplace: {marketplace}",
        )

    patches = build_content_patches(
        marketplace=marketplace,
        title=title,
        bullets=bullets,
        description=description,
        keywords=keywords,
    )
    if not patches:
        return PushResult(status="ERROR", error_message="No content fields to push.")

    url = f"{EU_ENDPOINT}/listings/{API_VERSION}/items/{seller_id}/{sku}"
    params: dict[str, str] = {"marketplaceIds": marketplace_id}
    if preview:
        params["mode"] = "VALIDATION_PREVIEW"

    body = {"productType": product_type, "patches": patches}

    try:
        token = _get_access_token()
        headers = {
            "x-amz-access-token": token,
            "Content-Type": "application/json",
        }
        resp = requests.patch(
            url, headers=headers, params=params, json=body, timeout=20
        )

        if resp.status_code == 401:
            # Token may have expired mid-session — invalidate and retry once
            invalidate_token()
            headers["x-amz-access-token"] = _get_access_token()
            resp = requests.patch(
                url, headers=headers, params=params, json=body, timeout=20
            )

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            return PushResult(
                status="ERROR",
                error_message=f"Rate limited by Amazon (429). Retry after {retry_after}s.",
                raw_response={"status_code": 429},
            )

        if resp.status_code != 200:
            logger.error("SP-API PATCH %d: %s", resp.status_code, resp.text[:500])
            return PushResult(
                status="ERROR",
                error_message=f"Amazon returned HTTP {resp.status_code}: {resp.text[:300]}",
                raw_response={"status_code": resp.status_code, "body": resp.text},
            )

        data = resp.json()
        issues = data.get("issues", [])
        return PushResult(
            status=data.get("status", "UNKNOWN"),
            submission_id=data.get("submissionId", ""),
            issues=issues,
            errors=[i for i in issues if i.get("severity") == "ERROR"],
            warnings=[i for i in issues if i.get("severity") == "WARNING"],
            raw_response=data,
        )

    except requests.RequestException as exc:
        logger.error("SP-API network error: %s", exc)
        return PushResult(status="ERROR", error_message=str(exc))
