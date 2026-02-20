"""
Stage 1: Opportunity Validator

Input a US ASIN, fetch competitor data via Jungle Scout, calculate a Pursuit Score,
and save results to the database. The score determines if the opportunity is worth
pursuing to subsequent stages.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg
import streamlit as st
from dotenv import load_dotenv

from services.db_connection import connect, resolve_dsn
from services.js_client import BudgetExhaustedError, JungleScoutClient
from services.launch_state import LaunchStateManager
from services.opportunity_scorer import (
    CATEGORY_GOLDMINE,
    CATEGORY_PROVEN,
    CATEGORY_SATURATED,
    OpportunityScorer,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Stage 1: Opportunity Validator",
    page_icon="🔍",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TARGET_MARKETPLACE_OPTIONS = ["UK", "DE", "FR", "IT", "ES"]

SCORE_COLORS = {
    CATEGORY_SATURATED: "#e74c3c",   # red
    CATEGORY_PROVEN: "#f39c12",      # orange
    CATEGORY_GOLDMINE: "#27ae60",    # green
}

SCORE_EMOJIS = {
    CATEGORY_SATURATED: "🔴",
    CATEGORY_PROVEN: "🟠",
    CATEGORY_GOLDMINE: "🟢",
}

# Load environment variables
load_dotenv()

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _get_dsn() -> str:
    """Resolve the database DSN once per session."""
    return resolve_dsn("LAUNCHPAD_DB_DSN", "MARKET_INTEL_DSN", "PG_DSN")


def _open_conn() -> psycopg.Connection:
    return connect(_get_dsn())


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------
def _init_session_state() -> None:
    defaults = {
        "launches": [],
        "selected_launch_id": None,
        "competitor_data": None,
        "pursuit_score": None,
        "pursuit_category": None,
        "score_breakdown": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def _load_launches() -> list[dict[str, Any]]:
    """Load all launches from DB into session state."""
    try:
        with _open_conn() as conn:
            mgr = LaunchStateManager()
            launches = mgr.list_launches(conn, limit=100)
            st.session_state["launches"] = launches
            return launches
    except Exception as exc:
        st.error(f"❌ Failed to load launches: {exc}")
        return []


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
def _render_header() -> None:
    st.title("🔍 Stage 1: Opportunity Validator")
    st.markdown(
        "Input a US ASIN to analyse the product opportunity across UK/EU markets. "
        "The **Pursuit Score** determines whether this niche is *Saturated*, *Proven*, or a *Goldmine*."
    )
    st.divider()


# ---------------------------------------------------------------------------
# Launch selector
# ---------------------------------------------------------------------------
def _render_launch_selector() -> dict[str, Any] | None:
    """Render launch selector. Returns selected launch dict or None."""
    st.subheader("📋 Select or Create Launch")

    launches = _load_launches()

    col_select, col_new = st.columns([3, 1])

    with col_select:
        if launches:
            options = {
                f"#{l['launch_id']} — {l['source_asin']} ({l.get('pursuit_category') or 'Not scored'})": l["launch_id"]
                for l in launches
            }
            options_list = ["— Create new launch —"] + list(options.keys())
            choice = st.selectbox("Select existing launch", options_list, key="launch_selector")

            if choice != "— Create new launch —":
                launch_id = options[choice]
                st.session_state["selected_launch_id"] = launch_id
                # Return the selected launch
                return next((l for l in launches if l["launch_id"] == launch_id), None)
            else:
                st.session_state["selected_launch_id"] = None
        else:
            st.info("No existing launches found. Fill in the form below to create one.")
            st.session_state["selected_launch_id"] = None

    with col_new:
        if st.button("🔄 Refresh", use_container_width=True):
            _load_launches()
            st.rerun()

    return None


def _render_launch_details(launch: dict[str, Any]) -> None:
    """Show a summary card for the selected launch."""
    with st.expander("📊 Launch Details", expanded=True):
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Launch ID", f"#{launch['launch_id']}")
        col2.metric("Source ASIN", launch["source_asin"])
        col3.metric("Stage", f"{launch['current_stage']} / 4")

        score = launch.get("pursuit_score")
        category = launch.get("pursuit_category") or "—"
        if score is not None:
            emoji = SCORE_EMOJIS.get(category, "")
            col4.metric("Pursuit Score", f"{score:.1f} {emoji}", delta=category)
        else:
            col4.metric("Pursuit Score", "Not calculated")

        if launch.get("product_description"):
            st.caption(f"**Description:** {launch['product_description']}")


# ---------------------------------------------------------------------------
# Data gathering form
# ---------------------------------------------------------------------------
def _render_data_gathering(selected_launch: dict[str, Any] | None) -> tuple[str, list[str]]:
    """Render ASIN input and target marketplace selector. Returns (asin, target_marketplaces)."""
    st.subheader("📥 Data Gathering")

    locked = selected_launch is not None

    col1, col2, col3 = st.columns([2, 1, 2])

    with col1:
        if locked:
            asin = st.text_input(
                "Source ASIN",
                value=selected_launch["source_asin"],
                disabled=True,
                help="ASIN is locked to the selected launch.",
            )
        else:
            asin = st.text_input(
                "Source ASIN",
                placeholder="e.g. B08N5WRWNW",
                help="Enter the US Amazon ASIN you want to analyse.",
            )

    with col2:
        st.text_input(
            "Source Marketplace",
            value="US",
            disabled=True,
            help="Source marketplace is always US.",
        )

    with col3:
        if locked:
            existing_targets = selected_launch.get("target_marketplaces") or TARGET_MARKETPLACE_OPTIONS
            target_marketplaces = st.multiselect(
                "Target Marketplaces",
                options=TARGET_MARKETPLACE_OPTIONS,
                default=existing_targets,
                disabled=True,
                help="Target marketplaces are locked to the selected launch.",
            )
        else:
            target_marketplaces = st.multiselect(
                "Target Marketplaces",
                options=TARGET_MARKETPLACE_OPTIONS,
                default=TARGET_MARKETPLACE_OPTIONS,
                help="Select which EU/UK markets to analyse.",
            )

    return asin.strip().upper() if asin else "", target_marketplaces


# ---------------------------------------------------------------------------
# Budget display
# ---------------------------------------------------------------------------
def _render_budget_status(conn: psycopg.Connection) -> dict[str, Any] | None:
    """Fetch and display API budget status. Returns budget dict or None on error."""
    try:
        client = JungleScoutClient()
        budget = client.get_budget_status(conn)
        remaining = int(budget["remaining_budget"])
        cap = int(budget["monthly_hard_cap"])
        used = cap - remaining

        col1, col2, col3 = st.columns(3)
        col1.metric("Monthly Cap", f"{cap:,} pages")
        col2.metric("Used This Month", f"{used:,} pages")

        if remaining <= 0:
            col3.metric("Remaining", f"{remaining:,} pages", delta="⚠️ Exhausted", delta_color="inverse")
        elif remaining < cap * 0.1:
            col3.metric("Remaining", f"{remaining:,} pages", delta="⚠️ Low", delta_color="inverse")
        else:
            col3.metric("Remaining", f"{remaining:,} pages")

        if budget.get("allow_override"):
            st.warning(f"⚠️ Budget override is active: {budget.get('override_reason', 'No reason given')}")

        return budget
    except Exception as exc:
        st.warning(f"⚠️ Could not fetch budget status: {exc}")
        return None


# ---------------------------------------------------------------------------
# Fetch competitors
# ---------------------------------------------------------------------------
def _fetch_competitors(
    conn: psycopg.Connection,
    asin: str,
    target_marketplaces: list[str],
    launch_id: int | None,
) -> list[dict[str, Any]] | None:
    """
    Fetch competitor data from Jungle Scout for each target marketplace.
    Returns a flat list of competitor dicts, or None on failure.
    """
    client = JungleScoutClient()
    all_competitors: list[dict[str, Any]] = []

    progress = st.progress(0, text="Checking budget...")

    # Pre-flight budget check
    pages_needed = len(target_marketplaces)
    if not client.check_budget_available(conn, pages=pages_needed):
        remaining = client.get_remaining_calls(conn)
        st.error(
            f"❌ API budget exhausted. Need {pages_needed} pages but only {remaining} remaining. "
            "Contact your admin to increase the monthly cap or enable override."
        )
        progress.empty()
        return None

    for i, marketplace in enumerate(target_marketplaces):
        progress.progress(
            (i + 1) / len(target_marketplaces),
            text=f"Fetching {marketplace} competitors ({i + 1}/{len(target_marketplaces)})...",
        )

        try:
            response = client.get_product_database(
                conn=conn,
                marketplace=marketplace,
                script_name="opportunity_validator",
                launch_id=launch_id,
                # Basic filters to find similar products
                min_monthly_revenue=500,
            )

            if response is None:
                st.warning(f"⚠️ Budget exhausted mid-fetch — skipped {marketplace}.")
                continue

            # Parse response — junglescout-client returns a response object
            # Extract product data from the response
            products = _parse_js_response(response, marketplace)
            all_competitors.extend(products)

        except Exception as exc:
            exc_str = str(exc)
            if "429" in exc_str or "rate limit" in exc_str.lower():
                st.error(f"❌ Jungle Scout rate limit hit for {marketplace}. Please wait and retry.")
            elif any(kw in exc_str.lower() for kw in ("connection", "timeout", "network")):
                st.error(f"❌ Network error fetching {marketplace}: {exc}")
            else:
                st.error(f"❌ Jungle Scout API error for {marketplace}: {exc}")
            progress.empty()
            return None

    progress.empty()
    return all_competitors


def _build_mock_competitors(asin: str, target_marketplaces: list[str]) -> list[dict[str, Any]]:
    """Return deterministic mock competitor rows without any API calls."""
    templates = [
        {"title": "Premium Stainless Bottle", "price": 24.99, "rating": 4.6, "review_count": 1840, "monthly_sales": 920},
        {"title": "Insulated Sports Flask", "price": 19.99, "rating": 4.4, "review_count": 980, "monthly_sales": 760},
        {"title": "Travel Thermo Tumbler", "price": 29.99, "rating": 4.7, "review_count": 2360, "monthly_sales": 1040},
        {"title": "Leakproof Daily Bottle", "price": 17.49, "rating": 4.2, "review_count": 640, "monthly_sales": 580},
        {"title": "Outdoor Vacuum Bottle", "price": 27.95, "rating": 4.5, "review_count": 1520, "monthly_sales": 860},
    ]

    competitors: list[dict[str, Any]] = []
    seed = (asin or "B000000000")[-4:]

    for mkt_index, marketplace in enumerate(target_marketplaces):
        price_shift = mkt_index * 0.75
        sales_shift = mkt_index * 40
        review_shift = mkt_index * 120

        for item_index, template in enumerate(templates, start=1):
            competitors.append(
                {
                    "marketplace": marketplace,
                    "asin": f"{seed}{marketplace}{item_index:02d}",
                    "title": f"{template['title']} ({marketplace})",
                    "price": round(float(template["price"]) + price_shift, 2),
                    "rating": float(template["rating"]),
                    "review_count": int(template["review_count"]) + review_shift,
                    "monthly_sales": int(template["monthly_sales"]) + sales_shift,
                }
            )

    return competitors


def _parse_js_response(response: Any, marketplace: str) -> list[dict[str, Any]]:
    """
    Parse a Jungle Scout product_database response into a list of competitor dicts.
    Handles both dict-like and object-like responses.
    """
    competitors: list[dict[str, Any]] = []

    try:
        # junglescout-client returns a response with .data attribute containing products
        if hasattr(response, "data"):
            data = response.data
        elif isinstance(response, dict):
            data = response.get("data", [])
        else:
            data = []

        # data may be a list of product objects or dicts
        if hasattr(data, "__iter__"):
            for item in data:
                comp = _extract_competitor(item, marketplace)
                if comp:
                    competitors.append(comp)

    except Exception as exc:
        logger.warning("Failed to parse JS response for %s: %s", marketplace, exc)

    return competitors


def _extract_competitor(item: Any, marketplace: str) -> dict[str, Any] | None:
    """Extract competitor fields from a JS product item (object or dict)."""
    try:
        def _get(obj: Any, *keys: str, default: Any = None) -> Any:
            for key in keys:
                if isinstance(obj, dict):
                    val = obj.get(key)
                else:
                    val = getattr(obj, key, None)
                if val is not None:
                    return val
            return default

        # Try attributes object (junglescout-client wraps in .attributes)
        attrs = _get(item, "attributes") or item

        asin = _get(attrs, "asin", "id", default="")
        title = _get(attrs, "title", "name", default="Unknown")
        price = float(_get(attrs, "price", "current_price", default=0) or 0)
        rating = float(_get(attrs, "rating", "avg_rating", default=0) or 0)
        reviews = int(_get(attrs, "reviews", "review_count", "num_reviews", default=0) or 0)
        monthly_sales = int(_get(attrs, "monthly_units_sold", "estimated_monthly_sales", default=0) or 0)

        return {
            "marketplace": marketplace,
            "asin": asin,
            "title": title[:80] if title else "Unknown",
            "price": price,
            "rating": rating,
            "review_count": reviews,
            "monthly_sales": monthly_sales,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Competitor table display
# ---------------------------------------------------------------------------
def _render_competitor_table(competitors: list[dict[str, Any]]) -> None:
    """Display competitor data in a table with summary metrics."""
    if not competitors:
        st.warning("No competitor data available.")
        return

    import pandas as pd

    st.subheader("🏪 Competitor Analysis")

    df = pd.DataFrame(competitors)

    # Summary metrics
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Competitors", len(df))

    if "rating" in df.columns and df["rating"].any():
        avg_rating = df[df["rating"] > 0]["rating"].mean()
        col2.metric("Avg Rating", f"{avg_rating:.2f} ⭐")
    else:
        col2.metric("Avg Rating", "N/A")

    if "review_count" in df.columns:
        avg_reviews = df["review_count"].mean()
        col3.metric("Avg Reviews", f"{avg_reviews:,.0f}")
    else:
        col3.metric("Avg Reviews", "N/A")

    if "price" in df.columns and df["price"].any():
        prices = df[df["price"] > 0]["price"]
        if not prices.empty:
            col4.metric("Min Price", f"${prices.min():.2f}")
            col5.metric("Max Price", f"${prices.max():.2f}")
        else:
            col4.metric("Min Price", "N/A")
            col5.metric("Max Price", "N/A")
    else:
        col4.metric("Min Price", "N/A")
        col5.metric("Max Price", "N/A")

    # Table
    display_cols = [c for c in ["marketplace", "asin", "title", "price", "rating", "review_count", "monthly_sales"] if c in df.columns]
    display_df = df[display_cols].copy()

    col_rename = {
        "marketplace": "Market",
        "asin": "ASIN",
        "title": "Title",
        "price": "Price ($)",
        "rating": "Rating",
        "review_count": "Reviews",
        "monthly_sales": "Est. Monthly Sales",
    }
    display_df = display_df.rename(columns={k: v for k, v in col_rename.items() if k in display_df.columns})

    st.dataframe(display_df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Pursuit Score calculation
# ---------------------------------------------------------------------------
def _compute_score_inputs(competitors: list[dict[str, Any]]) -> dict[str, float]:
    """Derive scorer inputs from competitor list."""
    if not competitors:
        return {
            "competitor_count": 0,
            "avg_review_count": 0.0,
            "review_velocity_30d": 0.0,
            "avg_rating": 3.0,
            "sales_velocity_score": 50.0,
            "keyword_difficulty": 50.0,
        }

    import statistics

    ratings = [c["rating"] for c in competitors if c.get("rating", 0) > 0]
    reviews = [c["review_count"] for c in competitors if c.get("review_count") is not None]
    sales = [c["monthly_sales"] for c in competitors if c.get("monthly_sales") is not None]

    avg_rating = statistics.mean(ratings) if ratings else 3.0
    avg_reviews = statistics.mean(reviews) if reviews else 0.0

    # Estimate review velocity: assume ~2% of total reviews are from last 30 days
    review_velocity = avg_reviews * 0.02

    # Normalise sales velocity: cap at 10,000 units/month → 100 pts
    avg_sales = statistics.mean(sales) if sales else 0.0
    sales_velocity_score = min(100.0, (avg_sales / 10_000.0) * 100.0)

    # Keyword difficulty: proxy from competitor count (more competitors = harder)
    competitor_count = len(competitors)
    keyword_difficulty = min(100.0, (competitor_count / 50.0) * 100.0)

    return {
        "competitor_count": competitor_count,
        "avg_review_count": avg_reviews,
        "review_velocity_30d": review_velocity,
        "avg_rating": avg_rating,
        "sales_velocity_score": sales_velocity_score,
        "keyword_difficulty": keyword_difficulty,
    }


def _render_pursuit_score(competitors: list[dict[str, Any]]) -> tuple[float | None, str | None]:
    """Render the Pursuit Score section. Returns (score, category) or (None, None)."""
    st.subheader("🎯 Pursuit Score")

    if st.button("⚡ Calculate Pursuit Score", type="primary", use_container_width=False):
        scorer = OpportunityScorer()
        inputs = _compute_score_inputs(competitors)

        score, category = scorer.calculate_pursuit_score(
            competitor_count=int(inputs["competitor_count"]),
            avg_review_count=inputs["avg_review_count"],
            review_velocity_30d=inputs["review_velocity_30d"],
            avg_rating=inputs["avg_rating"],
            sales_velocity_score=inputs["sales_velocity_score"],
            keyword_difficulty=inputs["keyword_difficulty"],
        )

        breakdown = scorer.get_score_breakdown(
            competitor_count=int(inputs["competitor_count"]),
            avg_review_count=inputs["avg_review_count"],
            review_velocity_30d=inputs["review_velocity_30d"],
            avg_rating=inputs["avg_rating"],
            sales_velocity_score=inputs["sales_velocity_score"],
            keyword_difficulty=inputs["keyword_difficulty"],
        )

        st.session_state["pursuit_score"] = score
        st.session_state["pursuit_category"] = category
        st.session_state["score_breakdown"] = breakdown

    score = st.session_state.get("pursuit_score")
    category = st.session_state.get("pursuit_category")
    breakdown = st.session_state.get("score_breakdown")

    if score is None:
        st.info("Click **Calculate Pursuit Score** to analyse the opportunity.")
        return None, None

    # Score display
    color = SCORE_COLORS.get(category, "#888888")
    emoji = SCORE_EMOJIS.get(category, "")

    col_score, col_gauge, col_cat = st.columns([1, 2, 1])

    with col_score:
        st.metric("Pursuit Score", f"{score:.1f} / 100")

    with col_gauge:
        st.progress(score / 100.0, text=f"{score:.1f}%")

    with col_cat:
        st.markdown(
            f"<div style='text-align:center; padding:8px; border-radius:8px; "
            f"background-color:{color}; color:white; font-weight:bold; font-size:1.1em;'>"
            f"{emoji} {category}</div>",
            unsafe_allow_html=True,
        )

    # Category description
    if category == CATEGORY_GOLDMINE:
        st.success("🟢 **Goldmine** — Strong opportunity with low barriers to entry. Proceed to Stage 2!")
    elif category == CATEGORY_PROVEN:
        st.warning("🟠 **Proven** — Validated market with moderate competition. Differentiation is key.")
    else:
        st.error("🔴 **Saturated** — High competition. Consider pivoting to a sub-niche.")

    # Score breakdown
    if breakdown:
        with st.expander("📊 Score Breakdown", expanded=False):
            import pandas as pd

            weights = {
                "Competitor Density": (breakdown.competitor_density_score, 0.20),
                "Review Moat": (breakdown.review_moat_score, 0.25),
                "Market Stability": (breakdown.market_stability_score, 0.15),
                "Rating Gap": (breakdown.rating_gap_score, 0.10),
                "Sales Velocity": (breakdown.sales_velocity_score, 0.20),
                "Keyword Difficulty": (breakdown.keyword_difficulty_score, 0.10),
            }

            rows = []
            for factor, (sub_score, weight) in weights.items():
                contribution = sub_score * weight
                rows.append({
                    "Factor": factor,
                    "Sub-Score": f"{sub_score:.1f}",
                    "Weight": f"{weight:.0%}",
                    "Contribution": f"{contribution:.2f}",
                })

            df_breakdown = pd.DataFrame(rows)
            st.dataframe(df_breakdown, use_container_width=True, hide_index=True)

            col_w, col_a = st.columns(2)
            col_w.metric("Weighted Score", f"{breakdown.weighted_score:.2f}")
            col_a.metric("Adjusted Score", f"{breakdown.adjusted_score:.2f}")

    # Recommendations
    scorer = OpportunityScorer()
    recommendations = scorer.get_score_recommendations(score, category)
    if recommendations:
        with st.expander("💡 Recommendations", expanded=True):
            for rec in recommendations:
                st.markdown(f"• {rec}")

    return score, category


# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------
def _save_results(
    conn: psycopg.Connection,
    asin: str,
    target_marketplaces: list[str],
    competitors: list[dict[str, Any]],
    score: float,
    category: str,
    selected_launch: dict[str, Any] | None,
) -> int | None:
    """
    Save pursuit score, competitor data, and review moat analysis to DB.
    Returns launch_id on success, None on failure.
    """
    mgr = LaunchStateManager()

    try:
        # Create or use existing launch
        if selected_launch is not None:
            launch_id = int(selected_launch["launch_id"])
        else:
            launch_id = mgr.create_launch(
                conn,
                source_asin=asin,
                source_marketplace="US",
                target_marketplaces=target_marketplaces,
            )
            conn.commit()

        # Update pursuit score and category
        mgr.update_launch(
            conn,
            launch_id,
            pursuit_score=score,
            pursuit_category=category,
            current_stage=2,
        )
        conn.commit()

        # Save review moat analysis per marketplace
        inputs = _compute_score_inputs(competitors)
        for marketplace in target_marketplaces:
            mkt_competitors = [c for c in competitors if c.get("marketplace") == marketplace]
            if not mkt_competitors:
                mkt_competitors = competitors  # fallback: use all

            mkt_inputs = _compute_score_inputs(mkt_competitors)

            # Determine moat strength
            avg_reviews = mkt_inputs["avg_review_count"]
            if avg_reviews < 100:
                moat_strength = "Weak"
            elif avg_reviews < 1000:
                moat_strength = "Medium"
            else:
                moat_strength = "Strong"

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO launchpad.review_moat_analysis
                        (launch_id, marketplace, competitor_count, avg_review_count,
                         avg_rating, review_velocity_30d, moat_strength)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (launch_id, marketplace) DO UPDATE SET
                        competitor_count    = EXCLUDED.competitor_count,
                        avg_review_count    = EXCLUDED.avg_review_count,
                        avg_rating          = EXCLUDED.avg_rating,
                        review_velocity_30d = EXCLUDED.review_velocity_30d,
                        moat_strength       = EXCLUDED.moat_strength,
                        analyzed_at         = now()
                    """,
                    (
                        launch_id,
                        marketplace,
                        int(mkt_inputs["competitor_count"]),
                        mkt_inputs["avg_review_count"],
                        mkt_inputs["avg_rating"],
                        mkt_inputs["review_velocity_30d"],
                        moat_strength,
                    ),
                )

        conn.commit()
        return launch_id

    except psycopg.Error as exc:
        conn.rollback()
        st.error(f"❌ Database error while saving: {exc}")
        logger.error("DB error saving opportunity analysis: %s", exc)
        return None
    except Exception as exc:
        conn.rollback()
        st.error(f"❌ Unexpected error while saving: {exc}")
        logger.error("Unexpected error saving opportunity analysis: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
def main() -> None:
    _init_session_state()
    _render_header()

    # --- Launch selector ---
    selected_launch = _render_launch_selector()
    if selected_launch:
        _render_launch_details(selected_launch)

    st.divider()

    # --- Data gathering ---
    asin, target_marketplaces = _render_data_gathering(selected_launch)

    # --- Fetch market data ---
    st.subheader("🌐 Market Data")

    # Budget status
    try:
        with _open_conn() as conn:
            budget = _render_budget_status(conn)
    except Exception as exc:
        st.error(f"❌ Cannot connect to database: {exc}")
        st.stop()

    fetch_disabled = not asin or not target_marketplaces
    if fetch_disabled and not asin:
        st.info("Enter a Source ASIN above to enable data fetching.")

    fetch_col, mock_col = st.columns([3, 2])

    with fetch_col:
        fetch_clicked = st.button(
            "🔍 Fetch Market Data",
            disabled=fetch_disabled,
            type="secondary",
            use_container_width=False,
        )

    with mock_col:
        mock_clicked = st.button(
            "🧪 Use Mock Data",
            disabled=fetch_disabled,
            type="secondary",
            help="Populate competitor data without calling Jungle Scout.",
            use_container_width=False,
        )

    if fetch_clicked:
        if not asin:
            st.error("❌ Please enter a Source ASIN.")
        elif len(asin) < 10:
            st.error("❌ ASIN must be at least 10 characters (e.g. B08N5WRWNW).")
        elif not target_marketplaces:
            st.error("❌ Please select at least one target marketplace.")
        else:
            launch_id = selected_launch["launch_id"] if selected_launch else None

            with st.spinner("Fetching competitor data from Jungle Scout..."):
                try:
                    with _open_conn() as conn:
                        competitors = _fetch_competitors(conn, asin, target_marketplaces, launch_id)
                except Exception as exc:
                    st.error(f"❌ Connection error: {exc}")
                    competitors = None

            if competitors is not None:
                st.session_state["competitor_data"] = competitors
                st.session_state["pursuit_score"] = None
                st.session_state["pursuit_category"] = None
                st.session_state["score_breakdown"] = None

                if competitors:
                    st.success(f"✅ Fetched {len(competitors)} competitors across {len(target_marketplaces)} marketplace(s).")
                else:
                    st.warning("⚠️ No competitors found. Try adjusting your filters or check the ASIN.")

    if mock_clicked:
        if not asin:
            st.error("❌ Please enter a Source ASIN.")
        elif len(asin) < 10:
            st.error("❌ ASIN must be at least 10 characters (e.g. B08N5WRWNW).")
        elif not target_marketplaces:
            st.error("❌ Please select at least one target marketplace.")
        else:
            competitors = _build_mock_competitors(asin, target_marketplaces)
            st.session_state["competitor_data"] = competitors
            st.session_state["pursuit_score"] = None
            st.session_state["pursuit_category"] = None
            st.session_state["score_breakdown"] = None
            st.success(
                f"✅ Loaded {len(competitors)} mock competitors across "
                f"{len(target_marketplaces)} marketplace(s). No API calls used."
            )

    # --- Competitor table ---
    competitors = st.session_state.get("competitor_data")
    if competitors is not None:
        _render_competitor_table(competitors)

        st.divider()

        # --- Pursuit Score ---
        score, category = _render_pursuit_score(competitors)

        # --- Save results ---
        if score is not None and category is not None:
            st.divider()
            st.subheader("💾 Save Analysis")

            if st.button("💾 Save Analysis", type="primary"):
                try:
                    with _open_conn() as conn:
                        launch_id = _save_results(
                            conn=conn,
                            asin=asin,
                            target_marketplaces=target_marketplaces,
                            competitors=competitors,
                            score=score,
                            category=category,
                            selected_launch=selected_launch,
                        )
                except Exception as exc:
                    st.error(f"❌ Connection error while saving: {exc}")
                    launch_id = None

                if launch_id is not None:
                    st.success(f"✅ Analysis saved! Launch ID: **#{launch_id}**")
                    st.session_state["selected_launch_id"] = launch_id

                    # Reload launches
                    _load_launches()

                    # Next stage button
                    if category in (CATEGORY_PROVEN, CATEGORY_GOLDMINE):
                        st.info("🚀 Ready to proceed! Navigate to **Stage 2: Compliance Compass** in the sidebar.")
                    else:
                        st.warning(
                            "⚠️ Score is Saturated. Consider pivoting before proceeding to Stage 2."
                        )


if __name__ == "__main__":
    main()
