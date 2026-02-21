"""
Stage 2: Compliance Compass — Regulatory requirements and checklist tracker.

Identifies CE/UKCA/WEEE/RoHS/ToyEN71/DPP requirements for a product category,
generates a per-launch compliance checklist, and tracks completion status.
"""

from __future__ import annotations

import logging
import os
import json
from datetime import date, datetime
from typing import Any

import psycopg
import streamlit as st
from dotenv import load_dotenv
from psycopg.rows import dict_row

from services.compliance_engine import ComplianceEngine
from services.compliance_profile import ProductProfile
from services.db_connection import connect, resolve_dsn
from services.launch_state import STAGE_COMPLIANCE, LaunchStateManager
from services.product_profiler import infer_product_profile
from services.workflow_ui import (
    record_section_save,
    render_readiness_panel,
    render_section_save_status,
)

# ---------------------------------------------------------------------------
# Page configuration — must be the first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Module 2: Compliance Compass",
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

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Electronic Toys": [
        "electronic toy",
        "robot toy",
        "rc car",
        "remote control toy",
        "drone toy",
        "tech toy",
        "toy car",
        "toy robot",
        "interactive toy",
    ],
    "Electronics": [
        "electronic",
        "electrical",
        "gadget",
        "usb",
        "bluetooth",
        "wireless",
        "smart home",
        "led light",
        "charger",
        "adapter",
        "power bank",
        "speaker",
        "headphone",
        "earphone",
        "camera",
        "monitor",
    ],
    "Toys & Games": [
        "toy",
        "game",
        "puzzle",
        "doll",
        "action figure",
        "plush",
        "building block",
        "board game",
        "play set",
        "stuffed animal",
    ],
    "Kitchen Appliances": [
        "kitchen appliance",
        "blender",
        "mixer",
        "toaster",
        "kettle",
        "coffee maker",
        "air fryer",
        "food processor",
        "microwave",
    ],
    "Home & Kitchen": [
        "home",
        "cookware",
        "utensil",
        "storage container",
        "organizer",
    ],
    "Clothing & Apparel": [
        "clothing",
        "apparel",
        "shirt",
        "dress",
        "jacket",
        "pants",
        "trousers",
        "shoes",
        "boots",
        "fashion",
        "garment",
    ],
    "Textiles": [
        "textile",
        "fabric",
        "linen",
        "curtain",
        "towel",
        "bedding",
    ],
    "Furniture": [
        "furniture",
        "chair",
        "table",
        "desk",
        "shelf",
        "cabinet",
        "sofa",
    ],
    "Beauty & Personal Care": [
        "beauty",
        "skincare",
        "cosmetic",
        "makeup",
        "shampoo",
        "lotion",
    ],
    "Baby Products": [
        "baby",
        "infant",
        "newborn",
        "nursery",
        "stroller",
        "car seat",
    ],
    "Sports & Outdoors": [
        "sport",
        "outdoor",
        "fitness",
        "exercise",
        "camping",
        "hiking",
    ],
    "Lighting": [
        "lamp",
        "light",
        "bulb",
        "lighting",
        "chandelier",
        "led strip",
    ],
    "Batteries & Chargers": [
        "battery",
        "batteries",
        "charger",
        "charging",
        "power supply",
    ],
}

_REGIME_PACKAGING_INFO: dict[str, list[str]] = {
    "CE": [
        "CE mark affixed visibly, legibly, and indelibly on product/packaging",
        "EU Declaration of Conformity (DoC) included or accessible via URL/QR",
        "Manufacturer name, registered trade name/trademark, and postal address",
        "EU Authorised Representative details (for non-EU manufacturers)",
        "Product identification (type, batch, serial number)",
    ],
    "UKCA": [
        "UKCA mark displayed on product or packaging",
        "UK Declaration of Conformity document",
        "UK Responsible Person name and GB postal address",
        "Importer details if manufacturer is outside UK",
    ],
    "WEEE": [
        "Crossed-out wheelie bin symbol on product and packaging",
        "Producer registration number on packaging",
        "Separate collection instructions for end-of-life disposal",
    ],
    "RoHS": [
        "RoHS compliance declaration or CE marking (covers RoHS for EEE)",
        "Material composition documentation available on request",
        "No restricted substances above threshold limits in packaging materials",
    ],
    "ToyEN71": [
        "CE mark on toy and packaging (mandatory for EU toy safety)",
        "Age warning labels (e.g. 'Not suitable for children under 3 years')",
        "Choking hazard warnings where applicable",
        "Manufacturer/importer identification on packaging",
        "EN 71 test report reference",
    ],
    "DPP": [
        "Digital Product Passport data carrier (QR code) on product/packaging",
        "Unique product identifier linked to DPP registry",
        "Sustainability and circularity information accessible via DPP",
        "Material composition and recyclability data",
        "Carbon footprint declaration (where applicable)",
    ],
}

