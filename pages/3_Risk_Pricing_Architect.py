"""
Stage 3: Risk & Pricing Architect

Determines optimal launch price and simulates PPC campaigns.
Analyses competitor pricing, calculates price envelope, simulates PPC
campaigns per keyword, and assesses product risks.
"""

from __future__ import annotations

import os
from typing import Any

import psycopg
import streamlit as st
from dotenv import load_dotenv

from services.db_connection import connect, normalize_dsn, resolve_dsn
from services.js_client import JungleScoutClient
from services.launch_state import STAGE_COMPLIANCE, STAGE_PRICING, LaunchStateManager
from services.pricing_engine import PricingEngine

# ---------------------------------------------------------------------------
# Page configuration — must be the first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Stage 3: Risk & Pricing Architect",
    page_icon="💰",
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
        dsn = normalize_dsn(raw_dsn)
        return connect(dsn)
    except Exception as exc:  # noqa: BLE001
        st.error(f"⚠️ Database connection failed: {exc}")
        return None


conn = get_connection()

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("💰 Stage 3: Risk & Pricing Architect")
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

selected_launch_id = st.selectbox(
    "Select Launch",
    options=launch_ids,
    index=default_idx,
    format_func=lambda lid: launch_options[lid],
)
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

current_stage = int(launch["current_stage"])

# ---------------------------------------------------------------------------
# Stage 2 completion check
# ---------------------------------------------------------------------------
if current_stage < STAGE_COMPLIANCE:
    st.warning(
        "⚠️ Stage 2 (Compliance Compass) must be completed before running pricing analysis. "
        f"This launch is currently at Stage {current_stage}."
    )
    st.stop()

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
        st.markdown(f"**Target Marketplaces:** {', '.join(launch['target_marketplaces'])}")

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
        "Enter competitor prices manually or fetch from Jungle Scout. "
        "Prices should be in the marketplace's native currency."
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
        "🔍 Fetch from Jungle Scout",
        disabled=not js_available,
        help="Requires JUNGLESCOUT_API_KEY and JUNGLESCOUT_API_KEY_NAME env vars.",
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

# Fetch from Jungle Scout
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
                try:
                    items = result.data if hasattr(result, "data") else []
                    for item in items:
                        attrs = getattr(item, "attributes", None) or {}
                        price = None
                        if isinstance(attrs, dict):
                            price = attrs.get("price") or attrs.get("current_price")
                        elif hasattr(attrs, "price"):
                            price = attrs.price
                        if price is not None:
                            try:
                                fetched_prices.append(float(price))
                            except (TypeError, ValueError):
                                pass
                except Exception:  # noqa: BLE001
                    pass

                if fetched_prices:
                    competitor_prices = fetched_prices
                    st.success(f"✅ Fetched {len(fetched_prices)} competitor prices from Jungle Scout.")
                    st.session_state["fetched_competitor_prices"] = fetched_prices
                else:
                    st.warning("Jungle Scout returned data but no prices could be extracted.")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        st.error(f"Jungle Scout error: {exc}")

# Use previously fetched prices if available
if not competitor_prices and st.session_state.get("fetched_competitor_prices"):
    competitor_prices = st.session_state["fetched_competitor_prices"]

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
        stability_color = {"stable": "🟢", "moderate": "🟡", "volatile": "🔴"}.get(stability, "⚪")
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
else:
    st.info("Enter competitor prices above to see the analysis.")

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 2: Price Envelope Calculation
# ---------------------------------------------------------------------------
st.subheader("🎯 Price Envelope Calculator")
st.markdown(
    "Calculate your viable price range based on costs and competitor data."
)

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
    amazon_fee_pct = st.number_input(
        "Amazon Referral Fee %",
        min_value=1.0,
        max_value=45.0,
        value=15.0,
        step=0.5,
        format="%.1f",
        help="Amazon referral fee percentage for your category (typically 8–15%).",
    )
    fulfillment_cost = st.number_input(
        "Fulfillment Cost £ (optional)",
        min_value=0.0,
        max_value=100.0,
        value=0.0,
        step=0.25,
        format="%.2f",
        help="FBA or FBM fulfilment cost per unit.",
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
                    envelope["recommended_launch_price"], cogs, amazon_fee_pct, fulfillment_cost
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
                mb_col3.metric("Amazon Fee", f"£{margin_detail['amazon_referral_fee']:.2f}")
                mb_col4.metric("Fulfillment", f"£{margin_detail['fulfillment_cost']:.2f}")

                mb_col5, mb_col6, mb_col7, mb_col8 = st.columns(4)
                mb_col5.metric("Total Costs", f"£{margin_detail['total_costs']:.2f}")
                mb_col6.metric("Net Profit", f"£{margin_detail['net_profit']:.2f}")
                mb_col7.metric("Gross Margin", f"{margin_detail['gross_margin_pct']:.1f}%")
                mb_col8.metric("Net Margin", f"{margin_detail['net_margin_pct']:.1f}%")

                st.caption(f"Break-even price: £{margin_detail['break_even_price']:.2f}")

            # Price positioning chart
            try:
                import altair as alt
                import pandas as pd

                price_points = pd.DataFrame(
                    {
                        "label": ["Floor", "Recommended", "Ceiling"],
                        "price": [
                            envelope["price_floor"],
                            envelope["recommended_launch_price"],
                            envelope["price_ceiling"],
                        ],
                        "color": ["#FF4B4B", "#21C354", "#FF9900"],
                    }
                )

                chart = (
                    alt.Chart(price_points)
                    .mark_bar(size=40)
                    .encode(
                        x=alt.X("label:N", title="Price Point", sort=["Floor", "Recommended", "Ceiling"]),
                        y=alt.Y("price:Q", title="Price (£)"),
                        color=alt.Color("color:N", scale=None, legend=None),
                        tooltip=["label:N", alt.Tooltip("price:Q", format="£.2f")],
                    )
                    .properties(title="Price Positioning", height=200)
                )
                st.altair_chart(chart, use_container_width=True)
            except ImportError:
                pass

            # Viability assessment
            viability = engine.assess_price_viability(
                recommended_price=envelope["recommended_launch_price"],
                price_floor=envelope["price_floor"],
                price_ceiling=envelope["price_ceiling"],
                competitor_count=envelope["competitor_count"],
            )

            score = viability["viability_score"]
            if score >= 80:
                st.success(f"✅ Viability Score: **{score:.0f}/100** — Strong positioning")
            elif score >= 50:
                st.warning(f"⚠️ Viability Score: **{score:.0f}/100** — Acceptable, monitor closely")
            else:
                st.error(f"❌ Viability Score: **{score:.0f}/100** — Revisit pricing strategy")

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
        keyword_dicts: list[dict] = []

        if js_available:
            try:
                js_client = JungleScoutClient()
                budget_ok = js_client.check_budget_available(conn, pages=len(raw_keywords))
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
                                            search_vol = int(getattr(a, "exact_suggested_bid_median", 0) or 0)
                                            cpc_val = float(getattr(a, "exact_suggested_bid_median", 0) or 0) or None
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
                    st.warning("API budget insufficient for keyword enrichment — using estimates.")
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
    ("safety", "🛡️ Safety Risk", "Product liability, injury risk, safety certifications required."),
    ("fragility", "📦 Fragility Risk", "Shipping damage potential, packaging requirements, breakage rate."),
    ("IP", "⚖️ IP Risk", "Patent infringement, trademark conflicts, design rights."),
    ("compliance", "📋 Compliance Risk", "Regulatory requirements, certifications, restricted products."),
    ("market", "📈 Market Risk", "Demand volatility, seasonality, market saturation trends."),
]

