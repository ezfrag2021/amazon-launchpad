"""
Stage 3: Risk & Pricing Architect

Determines optimal launch price and simulates PPC campaigns.
Analyses competitor pricing, calculates price envelope, simulates PPC
campaigns per keyword, and assesses product risks.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import psycopg
import streamlit as st
from dotenv import load_dotenv

from services.bdl_theme import apply_bdl_theme, render_bdl_footer
from services.db_connection import connect, resolve_dsn
from services.js_client import JungleScoutClient
from services.launch_state import STAGE_COMPLIANCE, STAGE_PRICING, LaunchStateManager
from services.pricing_engine import PricingEngine
from services.sp_api_fees import estimate_competitor_fees
from services.workflow_ui import (
    record_section_save,
    render_readiness_panel,
    render_section_save_status,
)

# ---------------------------------------------------------------------------
# Page configuration — must be the first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Module 3: Risk & Pricing Architect | Bodhi & Digby",
    page_icon="Logos/favicon.ico",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Environment & DB connection
# ---------------------------------------------------------------------------
load_dotenv()

_theme_state = apply_bdl_theme(
    "Model pricing, fees, PPC outcomes, and risks to choose a launch-ready strategy."
)


@st.cache_resource(show_spinner="Connecting to database…")
def get_connection() -> psycopg.Connection | None:
    """Return a cached psycopg connection, or None on failure."""
    try:
        raw_dsn = resolve_dsn("LAUNCHPAD_DB_DSN", "MARKET_INTEL_DSN", "PG_DSN")
        return connect(raw_dsn)
    except Exception as exc:  # noqa: BLE001
        st.error(f"⚠️ Database connection failed: {exc}")
        return None


conn = get_connection()

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("💰 Module 3: Risk & Pricing Architect")
st.markdown(
    "Determine optimal launch price and simulate PPC campaigns. "
    "Analyse competitor pricing, calculate your price envelope, simulate keyword campaigns, "
    "and assess product risks before moving to creative asset generation."
)
st.markdown("---")

# ---------------------------------------------------------------------------
# Guard: DB connection required
# ---------------------------------------------------------------------------
if conn is None:
    st.error("⚠️ No database connection. Cannot proceed.")
    st.stop()

# ---------------------------------------------------------------------------
# Launch selection
# ---------------------------------------------------------------------------
lsm = LaunchStateManager()

try:
    all_launches = lsm.list_launches(conn, limit=100)
    conn.commit()
except Exception as exc:  # noqa: BLE001
    conn.rollback()
    st.error(f"Failed to load launches: {exc}")
    st.stop()

if not all_launches:
    st.info("No launches found. Create a launch from the Dashboard first.")
    st.stop()

# Build dropdown options — show all launches but warn if stage < 3
launch_options = {
    l["launch_id"]: (
        f"#{l['launch_id']} — {l['source_asin']} "
        f"(Stage {l['current_stage']}"
        + (f", {l['pursuit_category']}" if l.get("pursuit_category") else "")
        + ")"
    )
    for l in all_launches
}

default_id = st.session_state.get("selected_launch_id")
default_idx = 0
launch_ids = list(launch_options.keys())
if default_id in launch_ids:
    default_idx = launch_ids.index(default_id)

selected_launch_raw = st.selectbox(
    "Select Launch",
    options=launch_ids,
    index=default_idx,
    format_func=lambda lid: launch_options[lid],
)
if selected_launch_raw is None:
    st.error("No launch selected.")
    st.stop()

selected_launch_id = int(selected_launch_raw)
st.session_state["selected_launch_id"] = selected_launch_id

# Load selected launch
try:
    launch = lsm.get_launch(conn, selected_launch_id)
    conn.commit()
except Exception as exc:  # noqa: BLE001
    conn.rollback()
    st.error(f"Failed to load launch: {exc}")
    st.stop()

if launch is None:
    st.error("Launch not found.")
    st.stop()

try:
    render_readiness_panel(conn, int(selected_launch_id), "Risk & Pricing")
except Exception:
    pass

current_stage = int(launch["current_stage"])

# ---------------------------------------------------------------------------
# Stage readiness advisory (non-blocking)
# ---------------------------------------------------------------------------
if current_stage < STAGE_COMPLIANCE:
    st.warning(
        "⚠️ This launch is earlier than Stage 2. You can still run and save pricing work here, "
        "but lifecycle stage advancement remains controlled by completion rules. "
        f"Current stage: {current_stage}."
    )

# ---------------------------------------------------------------------------
# Current product info
# ---------------------------------------------------------------------------
with st.expander("📦 Product Info", expanded=False):
    info_col1, info_col2, info_col3 = st.columns(3)
    info_col1.metric("Launch ID", f"#{launch['launch_id']}")
    info_col2.metric("Source ASIN", launch["source_asin"])
    info_col3.metric("Current Stage", f"Stage {current_stage}")

    if launch.get("product_description"):
        st.markdown(f"**Description:** {launch['product_description']}")
    if launch.get("product_category"):
        st.markdown(f"**Category:** {launch['product_category']}")
    if launch.get("target_marketplaces"):
        st.markdown(
            f"**Target Marketplaces:** {', '.join(launch['target_marketplaces'])}"
        )

st.markdown("---")

# ---------------------------------------------------------------------------
# Marketplace selector (used across sections)
# ---------------------------------------------------------------------------
target_mkts = launch.get("target_marketplaces") or ["UK"]
marketplace = st.selectbox(
    "Marketplace for Analysis",
    options=target_mkts,
    index=0,
    help="Select which target marketplace to analyse pricing for.",
)

marketplace_norm = "GB" if marketplace == "UK" else marketplace
prices_state_key = f"fetched_competitor_prices:{selected_launch_id}:{marketplace_norm}"
offers_state_key = f"competitor_offers:{selected_launch_id}:{marketplace_norm}"

_REPORT_DEFAULT_FOLDER_ID = "1wgc7C1nmHokJvu_i0TPNjVSQ1Rxp2xr7"

_REPORT_SECTIONS = [
    ("executive_summary", "## 1) Executive Summary"),
    ("product_snapshot", "## 2) Product Snapshot"),
    ("market_opportunity", "## 3) Market Opportunity & Demand"),
    ("competitive_landscape", "## 4) Competitive Landscape"),
    ("pricing_unit_economics", "## 5) Pricing & Unit Economics"),
    ("ppc_go_to_market", "## 6) PPC Go-To-Market Plan"),
    ("risk_register", "## 7) Risk Register"),
    ("compliance_readiness", "## 8) Compliance Readiness"),
    ("creative_readiness", "## 9) Creative Readiness"),
    ("recommendation_next_steps", "## 10) Recommendation & Next Steps"),
]


def _safe_report_token(value: str, max_len: int = 40) -> str:
    token = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
    token = "_".join(part for part in token.split("_") if part)
    return token[:max_len].strip("_")


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3:
            raw = "\n".join(lines[1:-1]).strip()

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model response.")
    return json.loads(raw[start : end + 1])


def _load_saved_pricing_snapshot(
    conn: psycopg.Connection,
    launch_id: int,
    marketplace_code: str,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT recommended_launch_price, price_floor, price_ceiling,
                   margin_estimate_pct, competitor_price_p25, competitor_price_p50,
                   competitor_price_p75, competitor_count, analyzed_at
            FROM launchpad.pricing_analysis
            WHERE launch_id = %s AND marketplace = %s
            ORDER BY analyzed_at DESC
            LIMIT 1
            """,
            (launch_id, marketplace_code),
        )
        row = cur.fetchone()

    if not row:
        return None

    return {
        "recommended_launch_price": float(row[0]) if row[0] is not None else 0.0,
        "price_floor": float(row[1]) if row[1] is not None else 0.0,
        "price_ceiling": float(row[2]) if row[2] is not None else 0.0,
        "margin_estimate_pct": float(row[3]) if row[3] is not None else None,
        "competitor_price_p25": float(row[4]) if row[4] is not None else 0.0,
        "competitor_price_p50": float(row[5]) if row[5] is not None else 0.0,
        "competitor_price_p75": float(row[6]) if row[6] is not None else 0.0,
        "competitor_count": int(row[7] or 0),
        "saved_analyzed_at": row[8],
    }


