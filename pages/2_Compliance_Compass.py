"""
Stage 2: Compliance Compass — Regulatory requirements and checklist tracker.

Identifies CE/UKCA/WEEE/RoHS/ToyEN71/DPP requirements for a product category,
generates a per-launch compliance checklist, and tracks completion status.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import psycopg
import streamlit as st
from dotenv import load_dotenv
from psycopg.rows import dict_row

from services.compliance_engine import ComplianceEngine
from services.db_connection import connect, resolve_dsn
from services.launch_state import STAGE_COMPLIANCE, LaunchStateManager

# ---------------------------------------------------------------------------
# Page configuration — must be the first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Stage 2: Compliance Compass",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

load_dotenv()

# ---------------------------------------------------------------------------
# Regime display config
# ---------------------------------------------------------------------------
_REGIME_CONFIG: dict[str, dict[str, str]] = {
    "CE": {
        "label": "CE — European Conformity",
        "color": "#003399",
        "icon": "🇪🇺",
        "description": "Required for products sold in the European Economic Area.",
    },
    "UKCA": {
        "label": "UKCA — UK Conformity Assessed",
        "color": "#012169",
        "icon": "🇬🇧",
        "description": "Required for products placed on the Great Britain market.",
    },
    "WEEE": {
        "label": "WEEE — Waste Electrical & Electronic Equipment",
        "color": "#2E7D32",
        "icon": "♻️",
        "description": "Applies to electrical/electronic equipment sold in the EU/UK.",
    },
    "RoHS": {
        "label": "RoHS — Restriction of Hazardous Substances",
        "color": "#E65100",
        "icon": "⚗️",
        "description": "Restricts hazardous substances in electrical/electronic equipment.",
    },
    "ToyEN71": {
        "label": "ToyEN71 — Toy Safety (EN 71)",
        "color": "#6A1B9A",
        "icon": "🧸",
        "description": "Safety standard for toys sold in the EU/UK.",
    },
    "DPP": {
        "label": "DPP — Digital Product Passport (2026)",
        "color": "#00695C",
        "icon": "📱",
        "description": "EU Digital Product Passport — mandatory from 2026 for certain categories.",
    },
}

_STATUS_CONFIG: dict[str, dict[str, str]] = {
    "pending": {"label": "Pending", "color": "#888888", "icon": "⏳"},
    "in_progress": {"label": "In Progress", "color": "#FF9900", "icon": "🔄"},
    "completed": {"label": "Completed", "color": "#21C354", "icon": "✅"},
    "not_applicable": {"label": "Not Applicable", "color": "#AAAAAA", "icon": "➖"},
    "blocked": {"label": "Blocked", "color": "#FF4B4B", "icon": "🚫"},
}

_STATUS_OPTIONS = ["pending", "in_progress", "completed", "not_applicable", "blocked"]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Connecting to database…")
def get_connection() -> psycopg.Connection | None:
    """Return a cached psycopg connection, or None on failure."""
    try:
        raw_dsn = resolve_dsn("LAUNCHPAD_DB_DSN", "MARKET_INTEL_DSN", "PG_DSN")
        return connect(raw_dsn)
    except Exception as exc:  # noqa: BLE001
        st.error(f"⚠️ Database connection failed: {exc}")
        return None


def load_all_launches(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Load all product launches ordered by created_at DESC."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT launch_id, source_asin, source_marketplace,
                   product_description, product_category,
                   pursuit_score, pursuit_category, current_stage,
                   created_at
            FROM launchpad.product_launches
            ORDER BY created_at DESC
            LIMIT 100
            """
        )
        return list(cur.fetchall())


