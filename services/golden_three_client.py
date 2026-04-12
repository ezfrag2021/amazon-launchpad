"""
Read-only client for fetching Golden Three keyword results from market_intel.

Queries market_intel.golden_three_results on the external amazon_dash DB using
the MARKET_INTEL_DSN connection (market_intel_writer has SELECT on this table).

Marketplace code mapping: Launchpad uses 'UK', the DB stores 'GB'.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

load_dotenv()

# Launchpad→DB marketplace code mapping
_MP_TO_DB: dict[str, str] = {
    "UK": "GB",
    "GB": "GB",
    "DE": "DE",
    "FR": "FR",
    "IT": "IT",
    "ES": "ES",
    "NL": "NL",
    "SE": "SE",
    "PL": "PL",
}

_ENV_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env_refs(value: str) -> str:
    expanded = value
    for _ in range(3):
        refs = _ENV_REF_PATTERN.findall(expanded)
        if not refs:
            break
        expanded = _ENV_REF_PATTERN.sub(lambda m: os.getenv(m.group(1), ""), expanded)
    return expanded


def _get_market_intel_dsn() -> str:
    """Resolve the MARKET_INTEL_DSN for the external amazon_dash DB."""
    raw = os.getenv("MARKET_INTEL_DSN", "")
    if not raw:
        raise RuntimeError("MARKET_INTEL_DSN is not set in the environment")
    return _expand_env_refs(raw)


def fetch_golden_three(
    asin: str,
    marketplace: str,
) -> dict[str, Any] | None:
    """Return the Golden Three result row for *asin* / *marketplace*, or None.

    Maps Launchpad marketplace codes (UK→GB) before querying.  On any DB
    error the exception is logged and None is returned so the caller can
    degrade gracefully.

    Returns a dict with keys:
        anchor_keyword, scaler_keyword, specialist_keyword,
        title_draft, anchor_search_volume, scaler_search_volume,
        specialist_search_volume, engine_mode, warning, computed_at
    """
    db_marketplace = _MP_TO_DB.get(marketplace.upper(), marketplace.upper())
    asin_upper = asin.strip().upper()

    try:
        dsn = _get_market_intel_dsn()
        with psycopg.connect(dsn) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        anchor_keyword,
                        scaler_keyword,
                        specialist_keyword,
                        title_draft,
                        anchor_search_volume,
                        scaler_search_volume,
                        specialist_search_volume,
                        engine_mode,
                        warning,
                        computed_at
                    FROM market_intel.golden_three_results
                    WHERE asin = %s
                      AND marketplace = %s
                    """,
                    (asin_upper, db_marketplace),
                )
                row = cur.fetchone()

        if not row:
            logger.debug(
                "No golden_three_results for ASIN=%s marketplace=%s",
                asin_upper,
                db_marketplace,
            )
            return None

        return dict(row)

    except Exception as exc:
        logger.warning(
            "golden_three_client: could not fetch for %s/%s: %s",
            asin_upper,
            db_marketplace,
            exc,
        )
        return None


def build_keywords_string(row: dict[str, Any]) -> str:
    """Return a comma-separated string of the three golden keywords."""
    parts = [
        str(row.get("anchor_keyword") or "").strip(),
        str(row.get("scaler_keyword") or "").strip(),
        str(row.get("specialist_keyword") or "").strip(),
    ]
    return ", ".join(p for p in parts if p)
