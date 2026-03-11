"""ASIN Snapshot persistence — stores the original listing state for improvement comparison.

Saves and loads the listing data fetched from SP-API at the start of an
ASIN improvement workflow. This allows the Creative Studio to display
a side-by-side comparison of current vs. improved listing content.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


def save_asin_snapshot(
    conn: psycopg.Connection,
    launch_id: int,
    catalog_data: dict[str, Any],
) -> int:
    """Persist an ASIN snapshot from SP-API catalog data.

    Parameters
    ----------
    conn : psycopg.Connection
        Active database connection.
    launch_id : int
        The launch this snapshot belongs to.
    catalog_data : dict
        Data returned by ``sp_api_catalog.fetch_asin_listing_data()``.

    Returns
    -------
    int
        The snapshot_id of the inserted row.
    """
    bullets = catalog_data.get("bullets") or []
    images = catalog_data.get("images") or []
    raw_payload = catalog_data.get("raw_payload")

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO launchpad.asin_snapshots
                (launch_id, asin, marketplace,
                 title, bullets, description, backend_keywords,
                 images, product_type, brand, category,
                 price, currency, raw_payload)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (launch_id, marketplace) DO UPDATE SET
                title = EXCLUDED.title,
                bullets = EXCLUDED.bullets,
                description = EXCLUDED.description,
                backend_keywords = EXCLUDED.backend_keywords,
                images = EXCLUDED.images,
                product_type = EXCLUDED.product_type,
                brand = EXCLUDED.brand,
                category = EXCLUDED.category,
                price = EXCLUDED.price,
                currency = EXCLUDED.currency,
                raw_payload = EXCLUDED.raw_payload,
                fetched_at = now()
            RETURNING snapshot_id
            """,
            (
                launch_id,
                catalog_data.get("asin", ""),
                catalog_data.get("marketplace", ""),
                catalog_data.get("title", ""),
                json.dumps(bullets),
                catalog_data.get("description", ""),
                catalog_data.get("backend_keywords", "") or "",
                json.dumps(images),
                catalog_data.get("product_type", ""),
                catalog_data.get("brand", ""),
                catalog_data.get("category", ""),
                catalog_data.get("price"),
                catalog_data.get("currency", ""),
                json.dumps(raw_payload) if raw_payload else None,
            ),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def load_asin_snapshot(
    conn: psycopg.Connection,
    launch_id: int,
    marketplace: str | None = None,
) -> dict[str, Any] | None:
    """Load the ASIN snapshot for a launch.

    Parameters
    ----------
    conn : psycopg.Connection
        Active database connection.
    launch_id : int
        The launch to look up.
    marketplace : str | None
        Optional marketplace filter. If None, returns the first snapshot.

    Returns
    -------
    dict | None
        Snapshot data or None if not found.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        if marketplace:
            cur.execute(
                """
                SELECT snapshot_id, launch_id, asin, marketplace,
                       title, bullets, description, backend_keywords,
                       images, product_type, brand, category,
                       price, currency, fetched_at
                FROM launchpad.asin_snapshots
                WHERE launch_id = %s AND marketplace = %s
                """,
                (launch_id, marketplace),
            )
        else:
            cur.execute(
                """
                SELECT snapshot_id, launch_id, asin, marketplace,
                       title, bullets, description, backend_keywords,
                       images, product_type, brand, category,
                       price, currency, fetched_at
                FROM launchpad.asin_snapshots
                WHERE launch_id = %s
                ORDER BY fetched_at DESC
                LIMIT 1
                """,
                (launch_id,),
            )
        row = cur.fetchone()
        if row is None:
            return None

        # Parse JSONB fields
        bullets = row.get("bullets")
        if isinstance(bullets, str):
            try:
                row["bullets"] = json.loads(bullets)
            except Exception:
                row["bullets"] = []

        images = row.get("images")
        if isinstance(images, str):
            try:
                row["images"] = json.loads(images)
            except Exception:
                row["images"] = []

        return dict(row)