def load_compliance_rules(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Load all compliance rules from the database."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT rule_id, regime, category_pattern, requirement_name,
                   requirement_description, documentation_required,
                   is_2026_dpp_relevant, effective_date, source_url
            FROM launchpad.compliance_rules
            ORDER BY regime, requirement_name
            """
        )
        return list(cur.fetchall())


def load_checklist_for_launch(
    conn: psycopg.Connection, launch_id: int
) -> list[dict[str, Any]]:
    """Load existing checklist items for a launch, joined with rule details."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                c.checklist_id,
                c.launch_id,
                c.rule_id,
                c.status,
                c.evidence_url,
                c.notes,
                c.completed_at,
                r.regime,
                r.requirement_name,
                r.requirement_description,
                r.documentation_required,
                r.is_2026_dpp_relevant,
                r.effective_date,
                r.source_url
            FROM launchpad.launch_compliance_checklist c
            JOIN launchpad.compliance_rules r ON r.rule_id = c.rule_id
            WHERE c.launch_id = %s
            ORDER BY r.regime, r.requirement_name
            """,
            (launch_id,),
        )
        return list(cur.fetchall())


def upsert_checklist_items(
    conn: psycopg.Connection,
    launch_id: int,
    items: list[dict[str, Any]],
) -> None:
    """Insert checklist items, ignoring duplicates (launch_id, rule_id unique)."""
    with conn.cursor() as cur:
        for item in items:
            cur.execute(
                """
                INSERT INTO launchpad.launch_compliance_checklist
                    (launch_id, rule_id, status, evidence_url, notes)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (launch_id, rule_id) DO NOTHING
                """,
                (
                    launch_id,
                    item["rule_id"],
                    item.get("status", "pending"),
                    item.get("evidence_url"),
                    item.get("notes"),
                ),
            )
    conn.commit()


def update_checklist_item(
    conn: psycopg.Connection,
    checklist_id: int,
    status: str,
    evidence_url: str | None,
    notes: str | None,
) -> None:
    """Update a single checklist item's status, evidence, and notes."""
    completed_at = "now()" if status == "completed" else "NULL"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE launchpad.launch_compliance_checklist
            SET status = %s,
                evidence_url = %s,
                notes = %s,
                completed_at = {completed_at}
            WHERE checklist_id = %s
            """,
            (status, evidence_url or None, notes or None, checklist_id),
        )
    conn.commit()


def update_product_category(
    conn: psycopg.Connection, launch_id: int, category: str
) -> None:
    """Update the product_category on a launch."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE launchpad.product_launches
            SET product_category = %s, updated_at = now()
            WHERE launch_id = %s
            """,
            (category, launch_id),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------
def regime_badge(regime: str) -> str:
    cfg = _REGIME_CONFIG.get(regime, {"label": regime, "color": "#888", "icon": "📋"})
    return (
        f"<span style='background:{cfg['color']};color:white;"
        f"padding:2px 10px;border-radius:12px;font-size:0.8em;font-weight:600'>"
        f"{cfg['icon']} {cfg['label']}</span>"
    )


def status_badge(status: str) -> str:
    cfg = _STATUS_CONFIG.get(status, {"label": status, "color": "#888", "icon": "❓"})
    return (
        f"<span style='background:{cfg['color']};color:white;"
        f"padding:2px 8px;border-radius:4px;font-size:0.8em'>"
        f"{cfg['icon']} {cfg['label']}</span>"
    )


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------
conn = get_connection()

st.title("🧭 Stage 2: Compliance Compass")
st.markdown(
    "Identify regulatory requirements for your product category and track compliance tasks. "
    "All requirements must be completed or marked N/A before advancing to Stage 3."
)
st.markdown("---")

if conn is None:
    st.error("⚠️ No database connection. Please check your environment configuration.")
    st.stop()

# ---------------------------------------------------------------------------
# Launch selection
# ---------------------------------------------------------------------------
st.subheader("Select Launch")

try:
    launches = load_all_launches(conn)
    conn.commit()
except Exception as exc:
    conn.rollback()
    st.error(f"Failed to load launches: {exc}")
    st.stop()

if not launches:
    st.info("No launches found. Create a launch on the Dashboard first.")
    st.stop()

# Build display options — only show launches that have completed Stage 1
stage1_complete = [
    l for l in launches if l.get("pursuit_score") is not None
]
all_option = launches  # allow selecting any, but warn if Stage 1 incomplete

launch_options = {
    l["launch_id"]: (
        f"#{l['launch_id']} — {l['source_asin']} "
        f"| {l.get('product_category') or 'No category'} "
        f"| Stage {l['current_stage']}"
    )
    for l in launches
}

# Restore previously selected launch if available
default_launch_id = st.session_state.get("selected_launch_id")
launch_ids = list(launch_options.keys())
default_idx = 0
if default_launch_id in launch_ids:
    default_idx = launch_ids.index(default_launch_id)

selected_launch_id = st.selectbox(
    "Active Launch",
    options=launch_ids,
    index=default_idx,
    format_func=lambda lid: launch_options[lid],
)
st.session_state["selected_launch_id"] = selected_launch_id

# Load selected launch details
selected_launch = next(
    (l for l in launches if l["launch_id"] == selected_launch_id), None
)

if selected_launch is None:
    st.error("Could not load selected launch.")
    st.stop()

# Stage 1 validation
if selected_launch.get("pursuit_score") is None:
    st.warning(
        "⚠️ **Stage 1 not complete.** This launch has no pursuit score yet. "
        "Please complete Stage 1: Opportunity Validator before proceeding."
    )
    st.info(
        f"**Launch #{selected_launch_id}** — ASIN: `{selected_launch['source_asin']}` "
        f"| Current Stage: {selected_launch['current_stage']}"
    )
    st.stop()

# Show launch summary
info_col1, info_col2, info_col3 = st.columns(3)
info_col1.metric("Launch ID", f"#{selected_launch_id}")
info_col2.metric("Source ASIN", selected_launch["source_asin"])
info_col3.metric("Pursuit Score", f"{selected_launch['pursuit_score']:.1f}" if selected_launch.get("pursuit_score") else "—")

desc_col1, desc_col2 = st.columns(2)
with desc_col1:
    st.markdown(f"**Pursuit Category:** {selected_launch.get('pursuit_category') or '—'}")
    st.markdown(f"**Current Stage:** {selected_launch['current_stage']}")
with desc_col2:
    if selected_launch.get("product_description"):
        st.markdown(f"**Description:** {selected_launch['product_description']}")

st.markdown("---")

# ---------------------------------------------------------------------------
# Product category analysis
# ---------------------------------------------------------------------------
st.subheader("📦 Product Category")

current_category = selected_launch.get("product_category") or ""

with st.expander("Edit Product Category", expanded=not bool(current_category)):
    new_category = st.text_input(
        "Product Category",
        value=current_category,
        placeholder="e.g. Electronic Toys, Kitchen Appliances, Clothing…",
        help="Used to match applicable compliance regimes. Be specific.",
        key="category_input",
    )
    if st.button("💾 Save Category", key="save_category"):
        if new_category.strip():
            try:
                update_product_category(conn, selected_launch_id, new_category.strip())
                st.success(f"✅ Category updated to: **{new_category.strip()}**")
                current_category = new_category.strip()
                st.rerun()
            except Exception as exc:
                conn.rollback()
                st.error(f"Failed to update category: {exc}")
        else:
            st.warning("Category cannot be empty.")

if current_category:
    st.markdown(f"**Current category:** `{current_category}`")
else:
    st.warning("⚠️ No product category set. Please set a category to analyse compliance requirements.")
    st.stop()

# ---------------------------------------------------------------------------
# Compliance rules matching & checklist generation
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("🔍 Compliance Requirements Analysis")

engine = ComplianceEngine()

# Check DPP relevance
dpp_relevant = engine.is_dpp_relevant(current_category, {})
if dpp_relevant:
    st.warning(
        "📱 **DPP 2026 Alert:** This product category may fall under the EU Digital Product Passport "
        "regulation, mandatory from 2026. DPP requirements are included in the checklist below."
    )

# Load existing checklist
try:
    existing_checklist = load_checklist_for_launch(conn, selected_launch_id)
    conn.commit()
except Exception as exc:
    conn.rollback()
    st.error(f"Failed to load checklist: {exc}")
    existing_checklist = []

# Analyse button
col_analyze, col_info = st.columns([2, 4])
with col_analyze:
    analyze_clicked = st.button(
        "🔎 Analyse Compliance Requirements",
        type="primary",
        use_container_width=True,
        key="analyze_btn",
    )

with col_info:
    if existing_checklist:
        st.info(
            f"ℹ️ Checklist already has **{len(existing_checklist)} item(s)**. "
            "Re-analysing will add any new matching rules (existing items are preserved)."
        )
    else:
        st.info("Click **Analyse** to match compliance rules for this product category.")

if analyze_clicked:
    try:
        rules = load_compliance_rules(conn)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        st.error(f"Failed to load compliance rules: {exc}")
        rules = []

    if not rules:
        st.warning(
            "⚠️ No compliance rules found in the database. "
            "Please run the seed script: `python scripts/seed_compliance_rules.py`"
        )
    else:
        matched = engine.match_rules_for_product(current_category, {}, rules)

        if not matched:
            st.warning(
                f"No compliance rules matched for category **'{current_category}'**. "
                "Try a more specific category (e.g. 'Electronic Toys', 'Kitchen Appliances')."
            )
        else:
            # Generate checklist items
            checklist_items = engine.generate_checklist(
                selected_launch_id, current_category, {}, rules
            )

            try:
                upsert_checklist_items(conn, selected_launch_id, checklist_items)
                # Reload
                existing_checklist = load_checklist_for_launch(conn, selected_launch_id)
                conn.commit()

                # Count by regime
                regime_counts: dict[str, int] = {}
                for item in checklist_items:
                    r = item.get("regime", "Unknown")
                    regime_counts[r] = regime_counts.get(r, 0) + 1

                st.success(
                    f"✅ Found **{len(matched)} applicable rule(s)** across "
                    f"**{len(regime_counts)} regime(s)**. Checklist updated."
                )
                for regime, count in sorted(regime_counts.items()):
                    cfg = _REGIME_CONFIG.get(regime, {"icon": "📋", "label": regime})
                    st.markdown(f"- {cfg['icon']} **{cfg['label']}**: {count} requirement(s)")

            except Exception as exc:
                conn.rollback()
                st.error(f"Failed to save checklist: {exc}")

# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------
if existing_checklist:
    st.markdown("---")
    st.subheader("📊 Compliance Progress")

    progress = engine.calculate_compliance_progress(existing_checklist)

    # Progress bar
    pct = progress["completion_pct"] / 100.0
    st.progress(pct, text=f"Overall completion: {progress['completion_pct']:.1f}%")

    # Metrics row
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Total", progress["total"])
    m2.metric("✅ Completed", progress["completed"])
    m3.metric("🔄 In Progress", progress["in_progress"])
    m4.metric("⏳ Pending", progress["pending"])
    m5.metric("🚫 Blocked", progress["blocked"])
    m6.metric("➖ N/A", progress["not_applicable"])

    # Blocked warning
    if progress["blocked"] > 0:
        st.error(
            f"🚫 **{progress['blocked']} item(s) are BLOCKED.** "
            "Blocked items must be resolved or marked Not Applicable before advancing."
        )

    # Next action
    next_action = engine.get_next_action(existing_checklist)
    st.info(f"💡 **Next action:** {next_action}")

    # DPP 2026 warning
    dpp_items = [i for i in existing_checklist if i.get("is_2026_dpp_relevant")]
    if dpp_items:
        dpp_pending = [i for i in dpp_items if i.get("status") in ("pending", "in_progress")]
        if dpp_pending:
            st.warning(
                f"📱 **DPP 2026:** {len(dpp_pending)} Digital Product Passport requirement(s) "
                "are not yet completed. These become mandatory in 2026."
            )

    # ---------------------------------------------------------------------------
    # Checklist display — grouped by regime
    # ---------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("📋 Compliance Checklist")

    # Group items by regime
    items_by_regime: dict[str, list[dict[str, Any]]] = {}
    for item in existing_checklist:
        regime = item.get("regime") or "Unknown"
        items_by_regime.setdefault(regime, []).append(item)

    # Display in regime order
    for regime in ComplianceEngine.ALL_REGIMES:
        if regime not in items_by_regime:
            continue

        items = items_by_regime[regime]
        cfg = _REGIME_CONFIG.get(regime, {"label": regime, "color": "#888", "icon": "📋", "description": ""})

        # Regime summary counts
        regime_completed = sum(1 for i in items if i.get("status") == "completed")
        regime_blocked = sum(1 for i in items if i.get("status") == "blocked")
        regime_pending = sum(1 for i in items if i.get("status") == "pending")

        # Regime header with status summary
        expander_label = (
            f"{cfg['icon']} {cfg['label']} "
            f"({regime_completed}/{len(items)} complete"
            + (f", 🚫 {regime_blocked} blocked" if regime_blocked else "")
            + (f", ⏳ {regime_pending} pending" if regime_pending else "")
            + ")"
        )

        with st.expander(expander_label, expanded=(regime_blocked > 0 or regime_pending > 0)):
            st.markdown(
                f"<span style='color:{cfg['color']};font-size:0.9em'>{cfg['description']}</span>",
                unsafe_allow_html=True,
            )
            st.markdown("")

            for item in items:
                checklist_id = item["checklist_id"]
                req_name = item.get("requirement_name") or "Unnamed Requirement"
                req_desc = item.get("requirement_description") or ""
                docs_required = item.get("documentation_required") or []
                current_status = item.get("status") or "pending"
                current_evidence = item.get("evidence_url") or ""
                current_notes = item.get("notes") or ""
                effective_date = item.get("effective_date")
                source_url = item.get("source_url")
                is_dpp = item.get("is_2026_dpp_relevant", False)

                # Item header
                status_cfg = _STATUS_CONFIG.get(current_status, {"icon": "❓", "color": "#888"})
                item_header = f"{status_cfg['icon']} **{req_name}**"
                if is_dpp:
                    item_header += " 📱 *DPP 2026*"

                st.markdown(item_header)

                item_col1, item_col2 = st.columns([3, 2])

                with item_col1:
                    if req_desc:
                        st.markdown(f"<small>{req_desc}</small>", unsafe_allow_html=True)

                    if docs_required:
                        st.markdown("**Required documents:**")
                        for doc in docs_required:
                            st.markdown(f"  - 📄 {doc}")

                    if effective_date:
                        st.markdown(f"<small>📅 Effective: {effective_date}</small>", unsafe_allow_html=True)

                    if source_url:
                        st.markdown(f"<small>🔗 [Reference]({source_url})</small>", unsafe_allow_html=True)

                with item_col2:
                    # Status dropdown
                    status_idx = _STATUS_OPTIONS.index(current_status) if current_status in _STATUS_OPTIONS else 0
                    new_status = st.selectbox(
                        "Status",
                        options=_STATUS_OPTIONS,
                        index=status_idx,
                        format_func=lambda s: f"{_STATUS_CONFIG[s]['icon']} {_STATUS_CONFIG[s]['label']}",
                        key=f"status_{checklist_id}",
                        label_visibility="collapsed",
                    )

                    # Evidence URL
                    new_evidence = st.text_input(
                        "Evidence URL",
                        value=current_evidence,
                        placeholder="https://… (optional)",
                        key=f"evidence_{checklist_id}",
                        label_visibility="visible",
                    )

                    # Notes
                    new_notes = st.text_area(
                        "Notes",
                        value=current_notes,
                        placeholder="Add notes, blockers, or context…",
                        height=80,
                        key=f"notes_{checklist_id}",
                        label_visibility="visible",
                    )

                    # Auto-save on change
                    if (
                        new_status != current_status
                        or new_evidence != current_evidence
                        or new_notes != current_notes
                    ):
                        try:
                            update_checklist_item(
                                conn,
                                checklist_id,
                                new_status,
                                new_evidence,
                                new_notes,
                            )
                            st.success("💾 Saved", icon="✅")
                        except Exception as exc:
                            conn.rollback()
                            st.error(f"Save failed: {exc}")

                st.markdown("---")

    # Handle any items with unknown regimes
    unknown_items = items_by_regime.get("Unknown", [])
    if unknown_items:
        with st.expander(f"❓ Unknown Regime ({len(unknown_items)} items)"):
            for item in unknown_items:
                st.markdown(f"- {item.get('requirement_name', 'Unnamed')}")

    # ---------------------------------------------------------------------------
    # Documentation section
    # ---------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("📁 Documentation Tracker")

    # Collect all required documents across all items
    all_required_docs: dict[str, list[str]] = {}  # doc_name -> [requirement_names]
    for item in existing_checklist:
        docs = item.get("documentation_required") or []
        req_name = item.get("requirement_name") or "Unknown"
        for doc in docs:
            all_required_docs.setdefault(doc, []).append(req_name)

    # Collect uploaded evidence URLs
    uploaded_evidence = [
        i.get("evidence_url")
        for i in existing_checklist
        if i.get("evidence_url")
    ]

    if all_required_docs:
        doc_col1, doc_col2 = st.columns([3, 1])
        doc_col1.markdown(f"**{len(all_required_docs)} unique document type(s) required across all regimes:**")
        doc_col2.metric("Evidence URLs uploaded", len(uploaded_evidence))

        for doc_name, req_names in sorted(all_required_docs.items()):
            # Check if any item with this doc has evidence
            has_evidence = any(
                i.get("evidence_url")
                for i in existing_checklist
                if doc_name in (i.get("documentation_required") or [])
            )
            icon = "✅" if has_evidence else "📄"
            with st.expander(f"{icon} {doc_name}", expanded=False):
                st.markdown(f"Required by: {', '.join(req_names)}")
                if has_evidence:
                    st.success("Evidence URL has been provided for at least one related requirement.")
                else:
                    st.warning("No evidence URL uploaded yet. Add it in the checklist item above.")
    else:
        st.info("No specific documents required, or checklist not yet generated.")

    # ---------------------------------------------------------------------------
    # Stage completion
    # ---------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("🚀 Stage Completion")

    lsm = LaunchStateManager()
    try:
        can_advance, blockers = lsm.can_advance_stage(conn, selected_launch_id)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        can_advance = False
        blockers = [str(exc)]

    current_stage = int(selected_launch["current_stage"])

    if current_stage > STAGE_COMPLIANCE:
        st.success(
            f"✅ Stage 2 already completed. This launch is at Stage {current_stage}."
        )
    elif current_stage < STAGE_COMPLIANCE:
        st.warning(
            f"⚠️ This launch is at Stage {current_stage}. "
            "Complete Stage 1 first before working on compliance."
        )
    else:
        # Currently at Stage 2
        if can_advance:
            st.success(
                "✅ All compliance requirements are completed or marked N/A. "
                "Ready to advance to **Stage 3: Risk & Pricing Architect**."
            )

            # Confirmation dialog via session state
            if st.button(
                "🚀 Complete Stage 2 — Advance to Pricing",
                type="primary",
                key="complete_stage2",
            ):
                st.session_state["confirm_stage2"] = True

            if st.session_state.get("confirm_stage2"):
                st.warning(
                    "⚠️ **Confirm Stage Completion**\n\n"
                    "Advancing to Stage 3 will lock the compliance checklist. "
                    "Are you sure all requirements are correctly recorded?"
                )
                confirm_col1, confirm_col2 = st.columns(2)
                with confirm_col1:
                    if st.button("✅ Yes, advance to Stage 3", key="confirm_yes", type="primary"):
                        try:
                            advanced = lsm.advance_stage(conn, selected_launch_id)
                            conn.commit()
                            if advanced:
                                st.success(
                                    "🎉 **Stage 2 Complete!** Launch advanced to "
                                    "**Stage 3: Risk & Pricing Architect**."
                                )
                                st.session_state["confirm_stage2"] = False
                                st.balloons()
                                st.rerun()
                            else:
                                st.error("Could not advance stage. Please try again.")
                        except Exception as exc:
                            conn.rollback()
                            st.error(f"Failed to advance stage: {exc}")
                with confirm_col2:
                    if st.button("❌ Cancel", key="confirm_no"):
                        st.session_state["confirm_stage2"] = False
                        st.rerun()
        else:
            st.error("❌ Cannot advance to Stage 3 yet. Resolve the following:")
            for blocker in blockers:
                st.markdown(f"- 🚫 {blocker}")

            # Show summary of what's blocking
            if progress["pending"] > 0:
                st.markdown(
                    f"**{progress['pending']} item(s) still pending** — "
                    "update their status to Completed or Not Applicable."
                )
            if progress["blocked"] > 0:
                st.markdown(
                    f"**{progress['blocked']} item(s) are blocked** — "
                    "resolve the blocker or mark as Not Applicable."
                )
            if progress["in_progress"] > 0:
                st.markdown(
                    f"**{progress['in_progress']} item(s) in progress** — "
                    "these must be completed before advancing."
                )

else:
    # No checklist yet
    st.markdown("---")
    st.info(
        "📋 No compliance checklist generated yet. "
        "Click **Analyse Compliance Requirements** above to get started."
    )
