"""
Amazon Launchpad — Main Streamlit entrypoint.

Serves as the home dashboard:
  - Sidebar navigation to the 4 stage pages
  - Active launches table with pursuit category colour-coding
  - New launch creation form
"""

from __future__ import annotations

import os
from datetime import datetime

import psycopg
import streamlit as st
from dotenv import load_dotenv

from services.db_connection import connect, resolve_dsn
from services.launch_state import (
    PURSUIT_GOLDMINE,
    PURSUIT_PROVEN,
    PURSUIT_SATURATED,
    LaunchStateManager,
    _STAGE_NAMES,
)

# ---------------------------------------------------------------------------
# Page configuration — must be the first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Amazon Launchpad",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Environment & DB connection
# ---------------------------------------------------------------------------
load_dotenv()


@st.cache_resource(show_spinner="Connecting to database…")
def get_connection() -> psycopg.Connection | None:
    """Return a cached psycopg connection, or None on failure."""
    try:
        raw_dsn = resolve_dsn("LAUNCHPAD_DB_DSN", "MARKET_INTEL_DSN", "PG_DSN")
        dsn = raw_dsn
        return connect(dsn)
    except Exception as exc:  # noqa: BLE001
        st.error(f"⚠️ Database connection failed: {exc}")
        return None


conn = get_connection()

# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("Amazon Launchpad 🚀")
    st.markdown("---")
    st.caption("Amazon Launchpad v0.1")

# ---------------------------------------------------------------------------
# Helper: colour-coded pursuit category badge
# ---------------------------------------------------------------------------
_PURSUIT_COLORS: dict[str, str] = {
    PURSUIT_SATURATED: "#FF4B4B",  # red
    PURSUIT_PROVEN: "#FF9900",  # orange (brand primary)
    PURSUIT_GOLDMINE: "#21C354",  # green
}

_STAGE_ICONS: dict[int, str] = {
    1: "🔍",
    2: "📋",
    3: "💰",
    4: "🎨",
    5: "✅",
}


def pursuit_badge(category: str | None) -> str:
    """Return an HTML badge for a pursuit category."""
    if not category:
        return "<span style='color:#888'>—</span>"
    color = _PURSUIT_COLORS.get(category, "#888")
    return (
        f"<span style='background:{color};color:white;"
        f"padding:2px 8px;border-radius:4px;font-size:0.85em'>"
        f"{category}</span>"
    )


def stage_badge(stage: int) -> str:
    """Return a formatted stage label."""
    icon = _STAGE_ICONS.get(stage, "❓")
    name = _STAGE_NAMES.get(stage, f"Stage {stage}")
    return f"{icon} {name}"


def launch_label(launch: dict[str, object]) -> str:
    """Return human-friendly launch name or fallback dash."""
    custom_name = str(launch.get("launch_name") or "").strip()
    return custom_name if custom_name else "—"


# ---------------------------------------------------------------------------
# Dashboard header
# ---------------------------------------------------------------------------
st.title("🚀 Amazon Launchpad")
st.markdown(
    "Evaluate, validate, and launch Amazon products across international marketplaces. "
    "Work across four modules: **Opportunity → Compliance → Pricing → Creative** in any order."
)

st.markdown("---")

# ---------------------------------------------------------------------------
# Summary metrics row
# ---------------------------------------------------------------------------
if conn is not None:
    lsm = LaunchStateManager()
    try:
        all_launches = lsm.list_launches(conn, limit=200, include_archived=True)
        conn.commit()

        active_launches = [l for l in all_launches if not bool(l.get("is_archived"))]
        total = len(active_launches)
        goldmine_count = sum(
            1 for l in active_launches if l.get("pursuit_category") == PURSUIT_GOLDMINE
        )
        proven_count = sum(
            1 for l in active_launches if l.get("pursuit_category") == PURSUIT_PROVEN
        )
        saturated_count = sum(
            1 for l in active_launches if l.get("pursuit_category") == PURSUIT_SATURATED
        )

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Active Launches", total)
        col2.metric("🟢 Goldmine", goldmine_count, help="High-opportunity products")
        col3.metric("🟠 Proven", proven_count, help="Moderate-opportunity products")
        col4.metric("🔴 Saturated", saturated_count, help="Low-opportunity products")

    except Exception as exc:  # noqa: BLE001
        st.warning(f"Could not load launch metrics: {exc}")
        conn.rollback()
        all_launches = []
else:
    all_launches = []

st.markdown("---")

# ---------------------------------------------------------------------------
# Create New Launch
# ---------------------------------------------------------------------------
col_header, col_btn = st.columns([6, 2])
col_header.subheader("Launches")

with col_btn:
    create_new = st.button(
        "➕ Create New Launch", use_container_width=True, type="primary"
    )

if create_new:
    st.session_state["show_create_form"] = True