def _load_saved_ppc_simulation(
    conn: psycopg.Connection,
    launch_id: int,
    marketplace_code: str,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT keyword, search_volume_exact, estimated_cpc, estimated_acos_pct,
                   estimated_tacos_pct, organic_rank_target, estimated_daily_spend,
                   estimated_days_to_page1, source_field
            FROM launchpad.ppc_simulation
            WHERE launch_id = %s AND marketplace = %s
            ORDER BY estimated_daily_spend DESC NULLS LAST, keyword
            """,
            (launch_id, marketplace_code),
        )
        rows = cur.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "keyword": row[0],
                "search_volume_exact": int(row[1] or 0),
                "estimated_cpc": float(row[2] or 0),
                "estimated_acos_pct": float(row[3] or 0),
                "estimated_tacos_pct": float(row[4] or 0),
                "organic_rank_target": int(row[5] or 0),
                "estimated_daily_spend": float(row[6] or 0),
                "estimated_days_to_page1": int(row[7] or 0),
                "source_field": row[8] or "ppc_bid_exact",
            }
        )
    return results


def _load_saved_risk_assessments(
    conn: psycopg.Connection,
    launch_id: int,
) -> dict[str, dict[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (risk_category)
                   risk_category, severity, risk_description, mitigation
            FROM launchpad.risk_assessment
            WHERE launch_id = %s
              AND risk_category IN ('safety', 'fragility', 'IP', 'compliance', 'market')
            ORDER BY risk_category, assessed_at DESC
            """,
            (launch_id,),
        )
        rows = cur.fetchall()

    return {
        str(r[0]): {
            "severity": str(r[1] or "Low"),
            "description": str(r[2] or ""),
            "mitigation": str(r[3] or ""),
        }
        for r in rows
        if r and r[0]
    }


def _hydrate_saved_module_state(
    conn: psycopg.Connection,
    launch_id: int,
    marketplace_code: str,
) -> None:
    hydration_key = f"rp_hydrated:{launch_id}:{marketplace_code}"
    if st.session_state.get(hydration_key):
        return

    saved_pricing = _load_saved_pricing_snapshot(conn, launch_id, marketplace_code)
    if saved_pricing:
        st.session_state["price_envelope"] = saved_pricing
        st.session_state["competitor_analysis"] = {
            "competitor_price_p25": saved_pricing.get("competitor_price_p25", 0.0),
            "competitor_price_p50": saved_pricing.get("competitor_price_p50", 0.0),
            "competitor_price_p75": saved_pricing.get("competitor_price_p75", 0.0),
            "competitor_count": saved_pricing.get("competitor_count", 0),
            "saved_analyzed_at": saved_pricing.get("saved_analyzed_at"),
        }
    else:
        st.session_state.pop("price_envelope", None)
        st.session_state.pop("competitor_analysis", None)

    saved_ppc = _load_saved_ppc_simulation(conn, launch_id, marketplace_code)
    if saved_ppc:
        st.session_state["ppc_simulation"] = saved_ppc
        st.session_state["ppc_marketplace"] = marketplace_code
        st.session_state["ppc_keywords"] = "\n".join(
            str(row.get("keyword") or "") for row in saved_ppc if row.get("keyword")
        )
    else:
        st.session_state.pop("ppc_simulation", None)
        st.session_state.pop("ppc_marketplace", None)
        st.session_state.pop("ppc_keywords", None)

    for risk_key in ["safety", "fragility", "IP", "compliance", "market"]:
        st.session_state.pop(f"risk_severity_{risk_key}", None)
        st.session_state.pop(f"risk_desc_{risk_key}", None)
        st.session_state.pop(f"risk_mit_{risk_key}", None)

    saved_risks = _load_saved_risk_assessments(conn, launch_id)
    for risk_key, risk_vals in saved_risks.items():
        sev_key = f"risk_severity_{risk_key}"
        desc_key = f"risk_desc_{risk_key}"
        mit_key = f"risk_mit_{risk_key}"
        st.session_state[sev_key] = risk_vals.get("severity", "Low")
        st.session_state[desc_key] = risk_vals.get("description", "")
        st.session_state[mit_key] = risk_vals.get("mitigation", "")

    st.session_state[hydration_key] = True