_SEVERITY_OPTIONS = ["Low", "Medium", "High", "Critical"]
_SEVERITY_COLORS = {
    "Low": "#21C354",
    "Medium": "#FF9900",
    "High": "#FF4B4B",
    "Critical": "#8B0000",
}

risk_assessments: dict[str, dict] = {}

for risk_key, risk_label, risk_hint in _RISK_CATEGORIES:
    with st.expander(risk_label, expanded=True):
        st.caption(risk_hint)

        r_col1, r_col2 = st.columns([1, 3])

        with r_col1:
            severity = st.selectbox(
                "Severity",
                options=_SEVERITY_OPTIONS,
                index=0,
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
total_weight = sum(severity_weights.get(r["severity"], 1) for r in risk_assessments.values())
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
st.subheader("💾 Save & Complete Stage 3")

save_col1, save_col2 = st.columns(2)

with save_col1:
    save_analysis = st.button("💾 Save Pricing Analysis", type="primary", use_container_width=True)

with save_col2:
    # Check if pricing analysis already saved
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM launchpad.pricing_analysis WHERE launch_id = %s",
                (selected_launch_id,),
            )
            pricing_saved = int(cur.fetchone()[0]) > 0
        conn.commit()
    except Exception:  # noqa: BLE001
        conn.rollback()
        pricing_saved = False

    complete_stage = st.button(
        "✅ Complete Stage 3 → Advance to Stage 4",
        type="secondary",
        use_container_width=True,
        disabled=not pricing_saved,
        help="Save pricing analysis first to enable stage completion." if not pricing_saved else "",
    )

if save_analysis:
    envelope = st.session_state.get("price_envelope")
    ppc_sim = st.session_state.get("ppc_simulation", [])
    comp_analysis = st.session_state.get("competitor_analysis")

    errors: list[str] = []

    # Validate we have minimum required data
    if envelope is None:
        errors.append("Price envelope not calculated — run 'Calculate Price Envelope' first.")
    if not ppc_sim:
        errors.append("PPC simulation not run — simulate a campaign first.")

    if errors:
        for err in errors:
            st.error(f"❌ {err}")
    else:
        try:
            with conn.cursor() as cur:
                # 1. Save pricing_analysis (upsert)
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

                # 2. Save PPC simulation (upsert per keyword)
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

                # 3. Save risk assessments
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

            conn.commit()
            st.success(
                f"✅ Pricing analysis saved for Launch #{selected_launch_id} "
                f"({marketplace}). {len(ppc_sim)} PPC keywords saved."
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
                new_stage = int(new_launch["current_stage"]) if new_launch else STAGE_PRICING + 1
                st.success(
                    f"🎉 Stage 3 complete! Launch #{selected_launch_id} advanced to "
                    f"**Stage {new_stage}: Creative Studio**."
                )
                st.balloons()
            else:
                st.warning("Stage could not be advanced. It may already be at Stage 4 or higher.")

    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        st.error(f"Failed to advance stage: {exc}")

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
        st.caption("No pricing analysis saved yet for this launch/marketplace combination.")
except Exception:  # noqa: BLE001
    conn.rollback()
    st.caption("Could not load saved pricing status.")
