"""
ASIN Importer — Import an existing Amazon listing for conversion improvement.

This page provides the entry point for the ASIN improvement workflow:
  1. Enter an ASIN + marketplace
  2. Fetch current listing data from SP-API (Catalog Items API)
  3. Review the fetched data
  4. Create an improvement project and jump to Creative Studio
"""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg
import streamlit as st
from dotenv import load_dotenv
from psycopg.rows import dict_row

from services.asin_snapshot import load_asin_snapshot, save_asin_snapshot
from services.bdl_theme import apply_bdl_theme, render_bdl_footer
from services.db_connection import connect, resolve_dsn
from services.launch_state import (
    WORKFLOW_ASIN_IMPROVEMENT,
    LaunchStateManager,
)
from services.sp_api_catalog import fetch_asin_listing_data

logger = logging.getLogger(__name__)
load_dotenv()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _get_dsn() -> str:
    return resolve_dsn("LAUNCHPAD_DB_DSN", "MARKET_INTEL_DSN", "PG_DSN")


def _open_conn() -> psycopg.Connection:
    return connect(_get_dsn())


# ---------------------------------------------------------------------------
# Marketplace options
# ---------------------------------------------------------------------------
MARKETPLACE_OPTIONS = {
    "UK": "United Kingdom (A1F83G8C2ARO7P)",
    "US": "United States (ATVPDKIKX0DER)",
    "DE": "Germany (A1PA6795UKMFR9)",
    "FR": "France (A13V1IB3VIYZZH)",
    "IT": "Italy (APJ6JRA9NG5V4)",
    "ES": "Spain (A1RKKUPIHCS9HS)",
    "CA": "Canada (A2EUQ1WTGCTBG2)",
    "AU": "Australia (A39IBJ37TRP1C6)",
}


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(
        page_title="ASIN Importer | Bodhi & Digby",
        page_icon="Logos/favicon.ico",
        layout="wide",
    )

    theme_state = apply_bdl_theme(
        "Import an existing Amazon listing and improve it for higher conversion."
    )

    st.title("🔧 ASIN Importer")
    st.markdown(
        "Fetch an existing Amazon listing via SP-API and create an **improvement project** "
        "that jumps straight to the Creative Studio, Images, and A+ Content workflows."
    )
    st.divider()

    # --- Existing improvement projects ---
    _render_existing_improvements()

    st.divider()

    # --- New import form ---
    st.subheader("📥 Import a New ASIN")

    col1, col2 = st.columns(2)
    with col1:
        asin = (
            st.text_input(
                "ASIN *",
                placeholder="e.g. B08N5WRWNW",
                help="The Amazon ASIN you want to improve.",
                key="import_asin",
            )
            .strip()
            .upper()
        )

    with col2:
        marketplace = st.selectbox(
            "Marketplace *",
            options=list(MARKETPLACE_OPTIONS.keys()),
            format_func=lambda k: f"{k} — {MARKETPLACE_OPTIONS[k]}",
            key="import_marketplace",
            help="The marketplace where this ASIN is currently listed.",
        )

    launch_name = st.text_input(
        "Project Name (optional)",
        placeholder="e.g. Improve Water Bottle UK Listing",
        help="Friendly name for this improvement project.",
        key="import_launch_name",
    )

    st.divider()

    # --- Fetch button ---
    fetch_col, _ = st.columns([1, 3])
    with fetch_col:
        fetch_clicked = st.button(
            "🔍 Fetch Listing from Amazon",
            type="primary",
            use_container_width=True,
            disabled=not asin,
        )

    if fetch_clicked and asin:
        _handle_fetch(asin, marketplace, launch_name)

    # --- Display fetched data ---
    fetched = st.session_state.get("import_fetched_data")
    if fetched and fetched.get("fetch_success"):
        st.divider()
        _render_fetched_listing(fetched)

        st.divider()

        # --- Create improvement project ---
        create_col, _ = st.columns([1, 3])
        with create_col:
            if st.button(
                "🚀 Create Improvement Project",
                type="primary",
                use_container_width=True,
            ):
                _create_improvement_project(fetched, launch_name)

    render_bdl_footer(theme_state)


def _handle_fetch(asin: str, marketplace: str, launch_name: str) -> None:
    """Fetch listing data from SP-API and store in session state."""
    if len(asin) != 10:
        st.error("ASIN must be exactly 10 characters (e.g. B08N5WRWNW).")
        return

    with st.spinner(f"Fetching listing data for **{asin}** in **{marketplace}**..."):
        try:
            data = fetch_asin_listing_data(asin, marketplace)
        except Exception as exc:
            st.error(f"Failed to fetch listing: {exc}")
            return

    if not data.get("fetch_success"):
        error_msg = data.get("fetch_error") or "Unknown error"
        st.error(f"Could not retrieve listing data: {error_msg}")
        st.session_state["import_fetched_data"] = None
        return

    st.session_state["import_fetched_data"] = data
    st.success(f"Successfully fetched listing for **{data.get('title', asin)}**")
    st.rerun()