def _collect_report_snapshot(
    conn: psycopg.Connection,
    launch_row: dict[str, Any],
    marketplace_code: str,
    competitor_analysis: dict[str, Any] | None,
    price_envelope: dict[str, Any] | None,
    ppc_simulation: list[dict[str, Any]],
    risks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    launch_id = int(launch_row["launch_id"])
    snapshot: dict[str, Any] = {
        "launch": {
            "launch_id": launch_id,
            "launch_name": launch_row.get("launch_name"),
            "source_asin": launch_row.get("source_asin"),
            "source_marketplace": launch_row.get("source_marketplace"),
            "target_marketplaces": launch_row.get("target_marketplaces") or [],
            "product_category": launch_row.get("product_category"),
            "product_description": launch_row.get("product_description"),
            "pursuit_score": launch_row.get("pursuit_score"),
            "pursuit_category": launch_row.get("pursuit_category"),
            "current_stage": launch_row.get("current_stage"),
        },
        "marketplace": marketplace_code,
        "competitor_analysis": competitor_analysis or {},
        "price_envelope": price_envelope or {},
        "ppc_simulation_summary": {
            "keyword_count": len(ppc_simulation),
            "total_daily_spend": round(
                sum(
                    float(row.get("estimated_daily_spend") or 0)
                    for row in ppc_simulation
                ),
                2,
            ),
            "blended_acos_pct": round(
                (
                    sum(
                        float(row.get("estimated_acos_pct") or 0)
                        for row in ppc_simulation
                    )
                    / len(ppc_simulation)
                )
                if ppc_simulation
                else 0.0,
                2,
            ),
        },
        "risk_assessments": risks,
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, COUNT(*)
            FROM launchpad.launch_compliance_checklist
            WHERE launch_id = %s
            GROUP BY status
            """,
            (launch_id,),
        )
        checklist_counts = {str(r[0]): int(r[1]) for r in cur.fetchall()}

        cur.execute(
            """
            SELECT recommended_launch_price, price_floor, price_ceiling, margin_estimate_pct,
                   competitor_count, analyzed_at
            FROM launchpad.pricing_analysis
            WHERE launch_id = %s AND marketplace = %s
            ORDER BY analyzed_at DESC
            LIMIT 1
            """,
            (launch_id, marketplace_code),
        )
        pricing_row = cur.fetchone()

        cur.execute(
            """
            SELECT COUNT(*), MAX(generated_at)
            FROM launchpad.listing_drafts
            WHERE launch_id = %s
            """,
            (launch_id,),
        )
        draft_row = cur.fetchone()

    snapshot["compliance_summary"] = checklist_counts
    snapshot["latest_saved_pricing"] = (
        {
            "recommended_launch_price": pricing_row[0],
            "price_floor": pricing_row[1],
            "price_ceiling": pricing_row[2],
            "margin_estimate_pct": pricing_row[3],
            "competitor_count": pricing_row[4],
            "analyzed_at": str(pricing_row[5]),
        }
        if pricing_row
        else {}
    )
    snapshot["creative_summary"] = {
        "listing_draft_count": int(draft_row[0]) if draft_row else 0,
        "latest_draft_at": str(draft_row[1]) if draft_row and draft_row[1] else None,
    }
    return snapshot


def _generate_opportunity_report_markdown(snapshot: dict[str, Any]) -> str:
    from services.auth_manager import get_generative_client

    genai = get_generative_client()
    preferred_model = os.environ.get("LAUNCHPAD_GEMINI_MODEL", "").strip()
    model_candidates = [
        m
        for m in [
            preferred_model,
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-1.5-flash",
        ]
        if m
    ]

    section_keys = [k for k, _ in _REPORT_SECTIONS]
    prompt = f"""
You are producing a structured Amazon launch opportunity report.

Rules:
- Return STRICT JSON only.
- Top-level keys must exactly be: {json.dumps(section_keys)}
- Each value must be markdown text content for that section only (no heading line).
- Keep numerical values consistent with provided data.
- If data is missing, state assumptions explicitly and keep recommendations conservative.

Context data:
{json.dumps(snapshot, default=str, ensure_ascii=True)}
"""

    parsed: dict[str, Any] | None = None
    last_error: Exception | None = None

    for model_name in model_candidates:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            parsed = _extract_json_object(str(getattr(response, "text", "") or ""))
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    if parsed is None:
        raise RuntimeError(
            "No compatible Gemini model was available for report generation. "
            f"Tried: {', '.join(model_candidates)}. "
            f"Last error: {last_error}"
        )

    launch_name = str(snapshot.get("launch", {}).get("launch_name") or "").strip()
    launch_id = snapshot.get("launch", {}).get("launch_id")
    launch_title = (
        f"Launch #{launch_id} - {launch_name}"
        if launch_name
        else f"Launch #{launch_id}"
    )
    lines = [
        f"# Opportunity Report - {launch_title}",
        f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Source ASIN: {snapshot.get('launch', {}).get('source_asin') or 'N/A'}",
        f"Source Marketplace: {snapshot.get('launch', {}).get('source_marketplace') or 'N/A'}",
        "",
    ]
    for key, heading in _REPORT_SECTIONS:
        lines.append(heading)
        body = str(parsed.get(key) or "No data available for this section.").strip()
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


try:
    _hydrate_saved_module_state(conn, selected_launch_id, marketplace)
    conn.commit()
except Exception:
    conn.rollback()


st.markdown("---")

# ---------------------------------------------------------------------------
# Section 1: Competitor Pricing Analysis
# ---------------------------------------------------------------------------
st.subheader("📊 Competitor Pricing Analysis")
st.markdown(
    "Fetch competitor pricing data from Jungle Scout to understand the price landscape."
)

engine = PricingEngine()

# Check if we have a JS client available
js_available = bool(
    os.getenv("JUNGLESCOUT_API_KEY") and os.getenv("JUNGLESCOUT_API_KEY_NAME")
)

comp_col1, comp_col2 = st.columns([3, 1])
with comp_col1:
    st.markdown(
        "Enter competitor prices manually, or reuse Stage 1 competitors. "
        "Use Jungle Scout fetch only when you need a fresh pull."
    )
with comp_col2:
    if not js_available:
        st.warning("Jungle Scout API not configured — manual entry only.")

# Manual price entry
manual_prices_input = st.text_area(
    "Competitor Prices (one per line, e.g. 19.99)",
    placeholder="19.99\n24.99\n17.50\n22.00\n29.99",
    height=120,
    help="Enter competitor prices manually. One price per line.",
    key="manual_prices",
)

fetch_col1, fetch_col2 = st.columns([2, 1])
with fetch_col1:
    fetch_competitors = st.button(
        "🔍 Refresh from Jungle Scout",
        disabled=not js_available,
        help="Optional refresh. Avoid unnecessary billable calls when Stage 1 data is sufficient.",
    )

# Parse manual prices
competitor_prices: list[float] = []
if manual_prices_input.strip():
    for line in manual_prices_input.strip().splitlines():
        line = line.strip()
        if line:
            try:
                competitor_prices.append(float(line))
            except ValueError:
                st.warning(f"Skipping invalid price: '{line}'")

# Seed from Stage 1 competitor set when available (zero extra API calls)
seeded_prices: list[float] = []
seeded_offers: list[dict[str, Any]] = []
stage1_competitors = st.session_state.get("competitor_data")
if isinstance(stage1_competitors, list):
    for row in stage1_competitors:
        if not isinstance(row, dict):
            continue
        row_market = str(row.get("marketplace") or "").strip().upper()
        row_market = "GB" if row_market == "UK" else row_market
        if row_market != marketplace_norm:
            continue
        raw_price = row.get("price")
        if raw_price is None:
            continue
        try:
            price_val = float(raw_price)
        except (TypeError, ValueError):
            continue
        if price_val <= 0:
            continue
        seeded_prices.append(price_val)
        asin = "".join(ch for ch in str(row.get("asin") or "").upper() if ch.isalnum())
        if asin:
            seeded_offers.append({"asin": asin, "price": price_val})

if (
    seeded_prices
    and not manual_prices_input.strip()
    and not st.session_state.get(prices_state_key)
):
    st.session_state[prices_state_key] = seeded_prices
    st.session_state[offers_state_key] = seeded_offers
    st.caption(
        f"Using {len(seeded_prices)} competitor prices from Module 1 for {marketplace}."
    )

# Fetch from Jungle Scout (optional refresh)
if fetch_competitors and js_available:
    try:
        js_client = JungleScoutClient()
        with st.spinner("Checking API budget…"):
            budget_ok = js_client.check_budget_available(conn, pages=1)
            conn.commit()

        if not budget_ok:
            st.error("❌ API budget exhausted. Cannot fetch competitor data.")
        else:
            with st.spinner("Fetching product database from Jungle Scout…"):
                result = js_client.get_product_database(
                    conn,
                    marketplace=marketplace,
                    script_name="3_Risk_Pricing_Architect",
                    launch_id=selected_launch_id,
                )
                conn.commit()

            if result is None:
                st.warning("No data returned from Jungle Scout.")
            else:
                # Extract prices from result
                fetched_prices: list[float] = []
                fetched_offers: list[dict[str, Any]] = []
                try:
                    if hasattr(result, "data"):
                        items = result.data
                    elif isinstance(result, dict):
                        items = result.get("data", [])
                    else:
                        items = []

                    if not isinstance(items, list):
                        items = []

                    for item in items:
                        if isinstance(item, dict):
                            item_id = item.get("id")
                            attrs = item.get("attributes") or {}
                        else:
                            item_id = getattr(item, "id", None)
                            attrs = getattr(item, "attributes", None) or {}

                        if not isinstance(attrs, dict):
                            attrs = {}

                        price = None
                        price = (
                            attrs.get("price")
                            or attrs.get("current_price")
                            or attrs.get("buy_box_price")
                            or attrs.get("list_price")
                        )

                        if isinstance(price, dict):
                            price = (
                                price.get("amount")
                                or price.get("value")
                                or price.get("price")
                            )

                        if price is not None:
                            try:
                                normalized = (
                                    str(price)
                                    .replace("£", "")
                                    .replace("€", "")
                                    .replace("$", "")
                                    .strip()
                                )
                                parsed = float(normalized)
                                if parsed > 0:
                                    fetched_prices.append(parsed)

                                    asin_raw = attrs.get("asin") or item_id
                                    asin = (
                                        "".join(
                                            ch
                                            for ch in str(asin_raw or "").upper()
                                            if ch.isalnum()
                                        )
                                        if asin_raw is not None
                                        else ""
                                    )
                                    if asin:
                                        fetched_offers.append(
                                            {"asin": asin, "price": parsed}
                                        )
                            except (TypeError, ValueError):
                                pass
                except Exception:  # noqa: BLE001
                    pass

                if fetched_prices:
                    competitor_prices = fetched_prices
                    st.success(
                        f"✅ Fetched {len(fetched_prices)} competitor prices from Jungle Scout."
                    )
                    st.session_state[prices_state_key] = fetched_prices
                    st.session_state[offers_state_key] = fetched_offers
                else:
                    st.warning(
                        "Jungle Scout returned data but no prices could be extracted."
                    )
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        st.error(f"Jungle Scout error: {exc}")

# Use previously fetched prices if available
if not competitor_prices and st.session_state.get(prices_state_key):
    competitor_prices = st.session_state[prices_state_key]

competitor_offers = st.session_state.get(offers_state_key, [])
saved_comp_analysis = st.session_state.get("competitor_analysis")

# Display analysis if we have prices
if competitor_prices:
    try:
        analysis = engine.analyze_competitor_pricing(competitor_prices)

        # Key metrics
        m_col1, m_col2, m_col3, m_col4, m_col5 = st.columns(5)
        m_col1.metric("Min Price", f"£{analysis['price_min']:.2f}")
        m_col2.metric("25th Percentile", f"£{analysis['competitor_price_p25']:.2f}")
        m_col3.metric("Median (P50)", f"£{analysis['competitor_price_p50']:.2f}")
        m_col4.metric("75th Percentile", f"£{analysis['competitor_price_p75']:.2f}")
        m_col5.metric("Max Price", f"£{analysis['price_max']:.2f}")

        avg_col1, avg_col2, avg_col3 = st.columns(3)
        avg_col1.metric("Average Price", f"£{analysis['price_mean']:.2f}")
        avg_col2.metric("Competitors Analysed", analysis["competitor_count"])

        stability = analysis["price_stability"]
        stability_color = {"stable": "🟢", "moderate": "🟡", "volatile": "🔴"}.get(
            stability, "⚪"
        )
        avg_col3.metric("Price Stability", f"{stability_color} {stability.title()}")

        # Price distribution histogram
        try:
            import altair as alt
            import pandas as pd

            price_df = pd.DataFrame({"price": competitor_prices})
            hist = (
                alt.Chart(price_df)
                .mark_bar(color="#FF9900", opacity=0.8)
                .encode(
                    alt.X("price:Q", bin=alt.Bin(maxbins=20), title="Price (£)"),
                    alt.Y("count():Q", title="Number of Competitors"),
                    tooltip=["count():Q"],
                )
                .properties(title="Competitor Price Distribution", height=250)
            )
            st.altair_chart(hist, use_container_width=True)
        except ImportError:
            # Fallback: simple bar chart
            import pandas as pd

            price_df = pd.DataFrame({"price": sorted(competitor_prices)})
            st.bar_chart(price_df["price"].value_counts().sort_index())

        # Top 10 competitors table
        with st.expander("📋 Top 10 Competitor Prices", expanded=False):
            sorted_prices = sorted(competitor_prices)
            top10 = sorted_prices[:10]
            import pandas as pd

            df_top10 = pd.DataFrame(
                {
                    "Rank": range(1, len(top10) + 1),
                    "Price (£)": [f"£{p:.2f}" for p in top10],
                    "vs Median": [
                        f"{'▲' if p > analysis['competitor_price_p50'] else '▼'} "
                        f"{abs(p - analysis['competitor_price_p50']):.2f}"
                        for p in top10
                    ],
                }
            )
            st.dataframe(df_top10, use_container_width=True, hide_index=True)

        # Store analysis in session state for price envelope
        st.session_state["competitor_analysis"] = analysis
        st.session_state["competitor_prices"] = competitor_prices

    except Exception as exc:  # noqa: BLE001
        st.error(f"Error analysing competitor prices: {exc}")
elif isinstance(saved_comp_analysis, dict) and saved_comp_analysis.get(
    "competitor_count", 0
):
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    m_col1.metric(
        "25th Percentile",
        f"£{float(saved_comp_analysis.get('competitor_price_p25', 0.0)):.2f}",
    )
    m_col2.metric(
        "Median (P50)",
        f"£{float(saved_comp_analysis.get('competitor_price_p50', 0.0)):.2f}",
    )
    m_col3.metric(
        "75th Percentile",
        f"£{float(saved_comp_analysis.get('competitor_price_p75', 0.0)):.2f}",
    )
    m_col4.metric(
        "Competitors Analysed", int(saved_comp_analysis.get("competitor_count", 0))
    )

    analyzed_at = saved_comp_analysis.get("saved_analyzed_at")
    analyzed_label = (
        analyzed_at.strftime("%Y-%m-%d %H:%M")
        if analyzed_at is not None and hasattr(analyzed_at, "strftime")
        else str(analyzed_at or "unknown")
    )
    st.caption(
        f"Loaded saved pricing snapshot for {marketplace} (analysed {analyzed_label})."
    )
else:
    st.info("Enter competitor prices above to see the analysis.")

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 2: Price Envelope Calculation
# ---------------------------------------------------------------------------
st.subheader("🎯 Price Envelope Calculator")
st.markdown("Calculate your viable price range based on costs and competitor data.")

if "pricing_amazon_fee_pct" not in st.session_state:
    st.session_state["pricing_amazon_fee_pct"] = 15.0
if "pricing_fulfillment_cost" not in st.session_state:
    st.session_state["pricing_fulfillment_cost"] = 0.0

fee_auto_col1, fee_auto_col2 = st.columns([3, 2])
with fee_auto_col2:
    autofill_fees = st.button(
        "🤖 Auto-fill Fees from SP-API",
        help="Uses competitor ASIN+price samples to estimate referral % and FBA fee.",
        disabled=not bool(competitor_offers),
        use_container_width=True,
    )
    if not competitor_offers:
        st.caption("Fetch competitor data first to auto-fill fees.")

pe_col1, pe_col2 = st.columns(2)

with pe_col1:
    cogs = st.number_input(
        "Cost of Goods (COGS) £",
        min_value=0.01,
        max_value=10000.0,
        value=8.00,
        step=0.50,
        format="%.2f",
        help="Your unit cost of goods in marketplace currency.",
    )
    target_margin = st.number_input(
        "Target Margin %",
        min_value=1.0,
        max_value=80.0,
        value=30.0,
        step=1.0,
        format="%.1f",
        help="Minimum acceptable gross margin percentage.",
    )

with pe_col2:
    if autofill_fees:
        try:
            with st.spinner("Estimating fees from SP-API competitor samples…"):
                fee_estimate = estimate_competitor_fees(
                    competitor_offers=competitor_offers,
                    marketplace=marketplace,
                    max_offers=10,
                )

            referral_est = fee_estimate.get("referral_fee_pct")
            fulfillment_est = fee_estimate.get("fulfillment_fee")
            if referral_est is not None:
                st.session_state["pricing_amazon_fee_pct"] = round(
                    float(referral_est), 2
                )
            if fulfillment_est is not None:
                st.session_state["pricing_fulfillment_cost"] = round(
                    float(fulfillment_est), 2
                )

            st.success(
                "Estimated fees loaded "
                f"(samples: referral={fee_estimate.get('referral_samples', 0)}, "
                f"fulfillment={fee_estimate.get('fulfillment_samples', 0)})."
            )
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.warning(
                "Could not auto-fill fees from SP-API. "
                f"You can continue manually. Details: {exc}"
            )

    amazon_fee_pct = st.number_input(
        "Amazon Referral Fee %",
        min_value=1.0,
        max_value=45.0,
        step=0.5,
        format="%.1f",
        help="Amazon referral fee percentage for your category (typically 8–15%).",
        key="pricing_amazon_fee_pct",
    )
    fulfillment_cost = st.number_input(
        "Fulfillment Cost £ (optional)",
        min_value=0.0,
        max_value=100.0,
        step=0.25,
        format="%.2f",
        help="FBA or FBM fulfilment cost per unit.",
        key="pricing_fulfillment_cost",
    )

calc_envelope = st.button("📐 Calculate Price Envelope", type="primary")

if calc_envelope or st.session_state.get("price_envelope"):
    prices_for_envelope = st.session_state.get("competitor_prices", competitor_prices)

    if not prices_for_envelope:
        st.warning("⚠️ Enter competitor prices first to calculate the price envelope.")
    else:
        try:
            envelope = engine.calculate_launch_price_envelope(
                competitor_prices=prices_for_envelope,
                target_margin_pct=target_margin,
                cost_of_goods=cogs,
            )
            st.session_state["price_envelope"] = envelope
            st.session_state["cogs"] = cogs
            st.session_state["amazon_fee_pct"] = amazon_fee_pct
            st.session_state["fulfillment_cost"] = fulfillment_cost

            # Display envelope results
            env_col1, env_col2, env_col3 = st.columns(3)

            with env_col1:
                st.metric(
                    "🔻 Price Floor",
                    f"£{envelope['price_floor']:.2f}",
                    help="Minimum viable price to achieve target margin.",
                )
                floor_margin = engine.calculate_margin(
                    envelope["price_floor"], cogs, amazon_fee_pct, fulfillment_cost
                )
                st.caption(f"Net margin: {floor_margin['net_margin_pct']:.1f}%")

            with env_col2:
                st.metric(
                    "✅ Recommended Launch Price",
                    f"£{envelope['recommended_launch_price']:.2f}",
                    help="Suggested entry price — slight undercut on median competitor.",
                )
                rec_margin = engine.calculate_margin(
                    envelope["recommended_launch_price"],
                    cogs,
                    amazon_fee_pct,
                    fulfillment_cost,
                )
                st.caption(f"Net margin: {rec_margin['net_margin_pct']:.1f}%")

            with env_col3:
                st.metric(
                    "🔺 Price Ceiling",
                    f"£{envelope['price_ceiling']:.2f}",
                    help="Maximum competitive price — premium positioning.",
                )
                ceil_margin = engine.calculate_margin(
                    envelope["price_ceiling"], cogs, amazon_fee_pct, fulfillment_cost
                )
                st.caption(f"Net margin: {ceil_margin['net_margin_pct']:.1f}%")

            # Margin breakdown at recommended price
            with st.expander("💹 Margin Breakdown at Recommended Price", expanded=True):
                rec_price = envelope["recommended_launch_price"]
                margin_detail = engine.calculate_margin(
                    rec_price, cogs, amazon_fee_pct, fulfillment_cost
                )

                mb_col1, mb_col2, mb_col3, mb_col4 = st.columns(4)
                mb_col1.metric("Selling Price", f"£{margin_detail['price']:.2f}")
                mb_col2.metric("COGS", f"£{margin_detail['cost_of_goods']:.2f}")
                mb_col3.metric(
                    "Amazon Fee", f"£{margin_detail['amazon_referral_fee']:.2f}"
                )
                mb_col4.metric(
                    "Fulfillment", f"£{margin_detail['fulfillment_cost']:.2f}"
                )

                mb_col5, mb_col6, mb_col7, mb_col8 = st.columns(4)
                mb_col5.metric("Total Costs", f"£{margin_detail['total_costs']:.2f}")
                mb_col6.metric("Net Profit", f"£{margin_detail['net_profit']:.2f}")
                mb_col7.metric(
                    "Gross Margin", f"{margin_detail['gross_margin_pct']:.1f}%"
                )
                mb_col8.metric("Net Margin", f"{margin_detail['net_margin_pct']:.1f}%")

                st.caption(
                    f"Break-even price: £{margin_detail['break_even_price']:.2f}"
                )

            # Price positioning chart (market range + launch markers)
            st.markdown("**Price Positioning**")
            try:
                import altair as alt
                import pandas as pd

                sorted_prices = sorted(float(p) for p in prices_for_envelope)
                n = len(sorted_prices)
                p50 = sorted_prices[n // 2]
                p25 = sorted_prices[max(0, int(n * 0.25) - 1)]
                p75 = sorted_prices[min(n - 1, int(n * 0.75))]
                pmin = sorted_prices[0]
                pmax = sorted_prices[-1]

                floor_v = float(envelope["price_floor"])
                rec_v = float(envelope["recommended_launch_price"])
                ceil_v = float(envelope["price_ceiling"])
                break_even_v = float(margin_detail["break_even_price"])

                segments_df = pd.DataFrame(
                    [
                        {
                            "row": "Competitor Market",
                            "x0": pmin,
                            "x1": pmax,
                            "band": "Range",
                        },
                        {
                            "row": "Competitor Market",
                            "x0": p25,
                            "x1": p75,
                            "band": "IQR",
                        },
                    ]
                )
                markers_df = pd.DataFrame(
                    [
                        {
                            "row": "Competitor Market",
                            "label": "Median",
                            "price": p50,
                            "kind": "Median",
                        },
                        {
                            "row": "Your Pricing",
                            "label": "Floor",
                            "price": floor_v,
                            "kind": "Floor",
                        },
                        {
                            "row": "Your Pricing",
                            "label": "Recommended",
                            "price": rec_v,
                            "kind": "Recommended",
                        },
                        {
                            "row": "Your Pricing",
                            "label": "Ceiling",
                            "price": ceil_v,
                            "kind": "Ceiling",
                        },
                        {
                            "row": "Your Pricing",
                            "label": "Break-even",
                            "price": break_even_v,
                            "kind": "Break-even",
                        },
                    ]
                )

                base = alt.Chart().encode(
                    y=alt.Y(
                        "row:N",
                        title=None,
                        sort=["Competitor Market", "Your Pricing"],
                    )
                )

                range_line = (
                    base.mark_rule(strokeWidth=5, color="#9AA4B2")
                    .encode(
                        x="x0:Q",
                        x2="x1:Q",
                    )
                    .transform_filter(alt.datum.band == "Range")
                )
                iqr_line = (
                    base.mark_rule(strokeWidth=12, color="#4B6CB7")
                    .encode(
                        x="x0:Q",
                        x2="x1:Q",
                    )
                    .transform_filter(alt.datum.band == "IQR")
                )

                markers = (
                    alt.Chart(markers_df)
                    .mark_point(filled=True, size=120)
                    .encode(
                        x=alt.X("price:Q", title="Price (£)"),
                        y=alt.Y(
                            "row:N",
                            title=None,
                            sort=["Competitor Market", "Your Pricing"],
                        ),
                        color=alt.Color(
                            "kind:N",
                            scale=alt.Scale(
                                domain=[
                                    "Floor",
                                    "Recommended",
                                    "Ceiling",
                                    "Break-even",
                                    "Median",
                                ],
                                range=[
                                    "#FF4B4B",
                                    "#21C354",
                                    "#FF9900",
                                    "#6B7280",
                                    "#1f2937",
                                ],
                            ),
                            legend=alt.Legend(title="Markers"),
                        ),
                        shape=alt.Shape(
                            "kind:N",
                            scale=alt.Scale(
                                domain=[
                                    "Floor",
                                    "Recommended",
                                    "Ceiling",
                                    "Break-even",
                                    "Median",
                                ],
                                range=[
                                    "circle",
                                    "diamond",
                                    "circle",
                                    "triangle-up",
                                    "square",
                                ],
                            ),
                            legend=None,
                        ),
                        tooltip=["label:N", alt.Tooltip("price:Q", format=".2f")],
                    )
                )

                labels = (
                    alt.Chart(markers_df)
                    .mark_text(align="left", dx=6, dy=-8, color="#4B5563")
                    .encode(
                        x="price:Q",
                        y=alt.Y(
                            "row:N",
                            sort=["Competitor Market", "Your Pricing"],
                            title=None,
                        ),
                        text="label:N",
                    )
                )

                chart = (
                    alt.layer(
                        range_line,
                        iqr_line,
                        markers,
                        labels,
                        data=segments_df,
                    )
                    .properties(height=220)
                    .resolve_scale(color="independent", shape="independent")
                )

                st.altair_chart(chart, use_container_width=True)
                st.caption(
                    "Reads left-to-right by price: grey line is full competitor range, blue band is the middle market (P25-P75), black diamond is median, and colored markers are your floor/recommended/ceiling plus break-even."
                )
            except Exception as chart_exc:  # noqa: BLE001
                st.warning(f"Could not render advanced positioning chart: {chart_exc}")
                st.bar_chart(
                    {
                        "Floor": [float(envelope["price_floor"])],
                        "Recommended": [float(envelope["recommended_launch_price"])],
                        "Ceiling": [float(envelope["price_ceiling"])],
                    }
                )

            # Viability assessment
            viability = engine.assess_price_viability(
                recommended_price=envelope["recommended_launch_price"],
                price_floor=envelope["price_floor"],
                price_ceiling=envelope["price_ceiling"],
                competitor_count=envelope["competitor_count"],
            )

            score = viability["viability_score"]
            if score >= 80:
                st.success(
                    f"✅ Viability Score: **{score:.0f}/100** — Strong positioning"
                )
            elif score >= 50:
                st.warning(
                    f"⚠️ Viability Score: **{score:.0f}/100** — Acceptable, monitor closely"
                )
            else:
                st.error(
                    f"❌ Viability Score: **{score:.0f}/100** — Revisit pricing strategy"
                )

            for rec in viability["recommendations"]:
                st.markdown(f"- {rec}")

        except ValueError as exc:
            st.error(f"Pricing calculation error: {exc}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Unexpected error: {exc}")

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 3: PPC Campaign Simulator
# ---------------------------------------------------------------------------
st.subheader("📣 PPC Campaign Simulator")
st.markdown(
    "Simulate a PPC campaign across your target keywords to estimate spend, ACoS, and time to Page 1."
)

ppc_col1, ppc_col2 = st.columns(2)

with ppc_col1:
    daily_budget = st.number_input(
        "Daily Budget £",
        min_value=1.0,
        max_value=10000.0,
        value=50.0,
        step=5.0,
        format="%.2f",
        help="Total daily PPC budget across all keywords.",
    )

with ppc_col2:
    target_acos = st.number_input(
        "Target ACoS %",
        min_value=1.0,
        max_value=150.0,
        value=30.0,
        step=1.0,
        format="%.1f",
        help="Target Advertising Cost of Sale percentage.",
    )

keywords_input = st.text_area(
    "Target Keywords (one per line)",
    placeholder="bamboo cutting board\nbamboo chopping board uk\nbest bamboo board kitchen\norganic bamboo board",
    height=120,
    help="Enter your target PPC keywords, one per line.",
    key="ppc_keywords",
)

simulate_ppc = st.button("🚀 Simulate Campaign", type="primary")

if simulate_ppc:
    raw_keywords = [k.strip() for k in keywords_input.strip().splitlines() if k.strip()]

    if not raw_keywords:
        st.warning("⚠️ Enter at least one keyword to simulate.")
    else:
        # Build keyword dicts — try to enrich with JS data if available
        keyword_dicts: list[dict[str, Any]] = []

        if js_available:
            try:
                js_client = JungleScoutClient()
                budget_ok = js_client.check_budget_available(
                    conn, pages=len(raw_keywords)
                )
                conn.commit()

                if budget_ok:
                    with st.spinner("Fetching keyword data from Jungle Scout…"):
                        for kw in raw_keywords:
                            try:
                                sov_result = js_client.get_share_of_voice(
                                    conn,
                                    keyword=kw,
                                    marketplace=marketplace,
                                    script_name="3_Risk_Pricing_Architect",
                                    launch_id=selected_launch_id,
                                )
                                conn.commit()

                                search_vol = 0
                                competition = "medium"
                                cpc_val = None

                                if sov_result is not None:
                                    try:
                                        attrs = getattr(sov_result, "data", None)
                                        if attrs and hasattr(attrs, "attributes"):
                                            a = attrs.attributes
                                            search_vol = int(
                                                getattr(
                                                    a, "exact_suggested_bid_median", 0
                                                )
                                                or 0
                                            )
                                            cpc_val = (
                                                float(
                                                    getattr(
                                                        a,
                                                        "exact_suggested_bid_median",
                                                        0,
                                                    )
                                                    or 0
                                                )
                                                or None
                                            )
                                    except Exception:  # noqa: BLE001
                                        pass

                                keyword_dicts.append(
                                    {
                                        "keyword": kw,
                                        "search_volume": search_vol,
                                        "competition_level": competition,
                                        "cpc": cpc_val,
                                        "source_field": "ppc_bid_exact",
                                    }
                                )
                            except Exception:  # noqa: BLE001
                                keyword_dicts.append(
                                    {
                                        "keyword": kw,
                                        "search_volume": 1000,
                                        "competition_level": "medium",
                                        "source_field": "ppc_bid_exact",
                                    }
                                )
                else:
                    st.warning(
                        "API budget insufficient for keyword enrichment — using estimates."
                    )
                    keyword_dicts = [
                        {
                            "keyword": kw,
                            "search_volume": 1000,
                            "competition_level": "medium",
                            "source_field": "ppc_bid_exact",
                        }
                        for kw in raw_keywords
                    ]
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                st.warning(f"Jungle Scout unavailable ({exc}) — using estimates.")
                keyword_dicts = [
                    {
                        "keyword": kw,
                        "search_volume": 1000,
                        "competition_level": "medium",
                        "source_field": "ppc_bid_exact",
                    }
                    for kw in raw_keywords
                ]
        else:
            keyword_dicts = [
                {
                    "keyword": kw,
                    "search_volume": 1000,
                    "competition_level": "medium",
                    "source_field": "ppc_bid_exact",
                }
                for kw in raw_keywords
            ]

        # Run simulation
        try:
            sim_results = engine.simulate_ppc_campaign(
                keywords=keyword_dicts,
                daily_budget=daily_budget,
                target_acos=target_acos,
                marketplace=marketplace,
            )
            st.session_state["ppc_simulation"] = sim_results
            st.session_state["ppc_marketplace"] = marketplace

            if sim_results:
                import pandas as pd

                df_ppc = pd.DataFrame(
                    [
                        {
                            "Keyword": r["keyword"],
                            "Search Volume": r["search_volume_exact"],
                            "Est. CPC (£)": f"£{r['estimated_cpc']:.2f}",
                            "Est. ACoS %": f"{r['estimated_acos_pct']:.1f}%",
                            "Daily Spend (£)": f"£{r['estimated_daily_spend']:.2f}",
                            "Days to Page 1": r["estimated_days_to_page1"],
                        }
                        for r in sim_results
                    ]
                )
                st.dataframe(df_ppc, use_container_width=True, hide_index=True)

                # Campaign totals
                total_daily_spend = sum(r["estimated_daily_spend"] for r in sim_results)
                blended_acos = (
                    sum(r["estimated_acos_pct"] for r in sim_results) / len(sim_results)
                    if sim_results
                    else 0.0
                )

                tot_col1, tot_col2, tot_col3 = st.columns(3)
                tot_col1.metric("Total Daily Spend", f"£{total_daily_spend:.2f}")
                tot_col2.metric("Blended ACoS Estimate", f"{blended_acos:.1f}%")
                tot_col3.metric("Keywords Simulated", len(sim_results))

                if total_daily_spend < daily_budget:
                    st.info(
                        f"ℹ️ Estimated spend (£{total_daily_spend:.2f}) is below your daily budget "
                        f"(£{daily_budget:.2f}). Consider adding more keywords."
                    )
                elif total_daily_spend >= daily_budget:
                    st.warning(
                        f"⚠️ Budget capped at £{daily_budget:.2f}/day. Some keywords may receive "
                        "reduced spend."
                    )
            else:
                st.warning("No simulation results generated.")

        except Exception as exc:  # noqa: BLE001
            st.error(f"PPC simulation error: {exc}")

elif st.session_state.get("ppc_simulation"):
    # Show previously simulated results
    sim_results = st.session_state["ppc_simulation"]
    import pandas as pd

    st.markdown("*Previously simulated results:*")
    df_ppc = pd.DataFrame(
        [
            {
                "Keyword": r["keyword"],
                "Search Volume": r["search_volume_exact"],
                "Est. CPC (£)": f"£{r['estimated_cpc']:.2f}",
                "Est. ACoS %": f"{r['estimated_acos_pct']:.1f}%",
                "Daily Spend (£)": f"£{r['estimated_daily_spend']:.2f}",
                "Days to Page 1": r["estimated_days_to_page1"],
            }
            for r in sim_results
        ]
    )
    st.dataframe(df_ppc, use_container_width=True, hide_index=True)

    total_daily_spend = sum(r["estimated_daily_spend"] for r in sim_results)
    blended_acos = (
        sum(r["estimated_acos_pct"] for r in sim_results) / len(sim_results)
        if sim_results
        else 0.0
    )
    tot_col1, tot_col2, tot_col3 = st.columns(3)
    tot_col1.metric("Total Daily Spend", f"£{total_daily_spend:.2f}")
    tot_col2.metric("Blended ACoS Estimate", f"{blended_acos:.1f}%")
    tot_col3.metric("Keywords Simulated", len(sim_results))

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 4: Risk Assessment
# ---------------------------------------------------------------------------
st.subheader("⚠️ Risk Analysis")
st.markdown(
    "Assess the key risk categories for this product launch. "
    "Be thorough — this informs your launch strategy and contingency planning."
)

_RISK_CATEGORIES = [
    (
        "safety",
        "🛡️ Safety Risk",
        "Product liability, injury risk, safety certifications required.",
    ),
    (
        "fragility",
        "📦 Fragility Risk",
        "Shipping damage potential, packaging requirements, breakage rate.",
    ),
    ("IP", "⚖️ IP Risk", "Patent infringement, trademark conflicts, design rights."),
    (
        "compliance",
        "📋 Compliance Risk",
        "Regulatory requirements, certifications, restricted products.",
    ),
    (
        "market",
        "📈 Market Risk",
        "Demand volatility, seasonality, market saturation trends.",
    ),
]

_SEVERITY_OPTIONS = ["Low", "Medium", "High", "Critical"]
_SEVERITY_COLORS = {
    "Low": "#21C354",
    "Medium": "#FF9900",
    "High": "#FF4B4B",
    "Critical": "#8B0000",
}

risk_assessments: dict[str, dict[str, str]] = {}

for risk_key, risk_label, risk_hint in _RISK_CATEGORIES:
    with st.expander(risk_label, expanded=True):
        st.caption(risk_hint)

        r_col1, r_col2 = st.columns([1, 3])

        with r_col1:
            saved_severity = str(
                st.session_state.get(f"risk_severity_{risk_key}", "Low")
            )
            severity_idx = (
                _SEVERITY_OPTIONS.index(saved_severity)
                if saved_severity in _SEVERITY_OPTIONS
                else 0
            )
            severity = st.selectbox(
                "Severity",
                options=_SEVERITY_OPTIONS,
                index=severity_idx,
                key=f"risk_severity_{risk_key}",
            )
            color = _SEVERITY_COLORS.get(severity, "#888")
            st.markdown(
                f"<span style='background:{color};color:white;padding:4px 12px;"
                f"border-radius:4px;font-weight:bold'>{severity}</span>",
                unsafe_allow_html=True,
            )

        with r_col2:
            description = st.text_area(
                "Risk Description",
                placeholder=f"Describe the {risk_label.split(' ', 1)[1].lower()} for this product…",
                height=80,
                key=f"risk_desc_{risk_key}",
            )
            mitigation = st.text_area(
                "Mitigation Strategy",
                placeholder="How will you mitigate or manage this risk?",
                height=80,
                key=f"risk_mit_{risk_key}",
            )

        risk_assessments[risk_key] = {
            "severity": severity,
            "description": description,
            "mitigation": mitigation,
        }

# Overall risk rating
severity_weights = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}
total_weight = sum(
    severity_weights.get(r["severity"], 1) for r in risk_assessments.values()
)
avg_weight = total_weight / len(risk_assessments) if risk_assessments else 1

if avg_weight <= 1.5:
    overall_risk = "🟢 Low"
    overall_color = "#21C354"
elif avg_weight <= 2.5:
    overall_risk = "🟡 Medium"
    overall_color = "#FF9900"
elif avg_weight <= 3.5:
    overall_risk = "🔴 High"
    overall_color = "#FF4B4B"
else:
    overall_risk = "🚨 Critical"
    overall_color = "#8B0000"

st.markdown(
    f"**Overall Risk Rating:** "
    f"<span style='background:{overall_color};color:white;padding:4px 12px;"
    f"border-radius:4px;font-weight:bold'>{overall_risk}</span>",
    unsafe_allow_html=True,
)

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 5: Save and Complete
# ---------------------------------------------------------------------------
st.subheader("💾 Save & Complete Module 3")
render_section_save_status(int(selected_launch_id), "pricing", "analysis")

save_col1, save_col2 = st.columns(2)

with save_col1:
    save_analysis = st.button(
        "💾 Save Pricing Analysis", type="primary", use_container_width=True
    )

with save_col2:
    # Check if pricing analysis already saved
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM launchpad.pricing_analysis WHERE launch_id = %s",
                (selected_launch_id,),
            )
            pricing_row = cur.fetchone()
            pricing_saved = int(pricing_row[0]) > 0 if pricing_row else False
        conn.commit()
    except Exception:  # noqa: BLE001
        conn.rollback()
        pricing_saved = False

    complete_stage = st.button(
        "✅ Complete Module 3 → Advance to Module 4",
        type="secondary",
        use_container_width=True,
        disabled=not pricing_saved,
        help="Save pricing analysis first to enable stage completion."
        if not pricing_saved
        else "",
    )

if save_analysis:
    envelope = st.session_state.get("price_envelope")
    ppc_sim = st.session_state.get("ppc_simulation", [])
    comp_analysis = st.session_state.get("competitor_analysis")

    if envelope is None:
        st.info(
            "ℹ️ Price envelope not calculated yet. Saving other available data now; "
            "you can calculate and save pricing later."
        )
    if not ppc_sim:
        st.info(
            "ℹ️ PPC simulation not run yet. Saving other available data now; "
            "you can simulate and save PPC later."
        )

    try:
        saved_pricing = False
        saved_ppc_rows = 0
        saved_risks = 0

        with conn.cursor() as cur:
            # 1. Save pricing_analysis (upsert) when envelope is available
            if envelope is not None:
                cur.execute(
                    """
                    INSERT INTO launchpad.pricing_analysis
                        (launch_id, marketplace, recommended_launch_price, price_floor,
                         price_ceiling, margin_estimate_pct, competitor_price_p25,
                         competitor_price_p50, competitor_price_p75, competitor_count,
                         data_freshness_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_DATE)
                    ON CONFLICT (launch_id, marketplace) DO UPDATE SET
                        recommended_launch_price = EXCLUDED.recommended_launch_price,
                        price_floor              = EXCLUDED.price_floor,
                        price_ceiling            = EXCLUDED.price_ceiling,
                        margin_estimate_pct      = EXCLUDED.margin_estimate_pct,
                        competitor_price_p25     = EXCLUDED.competitor_price_p25,
                        competitor_price_p50     = EXCLUDED.competitor_price_p50,
                        competitor_price_p75     = EXCLUDED.competitor_price_p75,
                        competitor_count         = EXCLUDED.competitor_count,
                        data_freshness_date      = EXCLUDED.data_freshness_date,
                        analyzed_at              = now()
                    """,
                    (
                        selected_launch_id,
                        marketplace,
                        envelope["recommended_launch_price"],
                        envelope["price_floor"],
                        envelope["price_ceiling"],
                        envelope.get("margin_estimate_pct"),
                        envelope["competitor_price_p25"],
                        envelope["competitor_price_p50"],
                        envelope["competitor_price_p75"],
                        envelope["competitor_count"],
                    ),
                )
                saved_pricing = True

            # 2. Save PPC simulation (upsert per keyword) when available
            for sim_row in ppc_sim:
                cur.execute(
                    """
                    INSERT INTO launchpad.ppc_simulation
                        (launch_id, marketplace, keyword, search_volume_exact,
                         estimated_cpc, estimated_acos_pct, estimated_tacos_pct,
                         organic_rank_target, estimated_daily_spend,
                         estimated_days_to_page1, source_field)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (launch_id, marketplace, keyword) DO UPDATE SET
                        search_volume_exact     = EXCLUDED.search_volume_exact,
                        estimated_cpc           = EXCLUDED.estimated_cpc,
                        estimated_acos_pct      = EXCLUDED.estimated_acos_pct,
                        estimated_tacos_pct     = EXCLUDED.estimated_tacos_pct,
                        organic_rank_target     = EXCLUDED.organic_rank_target,
                        estimated_daily_spend   = EXCLUDED.estimated_daily_spend,
                        estimated_days_to_page1 = EXCLUDED.estimated_days_to_page1,
                        source_field            = EXCLUDED.source_field,
                        simulated_at            = now()
                    """,
                    (
                        selected_launch_id,
                        marketplace,
                        sim_row["keyword"],
                        sim_row["search_volume_exact"],
                        sim_row["estimated_cpc"],
                        sim_row["estimated_acos_pct"],
                        sim_row["estimated_tacos_pct"],
                        sim_row["organic_rank_target"],
                        sim_row["estimated_daily_spend"],
                        sim_row["estimated_days_to_page1"],
                        sim_row["source_field"],
                    ),
                )
                saved_ppc_rows += 1

            # 3. Save risk assessments with entered descriptions
            cur.execute(
                """
                DELETE FROM launchpad.risk_assessment
                WHERE launch_id = %s
                  AND risk_category IN ('safety', 'fragility', 'IP', 'compliance', 'market')
                """,
                (selected_launch_id,),
            )

            for risk_key, risk_data in risk_assessments.items():
                if risk_data["description"].strip():
                    cur.execute(
                        """
                        INSERT INTO launchpad.risk_assessment
                            (launch_id, risk_category, risk_description, severity, mitigation)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            selected_launch_id,
                            risk_key,
                            risk_data["description"].strip(),
                            risk_data["severity"],
                            risk_data["mitigation"].strip() or None,
                        ),
                    )
                    saved_risks += 1

        if not saved_pricing and saved_ppc_rows == 0 and saved_risks == 0:
            conn.rollback()
            st.warning(
                "Nothing to save yet. Add at least one pricing envelope, PPC simulation, "
                "or risk description entry first."
            )
        else:
            conn.commit()
            record_section_save(int(selected_launch_id), "pricing", "analysis")
            st.success(
                f"✅ Saved Module 3 data for Launch #{selected_launch_id} ({marketplace})."
            )
            st.caption(
                f"Saved: pricing={'yes' if saved_pricing else 'no'}, "
                f"ppc_rows={saved_ppc_rows}, risk_items={saved_risks}."
            )
            st.rerun()

    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        st.error(f"Failed to save analysis: {exc}")