logger = logging.getLogger(__name__)


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
    """Load active (non-archived) launches ordered by created_at DESC."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT launch_id, source_asin, source_marketplace,
                   launch_name,
                   product_description, product_category,
                   pursuit_score, pursuit_category, current_stage,
                    created_at
            FROM launchpad.product_launches
            WHERE COALESCE(is_archived, FALSE) = FALSE
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


def _suggest_category(description: str) -> str | None:
    if not description:
        return None
    desc_lower = description.lower()
    best_match: str | None = None
    best_score = 0
    for category, keywords in _CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in desc_lower)
        if score > best_score:
            best_score = score
            best_match = category
    return best_match if best_score > 0 else None


def _extract_category_from_keywords_payload(payload: Any) -> str | None:
    """Best-effort extraction of category from cached keywords_by_asin payload."""
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    rows = payload.get("data", [])
    if not isinstance(rows, list):
        return None

    category_counts: dict[str, int] = {}
    keyword_tokens: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        attrs = row.get("attributes", {})
        if not isinstance(attrs, dict):
            continue

        for key in (
            "dominant_category",
            "category",
            "product_category",
            "root_category",
        ):
            value = str(attrs.get(key) or "").strip()
            if value:
                category_counts[value] = category_counts.get(value, 0) + 1

        name = str(attrs.get("name") or "").strip()
        if name:
            keyword_tokens.append(name)

    if category_counts:
        return sorted(category_counts.items(), key=lambda item: item[1], reverse=True)[
            0
        ][0]

    if keyword_tokens:
        return _suggest_category(" ".join(keyword_tokens))

    return None


def _suggest_category_from_cached_js(
    conn: psycopg.Connection,
    source_asin: str,
    source_marketplace: str,
) -> str | None:
    """Infer category from cached Jungle Scout keywords_by_asin responses."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT response_data
            FROM launchpad.jungle_scout_cache
            WHERE asin = %s
              AND marketplace = %s
              AND endpoint = 'keywords_by_asin'
              AND (expires_at IS NULL OR expires_at > now())
            ORDER BY fetched_at DESC
            LIMIT 5
            """,
            (source_asin, source_marketplace),
        )
        rows = list(cur.fetchall())

    for row in rows:
        inferred = _extract_category_from_keywords_payload(row.get("response_data"))
        if inferred:
            return inferred

    return None


def _render_risk_assessment_display(assessment: dict[str, Any]) -> None:
    level = assessment.get("overall_risk_level", "unknown")
    level_colors = {
        "low": "#21C354",
        "medium": "#FF9900",
        "high": "#FF4B4B",
        "critical": "#CC0000",
    }
    level_icons = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}
    color = level_colors.get(level, "#888888")

    st.markdown(
        f"**Overall Risk Level:** "
        f"<span style='background:{color};color:white;padding:2px 12px;"
        f"border-radius:4px;font-weight:bold;text-transform:uppercase'>"
        f"{level_icons.get(level, '⚪')} {level}</span>",
        unsafe_allow_html=True,
    )
    st.markdown(assessment.get("summary", ""))

    for risk in assessment.get("risks", []):
        severity = risk.get("severity", "unknown")
        sev_icon = level_icons.get(severity, "⚪")
        expanded = severity in ("high", "critical")

        with st.expander(
            f"{sev_icon} {risk.get('risk_name', 'Unknown Risk')} — {severity.upper()}",
            expanded=expanded,
        ):
            st.markdown(risk.get("description", ""))
            refs = risk.get("regime_references", [])
            if refs:
                ref_badges = " ".join(
                    regime_badge(r) for r in refs if r in _REGIME_CONFIG
                )
                if ref_badges:
                    st.markdown(f"**Regimes:** {ref_badges}", unsafe_allow_html=True)
            mitigations = risk.get("mitigations", [])
            if mitigations:
                st.markdown("**Mitigations:**")
                for m in mitigations:
                    st.markdown(f"- {m}")

    actions = assessment.get("recommended_priority_actions", [])
    if actions:
        st.markdown("**Priority Actions:**")
        for i, action in enumerate(actions, 1):
            st.markdown(f"{i}. {action}")


def _build_compliance_audit_report(
    launch: dict[str, Any],
    product_category: str,
    selected_regimes: list[str],
    risk_assessment: dict[str, Any] | None,
    checklist_items: list[dict[str, Any]],
    required_docs: dict[str, set[str]],
) -> str:
    """Build a markdown audit report for compliance review."""
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")
    launch_name = str(launch.get("launch_name") or "").strip()
    launch_title = (
        f"Launch #{launch.get('launch_id')} - {launch_name}"
        if launch_name
        else f"Launch #{launch.get('launch_id')}"
    )
    lines = [
        f"# Compliance Risk Assessment Report - {launch_title}",
        "",
        "## Audit Metadata",
        f"- Generated at (UTC): {generated_at}",
        f"- Opportunity Name: {launch_name or 'N/A'}",
        f"- Source ASIN: {launch.get('source_asin') or 'N/A'}",
        f"- Source Marketplace: {launch.get('source_marketplace') or 'US'}",
        f"- Product Description: {launch.get('product_description') or 'N/A'}",
        f"- Product Category: {product_category or 'N/A'}",
        f"- Pursuit Score: {launch.get('pursuit_score') or 'N/A'}",
        f"- Pursuit Category: {launch.get('pursuit_category') or 'N/A'}",
        f"- Selected Regimes: {', '.join(selected_regimes) if selected_regimes else 'N/A'}",
        "",
        "## AI Risk Assessment",
    ]

    if risk_assessment:
        level = str(risk_assessment.get("overall_risk_level") or "unknown").upper()
        lines.append(f"- Overall Risk Level: {level}")
        summary = str(risk_assessment.get("summary") or "").strip()
        if summary:
            lines.append(f"- Summary: {summary}")
        lines.append("")

        risks = risk_assessment.get("risks") or []
        if isinstance(risks, list) and risks:
            lines.append("### Risk Breakdown")
            for idx, risk in enumerate(risks, 1):
                if not isinstance(risk, dict):
                    continue
                name = str(risk.get("risk_name") or f"Risk {idx}")
                severity = str(risk.get("severity") or "unknown").upper()
                desc = str(risk.get("description") or "").strip()
                regimes = risk.get("regime_references") or []
                mitigations = risk.get("mitigations") or []
                lines.append(f"{idx}. {name} ({severity})")
                if desc:
                    lines.append(f"   - Description: {desc}")
                if isinstance(regimes, list) and regimes:
                    lines.append(f"   - Regimes: {', '.join(str(r) for r in regimes)}")
                if isinstance(mitigations, list) and mitigations:
                    lines.append("   - Mitigations:")
                    for mitigation in mitigations:
                        lines.append(f"     - {mitigation}")
            lines.append("")

        actions = risk_assessment.get("recommended_priority_actions") or []
        if isinstance(actions, list) and actions:
            lines.append("### Priority Actions")
            for i, action in enumerate(actions, 1):
                lines.append(f"{i}. {action}")
            lines.append("")
    else:
        lines.append("- No AI risk assessment has been generated for this launch yet.")
        lines.append("")

    lines.extend(
        [
            "## Compliance Checklist",
            "",
            "| Regime | Requirement | Status | Evidence URL | Notes |",
            "|---|---|---|---|---|",
        ]
    )

    for item in checklist_items:
        regime = str(item.get("regime") or "Unknown")
        req = str(item.get("requirement_name") or "Unnamed Requirement")
        status = str(item.get("status") or "pending")
        evidence = str(item.get("evidence_url") or "").strip() or "-"
        notes = str(item.get("notes") or "").strip() or "-"
        safe_req = req.replace("|", "\\|")
        safe_notes = notes.replace("|", "\\|")
        lines.append(
            f"| {regime} | {safe_req} | {status} | {evidence} | {safe_notes} |"
        )

    lines.extend(["", "## Required Documentation"])
    if required_docs:
        for doc_name, req_names in sorted(required_docs.items()):
            requirements = ", ".join(sorted(req_names)) if req_names else "N/A"
            lines.append(f"- {doc_name}: required by {requirements}")
    else:
        lines.append("- No required documentation identified.")

    lines.append("")
    return "\n".join(lines)


def _filename_safe_token(value: str, max_len: int = 48) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned[:max_len].strip("_")


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------
conn = get_connection()

st.title("🧭 Module 2: Compliance Compass")
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

# Build display options for all launches (non-blocking workflow)

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

selected_launch_raw = st.selectbox(
    "Active Launch",
    options=launch_ids,
    index=default_idx,
    format_func=lambda lid: launch_options[lid],
)
if selected_launch_raw is None:
    st.error("No launch selected.")
    st.stop()
selected_launch_id = int(selected_launch_raw)
st.session_state["selected_launch_id"] = selected_launch_id

# Load selected launch details
selected_launch = next(
    (l for l in launches if l["launch_id"] == selected_launch_id), None
)

if selected_launch is None:
    st.error("Could not load selected launch.")
    st.stop()

try:
    render_readiness_panel(conn, selected_launch_id, "Compliance")
except Exception:
    pass

# Stage 1 validation
if selected_launch.get("pursuit_score") is None:
    st.warning(
        "⚠️ **Stage 1 not complete.** This launch has no pursuit score yet. "
        "You can still work in Compliance Compass, but stage advancement will remain blocked "
        "until Stage 1 data is saved."
    )
    st.info(
        f"**Launch #{selected_launch_id}** — ASIN: `{selected_launch['source_asin']}` "
        f"| Current Stage: {selected_launch['current_stage']}"
    )

if int(selected_launch.get("current_stage") or 1) < STAGE_COMPLIANCE:
    try:
        lsm_sync = LaunchStateManager()
        lsm_sync.update_launch(conn, selected_launch_id, current_stage=STAGE_COMPLIANCE)
        conn.commit()
        selected_launch["current_stage"] = STAGE_COMPLIANCE
        st.info("Auto-updated launch stage to Stage 2 after Stage 1 completion.")
    except Exception as exc:
        conn.rollback()
        st.warning(f"Could not auto-update launch stage to Stage 2: {exc}")

# Show launch summary
info_col1, info_col2, info_col3 = st.columns(3)
info_col1.metric("Launch ID", f"#{selected_launch_id}")
info_col2.metric("Source ASIN", selected_launch["source_asin"])
info_col3.metric(
    "Pursuit Score",
    f"{selected_launch['pursuit_score']:.1f}"
    if selected_launch.get("pursuit_score")
    else "—",
)

desc_col1, desc_col2 = st.columns(2)
with desc_col1:
    st.markdown(
        f"**Pursuit Category:** {selected_launch.get('pursuit_category') or '—'}"
    )
    st.markdown(f"**Current Stage:** {selected_launch['current_stage']}")
with desc_col2:
    if selected_launch.get("product_description"):
        st.markdown(f"**Description:** {selected_launch['product_description']}")

st.markdown("---")

lid = selected_launch_id
engine = ComplianceEngine()

try:
    all_rules = load_compliance_rules(conn)
    conn.commit()
except Exception as exc:
    conn.rollback()
    all_rules = []

try:
    existing_checklist = load_checklist_for_launch(conn, lid)
    conn.commit()
except Exception as exc:
    conn.rollback()
    existing_checklist = []

# ═══════════════════════════════════════════════════════════════════════════
# STEP 1: PRODUCT CATEGORY — auto-suggest, edit, lock/reset
# ═══════════════════════════════════════════════════════════════════════════
st.subheader("📦 Step 1: Product Category")
render_section_save_status(lid, "compliance", "category")

db_category = selected_launch.get("product_category") or ""
lock_key = f"cc_cat_locked_{lid}"
val_key = f"cc_cat_value_{lid}"
unlock_key = f"cc_force_unlock_{lid}"

if lock_key not in st.session_state:
    st.session_state[lock_key] = False
if val_key not in st.session_state:
    suggestion = _suggest_category(selected_launch.get("product_description", ""))
    cache_suggestion: str | None = None
    if not db_category:
        try:
            cache_suggestion = _suggest_category_from_cached_js(
                conn,
                str(selected_launch.get("source_asin") or ""),
                str(selected_launch.get("source_marketplace") or "US"),
            )
        except Exception as exc:
            logger.info(
                "Could not infer category from cached JS data for launch %s: %s",
                lid,
                exc,
            )

    st.session_state[val_key] = db_category or cache_suggestion or suggestion or ""

category_locked = st.session_state[lock_key]

if not category_locked:
    working_value = st.session_state.get(val_key, "")
    suggestion = _suggest_category(selected_launch.get("product_description", ""))

    if suggestion and not working_value:
        st.info(f"**Suggested category:** {suggestion} (based on product description)")
        st.session_state[val_key] = suggestion
        working_value = suggestion

    edited_category = st.text_input(
        "Product Category",
        value=working_value,
        placeholder="e.g. Electronic Toys, Kitchen Appliances, Clothing…",
        help="Used to match applicable compliance regimes. Be specific.",
        key=f"cc_cat_input_{lid}",
    )
    st.session_state[val_key] = edited_category

    if st.button("🔒 Lock Category & Proceed", type="primary"):
        if edited_category.strip():
            try:
                update_product_category(conn, lid, edited_category.strip())
                record_section_save(lid, "compliance", "category")
                st.session_state[lock_key] = True
                st.session_state[val_key] = edited_category.strip()
                st.session_state.pop(unlock_key, None)
                st.rerun()
            except Exception as exc:
                conn.rollback()
                st.error(f"Failed to save category: {exc}")
        else:
            st.warning("Category cannot be empty.")
else:
    active_category = st.session_state.get(val_key, db_category)
    col_cat, col_reset = st.columns([5, 1])
    with col_cat:
        st.success(f"🔒 **Category locked:** `{active_category}`")
    with col_reset:
        if st.button("🔄 Reset", key="cc_reset_flow"):
            st.session_state[unlock_key] = True
            keys_to_clear = [
                lock_key,
                val_key,
                f"cc_profile_{lid}",
                f"cc_profile_confirmed_{lid}",
                f"cc_regimes_confirmed_{lid}",
                f"cc_selected_regimes_{lid}",
                f"cc_risk_assessment_{lid}",
                f"cc_intended_use_{lid}",
                f"cc_materials_{lid}",
                f"cc_regime_select_{lid}",
            ]
            for key in keys_to_clear:
                st.session_state.pop(key, None)
            st.rerun()

# ═══════════════════════════════════════════════════════════════════════════
# STEP 1.5: CONFIRM PRODUCT PROFILE (gated on category lock)
# ═══════════════════════════════════════════════════════════════════════════
profile_confirmed = False
active_profile: ProductProfile | None = None

if category_locked:
    active_category = st.session_state.get(val_key, db_category)
    profile_key = f"cc_profile_{lid}"
    profile_confirmed_key = f"cc_profile_confirmed_{lid}"

    if profile_key not in st.session_state:
        with st.spinner("Analyzing product profile..."):
            inferred = infer_product_profile(
                active_category, selected_launch.get("product_description", "")
            )
            st.session_state[profile_key] = inferred.as_dict()

    p_data = st.session_state[profile_key]
    profile_confirmed = st.session_state.get(profile_confirmed_key, False)

    if not profile_confirmed:
        st.markdown("---")
        st.subheader("🧬 Step 1.5: Confirm Product Profile")
        st.info("Verify these characteristics to ensure accurate compliance rules.")

        col_p1, col_p2 = st.columns([2, 1])
        with col_p1:
            st.markdown("#### Core Characteristics")

            def _render_flag(label: str, key_suffix: str, help_text: str):
                current_val = p_data.get(key_suffix, False)
                new_val = st.toggle(
                    label,
                    value=current_val,
                    help=help_text,
                    key=f"toggle_{key_suffix}_{lid}",
                )
                if new_val != current_val:
                    p_data[key_suffix] = new_val
                    st.session_state[profile_key] = p_data

            _render_flag(
                "⚡ Is Electrical (Mains/Low Voltage)",
                "is_electrical",
                "Triggers LVD, EMC",
            )
            _render_flag(
                "🔌 Is Electronic (PCBs/Components)",
                "is_electronic",
                "Triggers RoHS, WEEE",
            )
            _render_flag(
                "🔋 Contains Batteries",
                "contains_batteries",
                "Triggers Battery Regulation",
            )
            _render_flag(
                "📡 Radio/Wireless (WiFi/BT/RF)", "is_radio_equipment", "Triggers RED"
            )

            st.markdown("#### Target Audience & Safety")
            _render_flag(
                "🧸 Is Toy (< 14 years)", "is_toy", "Triggers Toy Safety Directive"
            )
            _render_flag(
                "👶 Is Childcare (< 36 months)",
                "is_childcare",
                "Strict chemical/mechanical safety",
            )
            _render_flag(
                "🛡️ Is PPE (Protective Equip)", "is_ppe", "Triggers PPE Regulation"
            )
            _render_flag("⚕️ Is Medical Device", "is_medical", "Triggers MDR")

        with col_p2:
            st.markdown("#### Material & Category")
            _render_flag("🍽️ Food Contact", "is_food_contact", "Triggers FCM")
            _render_flag("💄 Cosmetic", "is_cosmetic", "Triggers Cosmetics Reg")
            _render_flag("🧪 Chemical / Mixture", "is_chemical", "Triggers CLP/REACH")
            _render_flag(
                "👗 Textile / Footwear", "is_textile", "Triggers Textile Labeling"
            )
            _render_flag("🪑 Furniture", "is_furniture", "Triggers Flammability/DPP")
            _render_flag("💡 Lighting", "is_lighting", "Triggers EcoDesign")

            st.metric("AI Confidence", f"{p_data.get('confidence', 0.0):.0%}")

            if st.button(
                "✅ Confirm Profile & Continue",
                type="primary",
                use_container_width=True,
            ):
                st.session_state[profile_confirmed_key] = True
                st.rerun()

    else:
        active_profile = ProductProfile.from_dict(p_data)
        flags = active_profile.active_flags

        col_prof, col_prof_edit = st.columns([5, 1])
        with col_prof:
            if flags:
                flag_badges = " ".join(
                    f"<span style='background:#E0E0E0;color:#333;padding:2px 8px;border-radius:12px;font-size:0.8em'>{f}</span>"
                    for f in flags
                )
                st.markdown(
                    f"🧬 **Profile Confirmed:** {flag_badges}", unsafe_allow_html=True
                )
            else:
                st.markdown("🧬 **Profile Confirmed:** (No specific triggers set)")

        with col_prof_edit:
            if st.button("✏️ Edit Profile", key="cc_edit_profile"):
                st.session_state[profile_confirmed_key] = False
                # Force re-selection of regimes when profile changes
                st.session_state[f"cc_regimes_confirmed_{lid}"] = False
                st.session_state.pop(f"cc_selected_regimes_{lid}", None)
                st.rerun()

if category_locked and profile_confirmed and active_profile:
    active_category = st.session_state.get(val_key, db_category)
    st.markdown("---")
    st.subheader("⚖️ Step 2: Select Applicable Regimes")

    matched_rules = engine.match_rules_for_product(active_profile, {}, all_rules)
    inferred_regimes = sorted(
        {str(r["regime"]) for r in matched_rules if r.get("regime")}
    )

    confirmed_key = f"cc_regimes_confirmed_{lid}"
    regimes_key = f"cc_selected_regimes_{lid}"

    if confirmed_key not in st.session_state:
        if existing_checklist:
            checklist_regimes = sorted(
                {
                    str(item["regime"])
                    for item in existing_checklist
                    if item.get("regime")
                }
            )
            st.session_state[regimes_key] = checklist_regimes
            st.session_state[confirmed_key] = True
        else:
            st.session_state[confirmed_key] = False

    regimes_confirmed = st.session_state[confirmed_key]

    if not regimes_confirmed:
        if inferred_regimes:
            st.markdown(
                'Based on **"'
                + active_category
                + '"**, auto-detected regimes: '
                + ", ".join(f"**{r}**" for r in inferred_regimes)
            )
        else:
            st.info(
                "No regimes auto-detected for this category. Select manually below."
            )

        prev_selected = st.session_state.get(regimes_key, inferred_regimes)

        selected_regimes = st.multiselect(
            "Applicable Regimes",
            options=ComplianceEngine.ALL_REGIMES,
            default=[r for r in prev_selected if r in ComplianceEngine.ALL_REGIMES],
            format_func=lambda r: (
                f"{_REGIME_CONFIG.get(r, {}).get('icon', '📋')} "
                f"{_REGIME_CONFIG.get(r, {}).get('label', r)}"
            ),
            key=f"cc_regime_select_{lid}",
        )

        for regime in ComplianceEngine.ALL_REGIMES:
            cfg = _REGIME_CONFIG.get(regime, {})
            tag = " *(auto-detected)*" if regime in inferred_regimes else ""
            st.caption(
                f"{cfg.get('icon', '📋')} **{cfg.get('label', regime)}**{tag}"
                f" — {cfg.get('description', '')}"
            )

        if st.button("✅ Confirm Regime Selection", type="primary"):
            if selected_regimes:
                st.session_state[regimes_key] = selected_regimes
                st.session_state[confirmed_key] = True
                st.rerun()
            else:
                st.warning("Select at least one regime to proceed.")
    else:
        selected_regimes = st.session_state.get(regimes_key, inferred_regimes)
        badges = " ".join(regime_badge(r) for r in selected_regimes)
        col_regimes, col_edit = st.columns([5, 1])
        with col_regimes:
            st.markdown(f"**Confirmed regimes:** {badges}", unsafe_allow_html=True)
        with col_edit:
            if st.button("✏️ Edit", key="cc_edit_regimes"):
                st.session_state[confirmed_key] = False
                st.session_state.pop(f"cc_regime_select_{lid}", None)
                st.rerun()

# ═══════════════════════════════════════════════════════════════════════════
# STEPS 3–4 + CHECKLIST GENERATION (gated on regime confirmation)
# ═══════════════════════════════════════════════════════════════════════════
regimes_confirmed = st.session_state.get(f"cc_regimes_confirmed_{lid}", False)
selected_regimes = st.session_state.get(f"cc_selected_regimes_{lid}", [])

# Ensure we have the profile loaded for these steps
if category_locked and not active_profile:
    _p_data = st.session_state.get(f"cc_profile_{lid}")
    if _p_data:
        active_profile = ProductProfile.from_dict(_p_data)

if category_locked and profile_confirmed and regimes_confirmed and selected_regimes:
    active_category = st.session_state.get(val_key, db_category)

    # Use profile if available, else category string
    product_scope = active_profile if active_profile else active_category

    matched_rules = engine.match_rules_for_product(product_scope, {}, all_rules)
    regime_filtered_rules = [
        r for r in matched_rules if r.get("regime") in selected_regimes
    ]

    # --- Step 3: Requirements & Packaging ---
    st.markdown("---")
    st.subheader("📋 Step 3: Requirements & Packaging")

    tab_reqs, tab_pkg, tab_label = st.tabs(
        ["📄 Key Requirements", "📦 Packaging Requirements", "🏷️ Labelling"]
    )

    with tab_reqs:
        if regime_filtered_rules:
            rules_by_regime: dict[str, list[dict[str, Any]]] = {}
            for rule in regime_filtered_rules:
                rules_by_regime.setdefault(rule.get("regime", "Unknown"), []).append(
                    rule
                )

            for regime in selected_regimes:
                rules_for_regime = rules_by_regime.get(regime, [])
                cfg = _REGIME_CONFIG.get(regime, {"icon": "📋", "label": regime})
                with st.expander(
                    f"{cfg['icon']} {cfg['label']} — {len(rules_for_regime)} requirement(s)",
                    expanded=bool(rules_for_regime),
                ):
                    if not rules_for_regime:
                        st.caption("No specific rules matched for this regime.")
                    for rule in rules_for_regime:
                        st.markdown(f"**{rule.get('requirement_name', 'N/A')}**")
                        if rule.get("requirement_description"):
                            st.caption(rule["requirement_description"])
                        docs = engine.get_required_documents(rule)
                        filtered_docs = [
                            d
                            for d in docs
                            if not (
                                d.startswith("Article 19 Labelling:")
                                or "Labelling:" in d
                            )
                        ]
                        if filtered_docs:
                            st.markdown(
                                "Documents: "
                                + ", ".join(f"📄 {d}" for d in filtered_docs)
                            )
                        if rule.get("source_url"):
                            st.markdown(f"[🔗 Reference]({rule['source_url']})")
                        st.markdown("")
        else:
            st.info(
                "No specific rules matched. Checklist may still be generated from broader patterns."
            )

    with tab_pkg:
        for regime in selected_regimes:
            cfg = _REGIME_CONFIG.get(regime, {"icon": "📋", "label": regime})
            all_reqs = _REGIME_PACKAGING_INFO.get(regime, [])

            pkg_reqs = [
                r
                for r in all_reqs
                if not (r.startswith("Article 19 Labelling:") or "Labelling:" in r)
            ]

            with st.expander(
                f"{cfg['icon']} {cfg['label']} — Packaging", expanded=True
            ):
                if pkg_reqs:
                    for req in pkg_reqs:
                        st.markdown(f"- {req}")
                else:
                    st.caption("No specific packaging requirements defined.")

    with tab_label:
        has_any_labels = False

        rule_labelling_by_regime = {}
        if regime_filtered_rules:
            for rule in regime_filtered_rules:
                regime = rule.get("regime", "Unknown")
                docs = engine.get_required_documents(rule)
                if docs:
                    for d in docs:
                        if d.startswith("Article 19 Labelling:") or "Labelling:" in d:
                            rule_labelling_by_regime.setdefault(regime, []).append(d)

        for regime in selected_regimes:
            cfg = _REGIME_CONFIG.get(regime, {"icon": "📋", "label": regime})
            all_reqs = _REGIME_PACKAGING_INFO.get(regime, [])

            label_reqs = [
                r
                for r in all_reqs
                if (r.startswith("Article 19 Labelling:") or "Labelling:" in r)
            ]

            if regime in rule_labelling_by_regime:
                label_reqs.extend(rule_labelling_by_regime[regime])

            if label_reqs:
                has_any_labels = True
                with st.expander(
                    f"{cfg['icon']} {cfg['label']} — Labelling", expanded=True
                ):
                    for req in sorted(set(label_reqs)):
                        st.markdown(f"- {req}")

        if not has_any_labels:
            st.info(
                "No specific labelling requirements identified for selected regimes."
            )

    dpp_relevant = engine.is_dpp_relevant(active_category, {})
    if dpp_relevant and "DPP" in selected_regimes:
        st.warning(
            "📱 **DPP 2026 Alert:** This product may fall under the EU Digital "
            "Product Passport regulation, mandatory from 2026."
        )

    # --- Step 4: AI Risk Assessment ---
    st.markdown("---")
    st.subheader("🤖 Step 4: AI Risk Assessment")
    render_section_save_status(lid, "compliance", "risk_assessment")

    st.markdown("Provide additional context for a more accurate risk analysis.")

    risk_key = f"cc_risk_assessment_{lid}"

    col_use, col_mat = st.columns(2)
    with col_use:
        intended_use = st.text_area(
            "Intended Use",
            value=st.session_state.get(f"cc_intended_use_{lid}", ""),
            placeholder="e.g. Children aged 6+, indoor use, educational purposes",
            height=100,
            key=f"cc_use_input_{lid}",
        )
        st.session_state[f"cc_intended_use_{lid}"] = intended_use
    with col_mat:
        materials = st.text_area(
            "Key Materials & Components",
            value=st.session_state.get(f"cc_materials_{lid}", ""),
            placeholder="e.g. ABS plastic, lithium battery, LED lights, BPA-free silicone",
            height=100,
            key=f"cc_mat_input_{lid}",
        )
        st.session_state[f"cc_materials_{lid}"] = materials

    cached_assessment = st.session_state.get(risk_key)
    btn_label = "🔄 Regenerate" if cached_assessment else "🤖 Generate Risk Assessment"

    if st.button(
        btn_label,
        type="secondary" if cached_assessment else "primary",
        key="cc_gen_risk",
    ):
        with st.spinner("Analysing compliance risks with AI…"):
            try:
                from services.compliance_risk_assessment import assess_compliance_risks

                key_reqs = [
                    r.get("requirement_name", "")
                    for r in regime_filtered_rules
                    if r.get("requirement_name")
                ]
                result = assess_compliance_risks(
                    product_category=active_category,
                    intended_use=intended_use or "",
                    materials=materials or "",
                    selected_regimes=selected_regimes,
                    key_requirements=key_reqs,
                )
                if result:
                    st.session_state[risk_key] = result
                    record_section_save(lid, "compliance", "risk_assessment")
                    st.rerun()
                else:
                    st.error(
                        "❌ Risk assessment failed. "
                        "Check Gemini credentials or try again."
                    )
            except Exception as exc:
                st.error(f"❌ Risk assessment error: {exc}")

    if cached_assessment:
        _render_risk_assessment_display(cached_assessment)
    else:
        st.caption(
            "ℹ️ Risk assessment is optional. "
            "Click above to generate, or proceed to checklist generation."
        )

    # --- Generate Checklist ---
    st.markdown("---")
    st.subheader("🔍 Generate Compliance Checklist")
    render_section_save_status(lid, "compliance", "checklist")

    col_analyze, col_info = st.columns([2, 4])
    with col_analyze:
        analyze_clicked = st.button(
            "🔎 Generate Checklist for Selected Regimes",
            type="primary",
            use_container_width=True,
            key="analyze_btn",
        )
    with col_info:
        if existing_checklist:
            st.info(
                f"ℹ️ Checklist has **{len(existing_checklist)} item(s)**. "
                "Re-generating adds new rules; existing items are preserved."
            )
        else:
            st.info("Click **Generate** to create compliance checklist items.")

    if analyze_clicked:
        if not all_rules:
            st.warning(
                "⚠️ No compliance rules in database. "
                "Run: `python scripts/seed_compliance_rules.py`"
            )
        else:
            regime_scoped_rules = [
                r for r in all_rules if r.get("regime") in selected_regimes
            ]
            # Use profile if available, else category string
            product_scope = active_profile if active_profile else active_category

            matched = engine.match_rules_for_product(
                product_scope, {}, regime_scoped_rules
            )

            if not matched:
                st.warning(
                    f"No rules matched for **'{active_category}'** "
                    "within selected regimes."
                )
            else:
                checklist_items = engine.generate_checklist(
                    lid, product_scope, {}, regime_scoped_rules
                )
                try:
                    upsert_checklist_items(conn, lid, checklist_items)
                    existing_checklist = load_checklist_for_launch(conn, lid)
                    conn.commit()
                    record_section_save(lid, "compliance", "checklist")

                    regime_counts: dict[str, int] = {}
                    for item in checklist_items:
                        r = item.get("regime", "Unknown")
                        regime_counts[r] = regime_counts.get(r, 0) + 1

                    st.success(
                        f"✅ Found **{len(matched)} rule(s)** across "
                        f"**{len(regime_counts)} regime(s)**. Checklist updated."
                    )
                    for regime, count in sorted(regime_counts.items()):
                        cfg = _REGIME_CONFIG.get(
                            regime, {"icon": "📋", "label": regime}
                        )
                        st.markdown(
                            f"- {cfg['icon']} **{cfg['label']}**: "
                            f"{count} requirement(s)"
                        )
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
        dpp_pending = [
            i for i in dpp_items if i.get("status") in ("pending", "in_progress")
        ]
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
        cfg = _REGIME_CONFIG.get(
            regime, {"label": regime, "color": "#888", "icon": "📋", "description": ""}
        )

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

        with st.expander(
            expander_label, expanded=(regime_blocked > 0 or regime_pending > 0)
        ):
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
                status_cfg = _STATUS_CONFIG.get(
                    current_status, {"icon": "❓", "color": "#888"}
                )
                item_header = f"{status_cfg['icon']} **{req_name}**"
                if is_dpp:
                    item_header += " 📱 *DPP 2026*"

                st.markdown(item_header)

                item_col1, item_col2 = st.columns([3, 2])

                with item_col1:
                    if req_desc:
                        st.markdown(
                            f"<small>{req_desc}</small>", unsafe_allow_html=True
                        )

                    if docs_required:
                        st.markdown("**Required documents:**")
                        for doc in docs_required:
                            st.markdown(f"  - 📄 {doc}")

                    if effective_date:
                        st.markdown(
                            f"<small>📅 Effective: {effective_date}</small>",
                            unsafe_allow_html=True,
                        )

                    if source_url:
                        st.markdown(
                            f"<small>🔗 [Reference]({source_url})</small>",
                            unsafe_allow_html=True,
                        )

                with item_col2:
                    # Status dropdown
                    status_idx = (
                        _STATUS_OPTIONS.index(current_status)
                        if current_status in _STATUS_OPTIONS
                        else 0
                    )
                    new_status = st.selectbox(
                        "Status",
                        options=_STATUS_OPTIONS,
                        index=status_idx,
                        format_func=lambda s: (
                            f"{_STATUS_CONFIG[s]['icon']} {_STATUS_CONFIG[s]['label']}"
                        ),
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
                            record_section_save(lid, "compliance", "checklist")
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

    all_required_docs: dict[str, set[str]] = {}
    for item in existing_checklist:
        docs = item.get("documentation_required") or []
        req_name = item.get("requirement_name") or "Unknown"
        for doc in docs:
            all_required_docs.setdefault(doc, set()).add(req_name)

    if selected_regimes and all_rules:
        val_key_local = locals().get("val_key")
        category_value = (
            st.session_state.get(val_key_local, db_category)
            if val_key_local
            else db_category
        )
        product_scope = active_profile if active_profile else category_value
        if product_scope:
            regime_scoped_rules = [
                r for r in all_rules if r.get("regime") in selected_regimes
            ]
            matched_rules = engine.match_rules_for_product(
                product_scope, {}, regime_scoped_rules
            )
            for rule in matched_rules:
                req_name = rule.get("requirement_name") or "Unknown"
                for doc in engine.get_required_documents(rule):
                    all_required_docs.setdefault(doc, set()).add(req_name)

    # Collect uploaded evidence URLs
    uploaded_evidence = [
        i.get("evidence_url") for i in existing_checklist if i.get("evidence_url")
    ]

    if all_required_docs:
        doc_col1, doc_col2 = st.columns([3, 1])
        doc_col1.markdown(
            f"**{len(all_required_docs)} unique document type(s) required across all regimes:**"
        )
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
                st.markdown(f"Required by: {', '.join(sorted(req_names))}")
                if has_evidence:
                    st.success(
                        "Evidence URL has been provided for at least one related requirement."
                    )
                else:
                    st.warning(
                        "No evidence URL uploaded yet. Add it in the checklist item above."
                    )
    else:
        st.info("No specific documents required, or checklist not yet generated.")

    # ---------------------------------------------------------------------------
    # Audit export
    # ---------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("🗄️ Audit Export")

    report_category = st.session_state.get(val_key, db_category)
    risk_assessment_report = st.session_state.get(f"cc_risk_assessment_{lid}")

    report_text = _build_compliance_audit_report(
        launch=selected_launch,
        product_category=str(report_category or ""),
        selected_regimes=[str(r) for r in selected_regimes],
        risk_assessment=risk_assessment_report
        if isinstance(risk_assessment_report, dict)
        else None,
        checklist_items=existing_checklist,
        required_docs=all_required_docs,
    )

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    asin_for_name = "".join(
        ch
        for ch in str(selected_launch.get("source_asin") or "").upper()
        if ch.isalnum()
    )
    launch_name_for_file = _filename_safe_token(
        str(selected_launch.get("launch_name") or "")
    )
    report_filename = (
        f"compliance_audit_launch_{lid}_{launch_name_for_file}_{asin_for_name}_{stamp}.md"
        if launch_name_for_file and asin_for_name
        else (
            f"compliance_audit_launch_{lid}_{launch_name_for_file}_{stamp}.md"
            if launch_name_for_file
            else (
                f"compliance_audit_launch_{lid}_{asin_for_name}_{stamp}.md"
                if asin_for_name
                else f"compliance_audit_launch_{lid}_{stamp}.md"
            )
        )
    )

    export_col1, export_col2 = st.columns([2, 3])
    with export_col1:
        st.download_button(
            "⬇️ Download Audit Report (.md)",
            data=report_text,
            file_name=report_filename,
            mime="text/markdown",
            use_container_width=True,
            key=f"cc_download_audit_{lid}",
        )

    with export_col2:
        drive_folder_id = os.environ.get("LAUNCHPAD_AUDIT_DRIVE_FOLDER_ID", "").strip()
        if not drive_folder_id:
            st.caption(
                "Set `LAUNCHPAD_AUDIT_DRIVE_FOLDER_ID` in your environment to enable "
                "Google Drive audit uploads."
            )
        else:
            st.caption("Drive path: `Launch_<id>_<ASIN>/Compliance/<YYYY-MM-DD>/`")

        upload_md_clicked = st.button(
            "☁️ Save Audit (.md) to Google Drive",
            type="primary",
            use_container_width=True,
            key=f"cc_upload_audit_md_{lid}",
        )
        upload_doc_clicked = st.button(
            "📝 Save Audit as Google Doc",
            use_container_width=True,
            key=f"cc_upload_audit_doc_{lid}",
            help="Converts markdown into a native Google Doc for easier reading and sharing.",
        )

        if upload_md_clicked or upload_doc_clicked:
            if not isinstance(risk_assessment_report, dict):
                st.warning(
                    "Generate AI Risk Assessment first, then export the full audit report."
                )
            elif not drive_folder_id:
                st.error(
                    "Google Drive upload is not configured. Missing `LAUNCHPAD_AUDIT_DRIVE_FOLDER_ID`."
                )
            else:
                try:
                    from services.drive_audit import (
                        upload_markdown_gdoc_to_launch_audit_folder,
                        upload_markdown_report_to_launch_audit_folder,
                    )

                    if upload_doc_clicked:
                        uploaded = upload_markdown_gdoc_to_launch_audit_folder(
                            report_text=report_text,
                            file_name=report_filename,
                            root_folder_id=drive_folder_id,
                            launch_id=lid,
                            source_asin=str(selected_launch.get("source_asin") or ""),
                        )
                    else:
                        uploaded = upload_markdown_report_to_launch_audit_folder(
                            report_text=report_text,
                            file_name=report_filename,
                            root_folder_id=drive_folder_id,
                            launch_id=lid,
                            source_asin=str(selected_launch.get("source_asin") or ""),
                        )
                    web_link = str(uploaded.get("webViewLink") or "").strip()
                    folder_path = str(uploaded.get("audit_folder_path") or "").strip()
                    file_id = uploaded.get("id")
                    if web_link:
                        format_name = "Google Doc" if upload_doc_clicked else "Markdown"
                        st.success(f"Saved to Google Drive ({format_name}): {web_link}")
                        if folder_path:
                            st.caption(f"Folder: `{folder_path}`")
                    else:
                        st.success(f"Saved to Google Drive. File ID: {file_id}")
                except Exception as exc:
                    st.error(f"Failed to upload audit report to Google Drive: {exc}")

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
                    if st.button(
                        "✅ Yes, advance to Stage 3", key="confirm_yes", type="primary"
                    ):
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

elif not category_locked:
    st.markdown("---")
    st.info("🔒 Lock your product category in Step 1 to begin the compliance workflow.")
else:
    st.markdown("---")
    st.info(
        "📋 No compliance checklist yet. "
        "Complete the steps above and click **Generate Checklist**."
    )
