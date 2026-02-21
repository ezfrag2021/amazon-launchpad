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
from services.workflow_ui import (
    record_section_save,
    render_readiness_panel,
    render_section_save_status,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Module 1: Opportunity Validator",
    page_icon="🔍",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TARGET_MARKETPLACE_OPTIONS = ["UK", "DE", "FR", "IT", "ES"]

SCORE_COLORS = {
    CATEGORY_SATURATED: "#e74c3c",  # red
    CATEGORY_PROVEN: "#f39c12",  # orange
    CATEGORY_GOLDMINE: "#27ae60",  # green
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
        "inferred_product_category": None,
        "pursuit_score": None,
        "pursuit_category": None,
        "score_breakdown": None,
        "op_hydrated_launch_id": None,
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


def _hydrate_from_saved_launch(selected_launch: dict[str, Any]) -> None:
    launch_id = int(selected_launch["launch_id"])
    if st.session_state.get("op_hydrated_launch_id") == launch_id:
        return

    st.session_state["op_hydrated_launch_id"] = launch_id

    # Restore score/category from persisted launch state.
    st.session_state["pursuit_score"] = selected_launch.get("pursuit_score")
    st.session_state["pursuit_category"] = selected_launch.get("pursuit_category")
    st.session_state["score_breakdown"] = None
    st.session_state["inferred_product_category"] = selected_launch.get(
        "product_category"
    )

    # Rehydrate competitors from cache-backed fetch so page fields repopulate after reload.
    try:
        with _open_conn() as conn:
            competitors = _fetch_competitors(
                conn=conn,
                asin=str(selected_launch.get("source_asin") or ""),
                target_marketplaces=list(
                    selected_launch.get("target_marketplaces")
                    or TARGET_MARKETPLACE_OPTIONS
                ),
                launch_id=launch_id,
                source_marketplace=str(
                    selected_launch.get("source_marketplace") or "US"
                ),
                source_context=str(selected_launch.get("product_description") or ""),
                use_cache=True,
            )
            if competitors is not None:
                st.session_state["competitor_data"] = competitors
    except Exception as exc:
        logger.info(
            "Could not auto-hydrate competitor data for launch %s: %s", launch_id, exc
        )


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
def _render_header() -> None:
    st.title("🔍 Module 1: Opportunity Validator")
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
                f"#{l['launch_id']} — {l['source_asin']} ({l.get('pursuit_category') or 'Not scored'})": l[
                    "launch_id"
                ]
                for l in launches
            }
            options_list = ["— Create new launch —"] + list(options.keys())
            choice = st.selectbox(
                "Select existing launch", options_list, key="launch_selector"
            )

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
def _render_data_gathering(
    selected_launch: dict[str, Any] | None,
) -> tuple[str, list[str]]:
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
            existing_targets = (
                selected_launch.get("target_marketplaces") or TARGET_MARKETPLACE_OPTIONS
            )
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
            col3.metric(
                "Remaining",
                f"{remaining:,} pages",
                delta="⚠️ Exhausted",
                delta_color="inverse",
            )
        elif remaining < cap * 0.1:
            col3.metric(
                "Remaining",
                f"{remaining:,} pages",
                delta="⚠️ Low",
                delta_color="inverse",
            )
        else:
            col3.metric("Remaining", f"{remaining:,} pages")

        if budget.get("allow_override"):
            st.warning(
                f"⚠️ Budget override is active: {budget.get('override_reason', 'No reason given')}"
            )

        return budget
    except Exception as exc:
        st.warning(f"⚠️ Could not fetch budget status: {exc}")
        return None


def _get_cache_status_for_asin(
    conn: psycopg.Connection,
    asin: str,
    marketplaces: list[str] | None = None,
) -> dict[str, Any] | None:
    """Return latest Jungle Scout cache metadata for the ASIN."""
    normalized_asin = "".join(ch for ch in (asin or "").upper() if ch.isalnum())
    if len(normalized_asin) < 10:
        return None

    mkt_codes = [m.upper() for m in (marketplaces or []) if m]
    if "UK" in mkt_codes and "GB" not in mkt_codes:
        mkt_codes.append("GB")
    if "GB" in mkt_codes and "UK" not in mkt_codes:
        mkt_codes.append("UK")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MAX(fetched_at), COUNT(*)
            FROM launchpad.jungle_scout_cache
            WHERE UPPER(asin) = %s
              AND endpoint IN ('keywords_by_asin', 'sales_estimates', 'product_database')
              AND (
                    %s::text[] IS NULL
                    OR cardinality(%s::text[]) = 0
                    OR marketplace = ANY(%s::text[])
                  )
            """,
            (
                normalized_asin,
                mkt_codes if mkt_codes else None,
                mkt_codes if mkt_codes else [],
                mkt_codes if mkt_codes else [],
            ),
        )
        row = cur.fetchone()

    if not row or row[0] is None:
        return None

    return {
        "latest_fetched_at": row[0],
        "cache_rows": int(row[1] or 0),
    }


def _get_cache_status_for_launch(
    conn: psycopg.Connection,
    launch_id: int,
) -> dict[str, Any] | None:
    """Fallback cache signal for saved launches using API call ledger."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MAX(called_at), COUNT(*)
            FROM launchpad.api_call_ledger
            WHERE launch_id = %s
              AND script_name = 'opportunity_validator'
              AND endpoint IN ('keywords_by_asin', 'share_of_voice', 'sales_estimates', 'product_database')
            """,
            (launch_id,),
        )
        row = cur.fetchone()

    if not row or row[0] is None:
        return None

    return {
        "latest_fetched_at": row[0],
        "cache_rows": int(row[1] or 0),
    }


# ---------------------------------------------------------------------------
# Fetch competitors
# ---------------------------------------------------------------------------
def _fetch_competitors(
    conn: psycopg.Connection,
    asin: str,
    target_marketplaces: list[str],
    launch_id: int | None,
    source_marketplace: str = "US",
    source_context: str = "",
    use_cache: bool = True,
) -> list[dict[str, Any]] | None:
    """
    Fetch competitor data from Jungle Scout for each target marketplace.
    Returns a flat list of competitor dicts, or None on failure.
    """
    client = JungleScoutClient()
    all_competitors: list[dict[str, Any]] = []
    max_keywords = 4
    max_asins_per_keyword = 3

    generic_tokens = {
        "cream",
        "aging",
        "anti",
        "best",
        "for",
        "and",
        "with",
        "skin",
        "face",
        "body",
        "women",
        "men",
        "serum",
        "moisturizer",
        "wrinkles",
        "dark",
        "spots",
        "solution",
        "care",
        "extra",
        "strength",
        "fast",
        "healing",
    }
    stop_tokens = {"for", "and", "with", "the", "from", "your", "you", "that", "this"}
    context_tokens = {
        token.lower()
        for token in source_context.replace("-", " ").replace("/", " ").split()
        if len(token) >= 4
    }

    def _extract_keywords(resp: Any) -> list[str]:
        if not isinstance(resp, dict):
            return []
        rows = resp.get("data", [])
        candidates: list[tuple[str, float]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            attrs = row.get("attributes", {})
            if not isinstance(attrs, dict):
                continue
            keyword = (attrs.get("name") or "").strip()
            if len(keyword) < 3:
                continue

            normalized_keyword = keyword.lower().strip()
            if len(normalized_keyword) == 10 and normalized_keyword.isalnum():
                # Skip ASIN-like tokens (e.g. b00bwtka94)
                continue

            raw_tokens = [
                t.strip(" ,.!?()[]{}\"'").lower() for t in normalized_keyword.split()
            ]
            tokens = [t for t in raw_tokens if len(t) >= 3 and t not in stop_tokens]
            if not tokens:
                continue
            if all(t in generic_tokens for t in tokens):
                continue

            sv = (
                attrs.get("monthly_search_volume_exact")
                or attrs.get("monthly_search_volume_broad")
                or 0
            )
            try:
                sv_score = float(sv)
            except Exception:
                sv_score = 0.0

            try:
                organic_rank = int(attrs.get("organic_rank") or 999)
            except Exception:
                organic_rank = 999

            rank_score = 1.0 / max(1, organic_rank)
            overlap = len(set(tokens) & context_tokens)
            overlap_boost = 1.0 + (0.6 * overlap)
            if context_tokens and overlap == 0:
                overlap_boost = 0.35

            specificity = min(len(tokens), 6) / 6.0
            score = (
                (sv_score / 1000.0) + (rank_score * 150.0) + specificity
            ) * overlap_boost
            candidates.append((keyword, score))
        candidates.sort(key=lambda item: item[1], reverse=True)
        deduped: list[str] = []
        seen: set[str] = set()
        for keyword, _ in candidates:
            k = keyword.lower()
            if k in seen:
                continue
            seen.add(k)
            deduped.append(keyword)
            if len(deduped) >= max_keywords:
                break
        return deduped

    def _extract_categories(resp: Any) -> list[str]:
        if not isinstance(resp, dict):
            return []
        rows = resp.get("data", [])
        counts: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            attrs = row.get("attributes", {})
            if not isinstance(attrs, dict):
                continue
            category = str(attrs.get("dominant_category") or "").strip()
            if not category:
                continue
            counts[category] = counts.get(category, 0) + 1
        ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        return [name for name, _ in ordered[:2]]

    def _refine_keywords_with_gemini(candidates: list[str]) -> list[str]:
        """Use Gemini to pick niche-specific keywords from candidate set."""
        if not candidates:
            return candidates
        try:
            from services.auth_manager import get_generative_client

            genai = get_generative_client()
            model = genai.GenerativeModel("gemini-2.0-flash-exp")

            prompt = f"""
You are selecting Amazon niche-definition keywords.

Source product context:
{source_context or "N/A"}

Candidate keywords from Jungle Scout:
{json.dumps(candidates, ensure_ascii=True)}

Goal:
- Pick 2 to 4 keywords that best define the same buyer intent and product niche as the source product.
- Prefer specific high-intent long-tail terms.
- Avoid broad generic terms that could cross categories.

Return strict JSON only in this format:
{{"keywords": ["kw1", "kw2", "kw3"]}}
"""

            response = model.generate_content(prompt)
            raw = (response.text or "").strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

            parsed = json.loads(raw)
            selected = parsed.get("keywords", []) if isinstance(parsed, dict) else []
            if not isinstance(selected, list):
                return candidates

            allowed = {c.lower(): c for c in candidates}
            refined: list[str] = []
            seen: set[str] = set()
            for kw in selected:
                k = str(kw).strip().lower()
                if k in allowed and k not in seen:
                    seen.add(k)
                    refined.append(allowed[k])
                if len(refined) >= 4:
                    break

            return refined or candidates
        except Exception as exc:
            logger.info(
                "Gemini keyword refinement unavailable, using heuristic keywords: %s",
                exc,
            )
            return candidates

    def _extract_sov_asins(resp: Any) -> list[dict[str, Any]]:
        if not isinstance(resp, dict):
            return []
        attrs = (resp.get("data") or {}).get("attributes") or {}
        if not isinstance(attrs, dict):
            return []
        top_asins = attrs.get("top_asins", [])
        if not isinstance(top_asins, list):
            return []
        rows: list[dict[str, Any]] = []
        for item in top_asins[:max_asins_per_keyword]:
            if not isinstance(item, dict):
                continue
            asin_value = str(item.get("asin") or "").strip().upper()
            if len(asin_value) != 10 or not asin_value.isalnum():
                continue
            if asin_value == "0000000000" or asin_value == "0":
                continue
            if asin_value:
                item = {**item, "asin": asin_value}
                rows.append(item)
        return rows

    def _extract_monthly_units_and_price(resp: Any) -> tuple[int, float]:
        if not isinstance(resp, dict):
            return 0, 0.0
        data = resp.get("data", [])
        if not isinstance(data, list) or not data:
            return 0, 0.0
        attrs = (data[0] or {}).get("attributes", {})
        if not isinstance(attrs, dict):
            return 0, 0.0
        series = attrs.get("data", [])
        if not isinstance(series, list) or not series:
            return 0, 0.0

        units = [
            int(row.get("estimated_units_sold") or 0)
            for row in series
            if isinstance(row, dict)
        ]
        prices = [
            float(row.get("last_known_price") or 0)
            for row in series
            if isinstance(row, dict) and row.get("last_known_price") is not None
        ]

        if not units:
            return 0, 0.0

        observed_days = max(1, len(units))
        est_monthly_units = int(round(sum(units) * (30.0 / observed_days)))
        avg_price = round(sum(prices) / len(prices), 2) if prices else 0.0
        return est_monthly_units, avg_price

    def _fallback_keyword_competitors(marketplace: str) -> list[dict[str, Any]]:
        try:
            fallback_resp = client.get_product_database(
                conn=conn,
                marketplace=marketplace,
                script_name="opportunity_validator",
                launch_id=launch_id,
                use_cache=use_cache,
                ttl_hours=24,
                include_keywords=fallback_keywords,
                page_size=100,
            )
        except Exception as exc:
            logger.warning(
                "Fallback product-database fetch failed for %s: %s", marketplace, exc
            )
            return []

        fallback_rows = (
            _parse_js_response(fallback_resp, marketplace)
            if fallback_resp is not None
            else []
        )
        keyword_tokens = {
            token.lower()
            for keyword in fallback_keywords
            for token in keyword.split()
            if len(token) >= 4
        }
        if keyword_tokens:
            filtered_rows = [
                row
                for row in fallback_rows
                if any(
                    token in str(row.get("title", "")).lower()
                    for token in keyword_tokens
                )
            ]
            # If filtering is too strict for marketplace wording, keep unfiltered keyword query rows.
            fallback_rows = filtered_rows or fallback_rows
        return fallback_rows

    def _enrich_with_sales_estimates(
        rows: list[dict[str, Any]],
        marketplace: str,
        max_rows: int = 20,
    ) -> list[dict[str, Any]]:
        """Fill missing monthly sales and price using sales_estimates."""
        enriched: list[dict[str, Any]] = []
        attempted = 0

        for row in rows:
            item = dict(row)
            asin_candidate = str(item.get("asin") or "").strip().upper()
            needs_sales = int(item.get("monthly_sales") or 0) <= 0

            if (
                needs_sales
                and attempted < max_rows
                and len(asin_candidate) == 10
                and asin_candidate.isalnum()
                and client.check_budget_available(conn, pages=1)
            ):
                try:
                    sales_resp = client.get_sales_estimates(
                        conn=conn,
                        asin=asin_candidate,
                        marketplace=marketplace,
                        script_name="opportunity_validator",
                        launch_id=launch_id,
                        use_cache=use_cache,
                        ttl_hours=24,
                    )
                    attempted += 1
                    if sales_resp is not None:
                        monthly_sales, est_price = _extract_monthly_units_and_price(
                            sales_resp
                        )
                        if monthly_sales > 0:
                            item["monthly_sales"] = monthly_sales
                        if est_price > 0:
                            item["price"] = est_price
                except Exception as exc:
                    logger.warning(
                        "Sales-estimate enrichment failed for %s/%s: %s",
                        marketplace,
                        asin_candidate,
                        exc,
                    )

            enriched.append(item)

        return enriched

    progress = st.progress(0, text="Deriving high-intent niche keywords...")

    try:
        keyword_resp = client.get_keywords_by_asin(
            conn=conn,
            asin=asin,
            marketplace=source_marketplace,
            script_name="opportunity_validator",
            launch_id=launch_id,
            use_cache=use_cache,
            ttl_hours=24,
        )
    except Exception as exc:
        progress.empty()
        st.error(f"❌ Failed to derive niche keywords from ASIN {asin}: {exc}")
        return None

    if keyword_resp is None:
        progress.empty()
        st.error("❌ Budget exhausted while deriving seed keywords.")
        return None

    raw_keywords = _extract_keywords(keyword_resp)
    keywords = _refine_keywords_with_gemini(raw_keywords)

    anchor_terms = {
        token.lower()
        for token in source_context.replace("-", " ").replace("/", " ").split()
        if len(token) >= 5
    }
    anchor_terms.update({"arnica", "bruise", "bruising", "purpura", "vitamin", "thin"})
    broad_candidates = [
        kw
        for kw in raw_keywords
        if any(anchor in kw.lower() for anchor in anchor_terms)
    ]
    source_lower = source_context.lower()
    synthetic_keywords: list[str] = []
    if "arnica" in source_lower and (
        "bruise" in source_lower or "bruising" in source_lower
    ):
        synthetic_keywords.extend(["arnica bruise cream", "bruise cream"])
    if "vitamin k" in source_lower:
        synthetic_keywords.append("vitamin k cream")

    fallback_keywords = list(
        dict.fromkeys(synthetic_keywords + keywords + broad_candidates)
    )[:8]

    category_hints = _extract_categories(keyword_resp)
    inferred_category = category_hints[0] if category_hints else None
    st.session_state["inferred_product_category"] = inferred_category
    if not keywords and not fallback_keywords:
        progress.empty()
        st.error("❌ Could not extract target keywords from source ASIN.")
        return None

    st.caption(
        "Using seed keywords: "
        + ", ".join(f"`{kw}`" for kw in (keywords or fallback_keywords)[:4])
    )
    if inferred_category:
        st.caption(f"Detected product category hint: `{inferred_category}`")

    pages_needed = 1 + (
        len(target_marketplaces) * ((len(keywords) * (1 + max_asins_per_keyword)) + 1)
    )
    if not client.check_budget_available(conn, pages=pages_needed):
        remaining = client.get_remaining_calls(conn)
        progress.empty()
        st.error(
            f"❌ API budget too low for niche-definition fetch. Need ~{pages_needed} calls, "
            f"remaining {remaining}. Reduce marketplaces or increase budget cap."
        )
        return None

    steps = max(1, len(target_marketplaces))
    for i, marketplace in enumerate(target_marketplaces):
        progress.progress(
            (i + 1) / steps,
            text=f"Defining {marketplace} niche via keyword-based competitor discovery ({i + 1}/{steps})...",
        )

        marketplace_candidates: dict[str, dict[str, Any]] = {}
        for keyword in keywords:
            try:
                sov_resp = client.get_share_of_voice(
                    conn=conn,
                    keyword=keyword,
                    marketplace=marketplace,
                    script_name="opportunity_validator",
                    launch_id=launch_id,
                    use_cache=use_cache,
                    ttl_hours=24,
                )
            except Exception as exc:
                logger.warning(
                    "Share-of-voice fetch failed for %s/%s: %s",
                    marketplace,
                    keyword,
                    exc,
                )
                continue

            if sov_resp is None:
                continue

            for row in _extract_sov_asins(sov_resp):
                asin_key = str(row.get("asin"))
                if asin_key and asin_key not in marketplace_candidates:
                    marketplace_candidates[asin_key] = row

        if not marketplace_candidates:
            fallback_rows = _fallback_keyword_competitors(marketplace)
            all_competitors.extend(
                _enrich_with_sales_estimates(fallback_rows, marketplace)
            )
            continue

        added_for_marketplace = 0
        for comp_asin, meta in marketplace_candidates.items():
            try:
                sales_resp = client.get_sales_estimates(
                    conn=conn,
                    asin=comp_asin,
                    marketplace=marketplace,
                    script_name="opportunity_validator",
                    launch_id=launch_id,
                    use_cache=use_cache,
                    ttl_hours=24,
                )
            except Exception as exc:
                logger.warning(
                    "Sales-estimate fetch failed for %s/%s: %s",
                    marketplace,
                    comp_asin,
                    exc,
                )
                continue

            if sales_resp is None:
                continue

            monthly_sales, price = _extract_monthly_units_and_price(sales_resp)
            all_competitors.append(
                {
                    "marketplace": marketplace,
                    "asin": comp_asin,
                    "title": str(meta.get("name") or "Unknown")[:80],
                    "price": price,
                    "rating": 0.0,
                    "review_count": 0,
                    "monthly_sales": monthly_sales,
                }
            )
            added_for_marketplace += 1

        # If SOV ASINs failed downstream (e.g. sales_estimates unavailable),
        # fall back to keyword-scoped product_database rather than returning empty.
        if added_for_marketplace == 0:
            fallback_rows = _fallback_keyword_competitors(marketplace)
            all_competitors.extend(
                _enrich_with_sales_estimates(fallback_rows, marketplace)
            )

    progress.empty()
    return all_competitors


def _build_mock_competitors(
    asin: str, target_marketplaces: list[str]
) -> list[dict[str, Any]]:
    """Return deterministic mock competitor rows without any API calls."""
    templates = [
        {
            "title": "Premium Stainless Bottle",
            "price": 24.99,
            "rating": 4.6,
            "review_count": 1840,
            "monthly_sales": 920,
        },
        {
            "title": "Insulated Sports Flask",
            "price": 19.99,
            "rating": 4.4,
            "review_count": 980,
            "monthly_sales": 760,
        },
        {
            "title": "Travel Thermo Tumbler",
            "price": 29.99,
            "rating": 4.7,
            "review_count": 2360,
            "monthly_sales": 1040,
        },
        {
            "title": "Leakproof Daily Bottle",
            "price": 17.49,
            "rating": 4.2,
            "review_count": 640,
            "monthly_sales": 580,
        },
        {
            "title": "Outdoor Vacuum Bottle",
            "price": 27.95,
            "rating": 4.5,
            "review_count": 1520,
            "monthly_sales": 860,
        },
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
        if not asin:
            asin = _get(item, "asin", "id", default="")
        asin = str(asin or "")
        if "/" in asin:
            asin = asin.rsplit("/", 1)[-1]
        asin = asin.strip().upper()
        title = _get(attrs, "title", "name", default="Unknown")
        price = float(_get(attrs, "price", "current_price", default=0) or 0)
        rating = float(_get(attrs, "rating", "avg_rating", default=0) or 0)
        reviews = int(
            _get(attrs, "reviews", "review_count", "num_reviews", default=0) or 0
        )
        monthly_sales = int(
            _get(attrs, "monthly_units_sold", "estimated_monthly_sales", default=0) or 0
        )

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

    def _marketplace_currency(code: str) -> tuple[str, str]:
        market = (code or "").upper()
        if market == "UK":
            return "GBP", "GBP"
        if market in {"DE", "FR", "IT", "ES", "NL", "SE", "PL"}:
            return "EUR", "EUR"
        if market == "US":
            return "USD", "USD"
        return "USD", "USD"

    dominant_market = "US"
    if "marketplace" in df.columns and not df["marketplace"].empty:
        dominant_market = str(df["marketplace"].mode().iat[0])
    currency_code, currency_label = _marketplace_currency(dominant_market)

    # Summary metrics
    col1, col2, col3, col4, col5, col6 = st.columns(6)
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
            col4.metric("Min Price", f"{prices.min():.2f} {currency_label}")
            col5.metric("Max Price", f"{prices.max():.2f} {currency_label}")
        else:
            col4.metric("Min Price", "N/A")
            col5.metric("Max Price", "N/A")
    else:
        col4.metric("Min Price", "N/A")
        col5.metric("Max Price", "N/A")

    if {"monthly_sales", "price"}.issubset(df.columns):
        niche_value = float(
            (df["monthly_sales"].fillna(0) * df["price"].fillna(0)).sum()
        )
        col6.metric("Est. Niche Value / Month", f"{niche_value:,.0f} {currency_label}")
    else:
        col6.metric("Est. Niche Value / Month", "N/A")

    # Table
    display_cols = [
        c
        for c in [
            "marketplace",
            "asin",
            "title",
            "price",
            "rating",
            "review_count",
            "monthly_sales",
        ]
        if c in df.columns
    ]
    display_df = df[display_cols].copy()

    col_rename = {
        "marketplace": "Market",
        "asin": "ASIN",
        "title": "Title",
        "price": f"Price ({currency_code})",
        "rating": "Rating",
        "review_count": "Reviews",
        "monthly_sales": "Est. Monthly Sales",
    }
    display_df = display_df.rename(
        columns={k: v for k, v in col_rename.items() if k in display_df.columns}
    )

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
    reviews = [
        c["review_count"] for c in competitors if c.get("review_count") is not None
    ]
    sales = [
        c["monthly_sales"] for c in competitors if c.get("monthly_sales") is not None
    ]

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


def _render_pursuit_score(
    competitors: list[dict[str, Any]],
) -> tuple[float | None, str | None]:
    """Render the Pursuit Score section. Returns (score, category) or (None, None)."""
    st.subheader("🎯 Pursuit Score")

    if st.button(
        "⚡ Calculate Pursuit Score", type="primary", use_container_width=False
    ):
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

    score_raw = st.session_state.get("pursuit_score")
    try:
        score = float(score_raw) if score_raw is not None else None
    except (TypeError, ValueError):
        score = None

    category_raw = st.session_state.get("pursuit_category")
    category = str(category_raw) if category_raw else None
    breakdown = st.session_state.get("score_breakdown")

    if score is None:
        st.info("Click **Calculate Pursuit Score** to analyse the opportunity.")
        return None, None

    if category is None:
        st.warning("Pursuit category is missing. Recalculate the score to continue.")
        return score, None

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
        st.success(
            "🟢 **Goldmine** — Strong opportunity with low barriers to entry. Proceed to Stage 2!"
        )
    elif category == CATEGORY_PROVEN:
        st.warning(
            "🟠 **Proven** — Validated market with moderate competition. Differentiation is key."
        )
    else:
        st.error(
            "🔴 **Saturated** — High competition. Consider pivoting to a sub-niche."
        )

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
                rows.append(
                    {
                        "Factor": factor,
                        "Sub-Score": f"{sub_score:.1f}",
                        "Weight": f"{weight:.0%}",
                        "Contribution": f"{contribution:.2f}",
                    }
                )

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
    inferred_product_category: str | None = None,
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
                product_category=inferred_product_category,
            )
            conn.commit()

        # Update pursuit score and category
        update_fields: dict[str, Any] = {
            "pursuit_score": score,
            "pursuit_category": category,
            "current_stage": 2,
        }
        existing_product_category = str(
            (selected_launch or {}).get("product_category") or ""
        ).strip()
        if inferred_product_category and not existing_product_category:
            update_fields["product_category"] = inferred_product_category

        mgr.update_launch(conn, launch_id, **update_fields)
        conn.commit()

        # Save review moat analysis per marketplace
        inputs = _compute_score_inputs(competitors)
        for marketplace in target_marketplaces:
            mkt_competitors = [
                c for c in competitors if c.get("marketplace") == marketplace
            ]
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
        _hydrate_from_saved_launch(selected_launch)
        try:
            with _open_conn() as conn:
                render_readiness_panel(
                    conn, int(selected_launch["launch_id"]), "Opportunity"
                )
        except Exception:
            pass
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

    cache_choice = "Use cache"
    if asin and target_marketplaces:
        try:
            cache_marketplaces = list(target_marketplaces)
            source_mkt_for_cache = str(
                (selected_launch or {}).get("source_marketplace") or "US"
            ).upper()
            if source_mkt_for_cache not in cache_marketplaces:
                cache_marketplaces.append(source_mkt_for_cache)

            with _open_conn() as conn:
                cache_status = _get_cache_status_for_asin(
                    conn,
                    asin,
                    cache_marketplaces,
                )
                if cache_status is None and selected_launch is not None:
                    cache_status = _get_cache_status_for_launch(
                        conn,
                        int(selected_launch["launch_id"]),
                    )
        except Exception:
            cache_status = None

        if cache_status:
            latest = cache_status.get("latest_fetched_at")
            if latest is not None and hasattr(latest, "strftime"):
                latest_str = latest.strftime("%d:%m:%y %H:%M UTC")
            elif latest is not None:
                latest_str = str(latest)
            else:
                latest_str = "unknown"
            st.info(
                f"We have cached Jungle Scout data for ASIN `{asin.upper()}` dated {latest_str}. "
                "Choose whether to use cache or refresh."
            )
            cache_choice = st.radio(
                "Market data source",
                options=["Use cache", "Re-run search"],
                horizontal=True,
                key=f"cache_choice_{asin.upper()}",
                help="Use cache avoids billable API calls when cached records exist.",
            )

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
            st.session_state["inferred_product_category"] = None
            use_cache = cache_choice != "Re-run search"

            with st.spinner(
                "Loading competitor data from cache..."
                if use_cache
                else "Refreshing competitor data from Jungle Scout..."
            ):
                try:
                    with _open_conn() as conn:
                        source_mkt = str(
                            (selected_launch or {}).get("source_marketplace") or "US"
                        )
                        source_context = str(
                            (selected_launch or {}).get("product_description") or ""
                        )
                        competitors = _fetch_competitors(
                            conn,
                            asin,
                            target_marketplaces,
                            launch_id,
                            source_marketplace=source_mkt,
                            source_context=source_context,
                            use_cache=use_cache,
                        )
                except Exception as exc:
                    st.error(f"❌ Connection error: {exc}")
                    competitors = None

            if competitors is not None:
                st.session_state["competitor_data"] = competitors
                st.session_state["pursuit_score"] = None
                st.session_state["pursuit_category"] = None
                st.session_state["score_breakdown"] = None

                if competitors:
                    st.success(
                        f"✅ Loaded {len(competitors)} competitors across {len(target_marketplaces)} marketplace(s)."
                    )
                else:
                    st.warning(
                        "⚠️ No competitors found. Try adjusting your filters or check the ASIN."
                    )

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
            st.session_state["inferred_product_category"] = None
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
            active_launch_id = int((selected_launch or {}).get("launch_id") or 0)
            if active_launch_id:
                render_section_save_status(active_launch_id, "opportunity", "analysis")

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
                            inferred_product_category=st.session_state.get(
                                "inferred_product_category"
                            ),
                        )
                except Exception as exc:
                    st.error(f"❌ Connection error while saving: {exc}")
                    launch_id = None

                if launch_id is not None:
                    record_section_save(int(launch_id), "opportunity", "analysis")
                    st.success(f"✅ Analysis saved! Launch ID: **#{launch_id}**")
                    st.session_state["selected_launch_id"] = launch_id

                    # Reload launches
                    _load_launches()

                    # Next stage button
                    if category in (CATEGORY_PROVEN, CATEGORY_GOLDMINE):
                        st.info(
                            "🚀 Ready to proceed! Navigate to **Module 2: Compliance Compass** in the sidebar."
                        )
                    else:
                        st.warning(
                            "⚠️ Score is Saturated. Consider pivoting before proceeding to Module 2."
                        )


if __name__ == "__main__":
    main()