if complete_stage:
    try:
        can_advance, blockers = lsm.can_advance_stage(conn, selected_launch_id)
        conn.commit()

        if not can_advance:
            st.error("Cannot advance stage:")
            for blocker in blockers:
                st.markdown(f"- {blocker}")
        else:
            advanced = lsm.advance_stage(conn, selected_launch_id, validate=True)
            conn.commit()

            if advanced:
                new_launch = lsm.get_launch(conn, selected_launch_id)
                conn.commit()
                new_stage = (
                    int(new_launch["current_stage"])
                    if new_launch
                    else STAGE_PRICING + 1
                )
                st.success(
                    f"🎉 Stage 3 complete! Launch #{selected_launch_id} advanced to "
                    f"**Stage {new_stage}: Creative Studio**."
                )
                st.balloons()
            else:
                st.warning(
                    "Stage could not be advanced. It may already be at Stage 4 or higher."
                )

    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        st.error(f"Failed to advance stage: {exc}")

# ---------------------------------------------------------------------------
# Section 6: Opportunity Report Export (Google Doc)
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("📝 Opportunity Report (Google Doc)")
st.caption(
    "Generate a standardized report with fixed sections and upload it as a formatted Google Doc."
)

report_folder_id = (
    os.environ.get("LAUNCHPAD_OPPORTUNITY_REPORT_DRIVE_FOLDER_ID", "").strip()
    or os.environ.get("LAUNCHPAD_AUDIT_DRIVE_FOLDER_ID", "").strip()
    or _REPORT_DEFAULT_FOLDER_ID
)
st.caption("Drive path: `Launch_<id>_<ASIN>/Opportunity_Reports/<YYYY-MM-DD>/`")