if st.session_state.get("show_create_form"):
    with st.expander("📦 New Launch", expanded=True):
        with st.form("create_launch_form", clear_on_submit=True):
            st.markdown("#### Launch Details")

            form_col1, form_col2 = st.columns(2)

            with form_col1:
                source_asin = st.text_input(
                    "Source ASIN *",
                    placeholder="e.g. B08N5WRWNW",
                    help="The US ASIN you want to evaluate for international expansion.",
                )
                source_marketplace = st.text_input(
                    "Source Marketplace",
                    value="US",
                    disabled=True,
                    help="Source marketplace is locked to US.",
                )

            with form_col2:
                target_marketplaces = st.multiselect(
                    "Target Marketplaces",
                    options=[
                        "UK",
                        "DE",
                        "FR",
                        "IT",
                        "ES",
                        "CA",
                        "AU",
                        "JP",
                        "MX",
                        "IN",
                    ],
                    default=["UK", "DE", "FR", "IT", "ES"],
                    help="Select the marketplaces you want to expand into.",
                )

            product_description = st.text_area(
                "Product Description (optional)",
                placeholder="Brief description of the product…",
                height=80,
            )
            product_category = st.text_input(
                "Product Category (optional)",
                placeholder="e.g. Kitchen & Dining",
            )
            launch_name = st.text_input(
                "Launch Name (optional)",
                placeholder="e.g. Q2 Kitchen Expansion",
                help="Friendly name shown in the dashboard.",
            )

            submitted = st.form_submit_button("🚀 Create Launch", type="primary")

            if submitted:
                if not source_asin.strip():
                    st.error("Source ASIN is required.")
                elif conn is None:
                    st.error("No database connection available.")
                else:
                    try:
                        lsm = LaunchStateManager()
                        launch_id = lsm.create_launch(
                            conn,
                            source_asin=source_asin.strip().upper(),
                            source_marketplace="US",
                            target_marketplaces=target_marketplaces
                            or ["UK", "DE", "FR", "IT", "ES"],
                            launch_name=launch_name.strip() or None,
                            product_description=product_description.strip() or None,
                            product_category=product_category.strip() or None,
                        )
                        conn.commit()
                        st.success(
                            f"✅ Launch **#{launch_id}** created for ASIN `{source_asin.strip().upper()}`!"
                        )
                        st.session_state["show_create_form"] = False
                        st.session_state["selected_launch_id"] = launch_id
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        conn.rollback()
                        st.error(f"Failed to create launch: {exc}")

# ---------------------------------------------------------------------------
# Active launches table
# ---------------------------------------------------------------------------
if conn is None:
    st.warning("⚠️ No database connection. Cannot display launches.")