def _render_fetched_listing(data: dict[str, Any]) -> None:
    """Render the fetched listing data for review before creating a project."""
    st.subheader("📋 Current Listing Review")
    st.caption(
        "Review the current listing content below. This will be saved as a snapshot "
        "for side-by-side comparison when you improve it."
    )

    # --- Summary metrics ---
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("ASIN", data.get("asin", "—"))
    col2.metric("Marketplace", data.get("marketplace", "—"))
    col3.metric("Brand", data.get("brand", "—") or "—")

    price = data.get("price")
    currency = data.get("currency", "")
    if price is not None:
        col4.metric("Price", f"{currency} {price:.2f}")
    else:
        col4.metric("Price", "—")

    # --- Title ---
    st.markdown("#### Title")
    title = data.get("title", "")
    if title:
        st.info(title)
        st.caption(f"Character count: {len(title)}/200")
    else:
        st.warning("No title found in the listing.")

    # --- Bullet Points ---
    st.markdown("#### Bullet Points")
    bullets = data.get("bullets") or []
    if bullets:
        for i, bullet in enumerate(bullets, 1):
            st.markdown(f"**{i}.** {bullet}")
            st.caption(f"   Characters: {len(bullet)}/500")
    else:
        st.warning("No bullet points found.")

    # --- Description ---
    with st.expander("📝 Description", expanded=bool(data.get("description"))):
        description = data.get("description", "")
        if description:
            st.markdown(description)
            st.caption(f"Character count: {len(description)}/2000")
        else:
            st.warning("No description found.")

    # --- Images ---
    images = data.get("images") or []
    if images:
        with st.expander(f"🖼️ Images ({len(images)} found)", expanded=True):
            # Show up to 7 images in a grid
            img_cols = st.columns(min(len(images), 7))
            for i, img in enumerate(images[:7]):
                with img_cols[i % len(img_cols)]:
                    url = img.get("url", "")
                    variant = img.get("variant", "")
                    if url:
                        st.image(url, caption=variant or f"Image {i + 1}", width=150)
    else:
        st.info("No images found in the catalog data.")

    # --- Product metadata ---
    with st.expander("📊 Product Metadata"):
        meta_col1, meta_col2 = st.columns(2)
        with meta_col1:
            st.markdown(f"**Product Type:** {data.get('product_type', '—')}")
            st.markdown(f"**Category:** {data.get('category', '—')}")
        with meta_col2:
            st.markdown(f"**Brand:** {data.get('brand', '—')}")


def _create_improvement_project(
    catalog_data: dict[str, Any],
    launch_name: str,
) -> None:
    """Create an improvement launch record and save the ASIN snapshot."""
    asin = catalog_data.get("asin", "")
    marketplace = catalog_data.get("marketplace", "")

    if not asin or not marketplace:
        st.error("Missing ASIN or marketplace data.")
        return

    # Build a default name if not provided
    if not launch_name.strip():
        brand = catalog_data.get("brand", "")
        title_short = (catalog_data.get("title") or asin)[:50]
        launch_name = f"Improve: {brand + ' - ' if brand else ''}{title_short}"

    try:
        with _open_conn() as conn:
            lsm = LaunchStateManager()

            # 1. Create the improvement launch record
            launch_id = lsm.create_improvement_launch(
                conn,
                asin=asin,
                marketplace=marketplace,
                launch_name=launch_name.strip(),
                product_description=catalog_data.get("description", "")[:500] or None,
                product_category=catalog_data.get("category")
                or catalog_data.get("product_type")
                or None,
            )

            # 2. Save the ASIN snapshot for comparison
            save_asin_snapshot(conn, launch_id, catalog_data)

            # 3. Look up backend keywords from niche_keyword_bank
            #    (SP-API Catalog Items API does not return generic_keyword)
            backend_kw = _fetch_backend_keywords_from_niche_bank(conn, asin)

            # 4. Pre-populate a listing draft from the current listing
            #    so the Creative Studio can show current content immediately
            bullets = catalog_data.get("bullets") or []
            _save_initial_listing_draft(
                conn,
                launch_id=launch_id,
                marketplace=marketplace,
                title=catalog_data.get("title", ""),
                bullets=bullets,
                description=catalog_data.get("description", ""),
                backend_keywords=backend_kw,
            )

            conn.commit()

        # Set session state for Creative Studio navigation
        st.session_state["selected_launch_id"] = launch_id
        st.session_state["cs_selected_launch_id"] = launch_id
        st.session_state["import_fetched_data"] = None

        st.success(
            f"Improvement project **#{launch_id}** created for ASIN `{asin}` "
            f"in **{marketplace}**! Navigate to Creative Studio to start improving."
        )
        st.balloons()

        # Navigation links
        st.divider()
        nav_col1, nav_col2, nav_col3 = st.columns(3)
        with nav_col1:
            st.page_link(
                "pages/4_Creative_Studio.py",
                label="🎨 Open Creative Studio",
                use_container_width=True,
            )
        with nav_col2:
            st.page_link(
                "pages/5_Creative_Images.py",
                label="🖼️ Open Creative Images",
                use_container_width=True,
            )
        with nav_col3:
            st.page_link(
                "pages/6_Aplus_Studio.py",
                label="🧩 Open A+ Studio",
                use_container_width=True,
            )

    except Exception as exc:
        logger.error("Failed to create improvement project: %s", exc)
        st.error(f"Failed to create improvement project: {exc}")