report_col1, report_col2 = st.columns([2, 3])
with report_col1:
    generate_report = st.button(
        "📝 Save Opportunity Report to Google Docs",
        type="secondary",
        use_container_width=True,
    )

with report_col2:
    if report_folder_id == _REPORT_DEFAULT_FOLDER_ID:
        st.caption(
            "Using default folder ID. Set `LAUNCHPAD_OPPORTUNITY_REPORT_DRIVE_FOLDER_ID` "
            "to override."
        )

if generate_report:
    try:
        snapshot = _collect_report_snapshot(
            conn,
            launch,
            marketplace,
            competitor_analysis=st.session_state.get("competitor_analysis"),
            price_envelope=st.session_state.get("price_envelope"),
            ppc_simulation=st.session_state.get("ppc_simulation", []),
            risks=risk_assessments,
        )

        with st.spinner("Drafting structured opportunity report with Gemini…"):
            report_md = _generate_opportunity_report_markdown(snapshot)

        name_token = _safe_report_token(str(launch.get("launch_name") or ""))
        asin_token = "".join(
            ch for ch in str(launch.get("source_asin") or "").upper() if ch.isalnum()
        )
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = (
            f"opportunity_report_launch_{selected_launch_id}_{name_token}_{asin_token}_{stamp}.md"
            if name_token
            else f"opportunity_report_launch_{selected_launch_id}_{asin_token}_{stamp}.md"
        )

        from services.drive_audit import upload_markdown_gdoc_to_launch_stage_folder

        with st.spinner("Uploading report as Google Doc…"):
            uploaded = upload_markdown_gdoc_to_launch_stage_folder(
                report_text=report_md,
                file_name=filename,
                root_folder_id=report_folder_id,
                launch_id=int(selected_launch_id),
                stage_folder="Opportunity_Reports",
                source_asin=str(launch.get("source_asin") or ""),
            )

        web_link = str(uploaded.get("webViewLink") or "").strip()
        folder_path = str(uploaded.get("audit_folder_path") or "").strip()
        if web_link:
            st.success(f"Opportunity report saved: {web_link}")
        else:
            st.success(f"Opportunity report saved. File ID: {uploaded.get('id')}")
        if folder_path:
            st.caption(f"Folder: `{folder_path}`")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to generate/upload opportunity report: {exc}")

# ---------------------------------------------------------------------------
# Footer: current save status
# ---------------------------------------------------------------------------
st.markdown("---")
try:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT recommended_launch_price, price_floor, price_ceiling,
                   competitor_count, analyzed_at
            FROM launchpad.pricing_analysis
            WHERE launch_id = %s AND marketplace = %s
            ORDER BY analyzed_at DESC
            LIMIT 1
            """,
            (selected_launch_id, marketplace),
        )
        saved_row = cur.fetchone()
    conn.commit()

    if saved_row:
        st.caption(
            f"📌 Saved pricing analysis for {marketplace}: "
            f"Recommended £{saved_row[0]:.2f} | "
            f"Floor £{saved_row[1]:.2f} | "
            f"Ceiling £{saved_row[2]:.2f} | "
            f"{saved_row[3]} competitors | "
            f"Analysed {saved_row[4].strftime('%Y-%m-%d %H:%M') if hasattr(saved_row[4], 'strftime') else saved_row[4]}"
        )
    else:
        st.caption(
            "No pricing analysis saved yet for this launch/marketplace combination."
        )
except Exception:  # noqa: BLE001
    conn.rollback()
    st.caption("Could not load saved pricing status.")

render_bdl_footer(_theme_state)