else:
    if not all_launches:
        st.info("No launches yet. Click **➕ Create New Launch** to get started.")
    else:
        # Filter controls
        filter_col1, filter_col2 = st.columns([3, 1])
        with filter_col1:
            archive_filter = st.selectbox(
                "Filter by status",
                ["Active", "Archived", "All"],
                index=0,
            )
        with filter_col2:
            filter_category = st.selectbox(
                "Filter by category",
                ["All", PURSUIT_GOLDMINE, PURSUIT_PROVEN, PURSUIT_SATURATED],
                index=0,
            )

        filtered = all_launches
        if archive_filter == "Active":
            filtered = [l for l in filtered if not bool(l.get("is_archived"))]
        elif archive_filter == "Archived":
            filtered = [l for l in filtered if bool(l.get("is_archived"))]

        if filter_category != "All":
            filtered = [
                l for l in filtered if l.get("pursuit_category") == filter_category
            ]

        # Build display rows
        rows_html = ""
        for launch in filtered:
            lid = launch["launch_id"]
            asin = launch["source_asin"]
            stage = int(launch["current_stage"])
            launch_name = launch_label(launch)
            category = launch.get("pursuit_category")
            created = launch["created_at"]
            created_str = (
                created.strftime("%Y-%m-%d")
                if hasattr(created, "strftime")
                else str(created)
            )
            archived_badge = "📦 Archived" if bool(launch.get("is_archived")) else ""

            badge = pursuit_badge(category)
            stage_label = stage_badge(stage)

            rows_html += (
                f"<tr>"
                f"<td style='padding:8px'><b>#{lid}</b></td>"
                f"<td style='padding:8px'>{launch_name}</td>"
                f"<td style='padding:8px'><code>{asin}</code></td>"
                f"<td style='padding:8px'>{stage_label}</td>"
                f"<td style='padding:8px'>{badge}</td>"
                f"<td style='padding:8px'>{created_str}</td>"
                f"<td style='padding:8px'>{archived_badge}</td>"
                f"</tr>"
            )

        table_html = f"""
        <table style='width:100%;border-collapse:collapse;font-size:0.9em'>
          <thead>
            <tr style='background:#F0F2F6;text-align:left'>
              <th style='padding:8px'>Launch ID</th>
              <th style='padding:8px'>Launch Name</th>
              <th style='padding:8px'>Source ASIN</th>
              <th style='padding:8px'>Current Stage</th>
              <th style='padding:8px'>Pursuit Category</th>
              <th style='padding:8px'>Created</th>
              <th style='padding:8px'>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
        """
        st.markdown(table_html, unsafe_allow_html=True)

        st.markdown("---")

        # Launch detail selector
        st.subheader("Launch Detail")
        launch_ids = [l["launch_id"] for l in filtered]
        selected_id: int | None = None
        if not launch_ids:
            st.info("No launches match the selected filters.")
        else:
            default_idx = 0
            if st.session_state.get("selected_launch_id") in launch_ids:
                default_idx = launch_ids.index(st.session_state["selected_launch_id"])

            selected_id = st.selectbox(
                "Select a launch to view details",
                options=launch_ids,
                index=default_idx,
                format_func=lambda lid: (
                    f"#{lid} — {next((l['source_asin'] for l in filtered if l['launch_id'] == lid), '')}"
                ),
            )

        if selected_id is not None:
            st.session_state["selected_launch_id"] = selected_id
            try:
                lsm = LaunchStateManager()
                summary = lsm.get_launch_summary(conn, selected_id)
                conn.commit()

                d_col1, d_col2, d_col3 = st.columns(3)
                d_col1.metric("Launch ID", f"#{summary['launch_id']}")
                d_col2.metric("Source ASIN", summary["source_asin"])
                d_col3.metric(
                    "Current Stage",
                    f"{_STAGE_ICONS.get(summary['current_stage'], '')} {summary['current_stage_name']}",
                )

                info_col1, info_col2 = st.columns(2)
                with info_col1:
                    st.markdown(f"**Launch Name:** {launch_label(summary)}")
                    st.markdown(
                        f"**Pursuit Category:** {pursuit_badge(summary.get('pursuit_category'))}",
                        unsafe_allow_html=True,
                    )
                    score = summary.get("pursuit_score")
                    st.markdown(
                        f"**Pursuit Score:** {score if score is not None else '—'}"
                    )
                    st.markdown(
                        f"**Source Marketplace:** {summary['source_marketplace']}"
                    )
                    st.markdown(
                        f"**Target Marketplaces:** {', '.join(summary.get('target_marketplaces') or [])}"
                    )

                with info_col2:
                    if summary.get("is_archived"):
                        archived_at = summary.get("archived_at")
                        if archived_at is not None:
                            archived_str = (
                                archived_at.strftime("%Y-%m-%d %H:%M")
                                if hasattr(archived_at, "strftime")
                                else str(archived_at)
                            )
                        else:
                            archived_str = "yes"
                        st.markdown(f"**Archived:** {archived_str}")
                    st.markdown(f"**Created:** {summary['created_at']}")
                    st.markdown(f"**Updated:** {summary['updated_at']}")
                    if summary.get("product_category"):
                        st.markdown(f"**Category:** {summary['product_category']}")
                    if summary.get("product_description"):
                        st.markdown(
                            f"**Description:** {summary['product_description']}"
                        )

                # Stage advancement status
                if summary["can_advance"]:
                    st.success("✅ Ready to advance to the next stage.")
                else:
                    st.warning("⚠️ Stage blockers:")
                    for blocker in summary["blockers"]:
                        st.markdown(f"- {blocker}")

                st.markdown("---")
                st.markdown("#### Launch Controls")
                ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([3, 2, 2])

                with ctrl_col1:
                    edit_name = st.text_input(
                        "Launch Name",
                        value=str(summary.get("launch_name") or ""),
                        placeholder="Enter a friendly launch name",
                        key=f"launch_name_{selected_id}",
                    )
                with ctrl_col2:
                    if st.button(
                        "💾 Save Name",
                        key=f"save_name_{selected_id}",
                        use_container_width=True,
                    ):
                        updated = lsm.update_launch(
                            conn,
                            selected_id,
                            launch_name=edit_name.strip() or None,
                        )
                        conn.commit()
                        if updated:
                            st.success("Launch name updated.")
                            st.rerun()
                        else:
                            st.warning("Could not update launch name.")

                is_archived = bool(summary.get("is_archived"))
                with ctrl_col3:
                    archive_label = "♻️ Unarchive" if is_archived else "🗄️ Archive"
                    if st.button(
                        archive_label,
                        key=f"toggle_archive_{selected_id}",
                        use_container_width=True,
                    ):
                        updated = lsm.update_launch(
                            conn,
                            selected_id,
                            is_archived=not is_archived,
                            archived_at=datetime.utcnow() if not is_archived else None,
                        )
                        conn.commit()
                        if updated:
                            st.success(
                                "Launch archived."
                                if not is_archived
                                else "Launch restored."
                            )
                            st.rerun()
                        else:
                            st.warning("Could not update archive status.")

            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                st.error(f"Could not load launch details: {exc}")