def _fetch_backend_keywords_from_niche_bank(
    conn: psycopg.Connection,
    asin: str,
    byte_limit: int = 249,
) -> str:
    """Return a deduplicated, byte-capped backend keyword string.

    Pulls keyword phrases from market_intel.niche_keyword_bank ordered by
    search volume.  For each phrase, only the tokens not yet seen are kept.
    Phrases that contribute no new tokens are dropped entirely.  The result
    is packed greedily until ``byte_limit`` UTF-8 bytes would be exceeded.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT keyword
                FROM market_intel.niche_keyword_bank
                WHERE source_asin = %s
                  AND keyword IS NOT NULL
                ORDER BY monthly_search_volume_exact DESC NULLS LAST
                LIMIT 60
                """,
                (asin,),
            )
            rows = cur.fetchall()
        parts: list[str] = []
        seen_tokens: set[str] = set()
        used = 0
        for (kw,) in rows:
            kw = str(kw).strip()
            if not kw:
                continue
            tokens = kw.lower().split()
            new_tokens = [t for t in tokens if t not in seen_tokens]
            if not new_tokens:
                continue
            # Emit only the novel tokens (preserving original casing)
            original_tokens = kw.split()
            novel_original = [
                t for t in original_tokens if t.lower() not in seen_tokens
            ]
            phrase = " ".join(novel_original)
            needed = len(phrase.encode("utf-8")) + (1 if parts else 0)
            if used + needed > byte_limit:
                continue  # try later phrases that may be shorter
            parts.append(phrase)
            seen_tokens.update(t.lower() for t in original_tokens)
            used += needed
        return " ".join(parts)
    except Exception as exc:
        logger.debug("niche_keyword_bank lookup failed for %s: %s", asin, exc)
        return ""


def _save_initial_listing_draft(
    conn: psycopg.Connection,
    launch_id: int,
    marketplace: str,
    title: str,
    bullets: list[str],
    description: str,
    backend_keywords: str = "",
) -> None:
    """Save the current listing as an initial draft for reference."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO launchpad.listing_drafts
                (launch_id, marketplace, title, bullets, description,
                 backend_keywords, rufus_optimized)
            VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)
            """,
            (
                launch_id,
                marketplace,
                title,
                json.dumps(bullets),
                description,
                backend_keywords,
                False,
            ),
        )


def _render_existing_improvements() -> None:
    """Show a list of existing ASIN improvement projects."""
    st.subheader("📂 Existing Improvement Projects")

    try:
        with _open_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT pl.launch_id, pl.source_asin, pl.source_marketplace,
                           pl.launch_name, pl.current_stage, pl.created_at,
                           snap.title AS snapshot_title,
                           snap.brand AS snapshot_brand
                    FROM launchpad.product_launches pl
                    LEFT JOIN launchpad.asin_snapshots snap
                        ON snap.launch_id = pl.launch_id
                    WHERE pl.workflow_type = %s
                      AND COALESCE(pl.is_archived, FALSE) = FALSE
                    ORDER BY pl.created_at DESC
                    LIMIT 20
                    """,
                    (WORKFLOW_ASIN_IMPROVEMENT,),
                )
                improvements = cur.fetchall()

        if not improvements:
            st.info("No improvement projects yet. Import an ASIN below to get started.")
            return

        for imp in improvements:
            lid = imp["launch_id"]
            asin = imp["source_asin"]
            mkt = imp["source_marketplace"]
            name = imp.get("launch_name") or "—"
            brand = imp.get("snapshot_brand") or ""
            title = imp.get("snapshot_title") or ""
            created = imp["created_at"]
            created_str = (
                created.strftime("%Y-%m-%d")
                if hasattr(created, "strftime")
                else str(created)
            )

            with st.container():
                cols = st.columns([1, 2, 3, 1, 1])
                cols[0].markdown(f"**#{lid}**")
                cols[1].markdown(f"`{asin}` ({mkt})")
                cols[2].markdown(f"{name}")
                cols[3].markdown(f"{created_str}")
                with cols[4]:
                    if st.button("Open", key=f"open_imp_{lid}"):
                        st.session_state["selected_launch_id"] = lid
                        st.session_state["cs_selected_launch_id"] = lid
                        st.switch_page("pages/4_Creative_Studio.py")

    except Exception as exc:
        # Gracefully handle the case where the table doesn't exist yet
        if "asin_snapshots" in str(exc) or "workflow_type" in str(exc):
            st.info(
                "Run migration `016_workflow_type_improvement.sql` to enable "
                "the ASIN improvement workflow."
            )
        else:
            st.warning(f"Could not load improvement projects: {exc}")


if __name__ == "__main__":
    main()
