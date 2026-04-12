"""
Stage 4: Creative Studio

AI-powered listing generation and image gallery management.
Uses Google Gemini for text generation and Google Imagen 3 for image generation.
Manages 7-slot image gallery following Amazon best practices.
"""

from __future__ import annotations

import csv
import base64
import hashlib
import io
import json
import logging
import os
import re
import time
import zipfile
from datetime import datetime, timezone
from typing import Any

import psycopg
import streamlit as st
from dotenv import load_dotenv
from psycopg import errors
from psycopg.rows import dict_row

from services.auth_manager import get_generative_client, get_vertex_genai_client
from services.creative_gallery import (
    image_gallery_supports_binary,
    load_image_gallery,
    save_image_to_gallery,
)
from services.db_connection import connect, resolve_dsn
from services.imagen_quota import (
    call_with_quota_retry as quota_call_with_retry,
    is_quota_error as quota_is_quota_error,
    mark_imagen_request_attempt as quota_mark_request_attempt,
    seconds_until_next_image_request as quota_seconds_until_next_request,
)
from services.golden_three_client import build_keywords_string, fetch_golden_three
from services.launch_state import WORKFLOW_ASIN_IMPROVEMENT, LaunchStateManager
from services import sp_api_listings as _sp_api
from services.asin_snapshot import load_asin_snapshot
from services.listing_policy import (
    DEFAULT_EU_UK_RESTRICTED_MARKETING_PHRASES,
    DEFAULT_GLOBAL_PROHIBITED_LISTING_TERMS,
    effective_blocked_phrases,
    normalize_listing_with_policy,
    normalize_policy_rows,
    split_phrase_lines,
)
from services.workflow_ui import (
    record_section_save,
    render_readiness_panel,
    render_section_save_status,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TARGET_MARKETPLACES = ["UK", "DE", "FR", "IT", "ES"]

BRAND_VOICES = ["Professional", "Friendly", "Technical", "Luxury"]

# Full catalogue of available image slot types.
# Slot 1 is always 'main_white_bg' (Amazon requirement). Slots 2-7 are user-configurable.
IMAGE_TYPE_CATALOGUE: dict[str, dict[str, str]] = {
    "main_white_bg": {
        "name": "Main Image",
        "desc": "White background, product only",
        "icon": "🖼️",
    },
    "lifestyle": {
        "name": "Lifestyle",
        "desc": "Product in use / lifestyle context",
        "icon": "🌟",
    },
    "infographic": {
        "name": "Infographic",
        "desc": "Features & benefits callouts",
        "icon": "📊",
    },
    "comparison": {
        "name": "Comparison",
        "desc": "vs. competitors / before-after",
        "icon": "⚖️",
    },
    "dimensions": {
        "name": "Dimensions",
        "desc": "Size reference / measurements",
        "icon": "📐",
    },
    "packaging": {
        "name": "Packaging",
        "desc": "What's in the box",
        "icon": "📦",
    },
    "in_use": {
        "name": "In-Use",
        "desc": "Hands using the product",
        "icon": "🤲",
    },
    "before_after": {
        "name": "Before & After",
        "desc": "Results transformation",
        "icon": "✨",
    },
    "ingredients": {
        "name": "Key Ingredients",
        "desc": "Active ingredients / key actives callout",
        "icon": "🧪",
    },
    "how_to_use": {
        "name": "How to Use",
        "desc": "Step-by-step usage guide",
        "icon": "📋",
    },
    "sensory": {
        "name": "Texture & Sensory",
        "desc": "Close-up texture / sensory detail",
        "icon": "💧",
    },
    "certifications": {
        "name": "Claims & Certifications",
        "desc": "Vegan, organic, dermatologist-tested, etc.",
        "icon": "🏅",
    },
    "social_proof": {
        "name": "Social Proof",
        "desc": "Review quotes / customer results",
        "icon": "⭐",
    },
    "variant_range": {
        "name": "Variant Range",
        "desc": "Colour / scent / size range shot",
        "icon": "🎨",
    },
}

# Default slot-type assignments (used when no per-launch config exists).
# Slot 1 is locked to main_white_bg.
DEFAULT_SLOT_TYPES: dict[int, str] = {
    1: "main_white_bg",
    2: "lifestyle",
    3: "infographic",
    4: "comparison",
    5: "dimensions",
    6: "packaging",
    7: "in_use",
}

# Category-aware default overrides: map product category keywords to slot-type suggestions.
# Keys are lowercase substrings matched against the product category/description.
CATEGORY_SLOT_OVERRIDES: dict[str, dict[int, str]] = {
    # Beauty / personal care consumables — drop dimensions & packaging
    "shampoo": {5: "before_after", 6: "ingredients"},
    "conditioner": {5: "before_after", 6: "ingredients"},
    "serum": {5: "before_after", 6: "ingredients"},
    "moisturiser": {5: "sensory", 6: "ingredients"},
    "moisturizer": {5: "sensory", 6: "ingredients"},
    "cleanser": {5: "before_after", 6: "how_to_use"},
    "skincare": {5: "before_after", 6: "ingredients"},
    "haircare": {5: "before_after", 6: "ingredients"},
    "hair care": {5: "before_after", 6: "ingredients"},
    "supplement": {5: "certifications", 6: "ingredients"},
    "vitamin": {5: "certifications", 6: "ingredients"},
    "protein": {5: "certifications", 6: "ingredients"},
    "food": {5: "sensory", 6: "certifications"},
    "snack": {5: "sensory", 6: "certifications"},
    "coffee": {5: "sensory", 6: "how_to_use"},
    "tea": {5: "sensory", 6: "how_to_use"},
    "perfume": {5: "sensory", 6: "variant_range"},
    "fragrance": {5: "sensory", 6: "variant_range"},
    "candle": {5: "sensory", 6: "variant_range"},
}

# Build the legacy IMAGE_SLOTS dict dynamically from DEFAULT_SLOT_TYPES so that
# any code that still references IMAGE_SLOTS continues to work unchanged.
IMAGE_SLOTS: dict[int, dict[str, str]] = {
    slot_num: {"type": type_key, **IMAGE_TYPE_CATALOGUE[type_key]}
    for slot_num, type_key in DEFAULT_SLOT_TYPES.items()
}

APLUS_IMAGE_SPECS: dict[str, dict[str, Any]] = {
    "hero_banner": {
        "width": 970,
        "height": 600,
        "slot_type": "lifestyle",
        "desc": "Main hero image with product in-brand context",
    },
    "brand_story": {
        "width": 970,
        "height": 600,
        "slot_type": "in_use",
        "desc": "Brand story visual with human/product interaction",
    },
    "feature_1": {
        "width": 300,
        "height": 300,
        "slot_type": "infographic",
        "desc": "Feature tile image 1",
    },
    "feature_2": {
        "width": 300,
        "height": 300,
        "slot_type": "infographic",
        "desc": "Feature tile image 2",
    },
    "feature_3": {
        "width": 300,
        "height": 300,
        "slot_type": "infographic",
        "desc": "Feature tile image 3",
    },
    "comparison": {
        "width": 150,
        "height": 300,
        "slot_type": "comparison",
        "desc": "Comparison chart product image",
    },
}

AMAZON_LIMITS = {
    "title": 200,
    "bullet": 500,
    "description": 2000,
    "backend_keywords": 249,
}

# Load environment variables
load_dotenv()

GEMINI_MODEL = os.getenv("CREATIVE_GEMINI_MODEL", "gemini-2.5-flash")
IMAGEN_MODEL = os.getenv("CREATIVE_IMAGEN_MODEL", "imagen-3.0-generate-002")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
IMAGEN_RETRY_MAX_ATTEMPTS = max(0, int(os.getenv("CREATIVE_IMAGEN_MAX_RETRIES", "4")))
IMAGEN_RETRY_BASE_SECONDS = max(
    0.25, float(os.getenv("CREATIVE_IMAGEN_RETRY_BASE_SECONDS", "1.5"))
)
IMAGEN_RETRY_MAX_SECONDS = max(
    IMAGEN_RETRY_BASE_SECONDS,
    float(os.getenv("CREATIVE_IMAGEN_RETRY_MAX_SECONDS", "20")),
)
APLUS_IMAGE_CALL_SPACING_SECONDS = max(
    0.0, float(os.getenv("CREATIVE_APLUS_IMAGE_CALL_SPACING_SECONDS", "0.4"))
)
IMAGEN_QUOTA_COOLDOWN_SECONDS = max(
    10.0, float(os.getenv("CREATIVE_IMAGEN_QUOTA_COOLDOWN_SECONDS", "60"))
)
IMAGEN_STRICT_CALL_SPACING_SECONDS = max(
    0.0, float(os.getenv("CREATIVE_IMAGEN_STRICT_CALL_SPACING_SECONDS", "60"))
)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _get_dsn() -> str:
    return resolve_dsn("LAUNCHPAD_DB_DSN", "MARKET_INTEL_DSN", "PG_DSN")


def _open_conn() -> psycopg.Connection:
    return connect(_get_dsn())


# ---------------------------------------------------------------------------
# SKU registry helpers (local JSON persistence — no DB write access needed)
# ---------------------------------------------------------------------------
_SKU_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), ".streamlit", "sku_registry.json"
)


def _load_sku_registry() -> dict[str, list[str]]:
    """Load SKU registry from local JSON file.

    Registry format: {"ASIN/MP": ["SKU1", "SKU2", ...], ...}
    """
    try:
        with open(_SKU_REGISTRY_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def _save_sku_registry(registry: dict[str, list[str]]) -> None:
    """Persist SKU registry to local JSON file."""
    try:
        os.makedirs(os.path.dirname(_SKU_REGISTRY_PATH), exist_ok=True)
        with open(_SKU_REGISTRY_PATH, "w") as f:
            json.dump(registry, f, indent=2)
    except Exception as exc:
        logger.warning("Could not save SKU registry: %s", exc)


def _get_skus_for_asin_mp(asin: str, marketplace: str) -> list[str]:
    """Return sorted list of known SKUs for this ASIN/marketplace pair."""
    key = f"{asin.upper()}/{marketplace.upper()}"
    registry = _load_sku_registry()
    return registry.get(key, [])


def _register_sku(asin: str, marketplace: str, sku: str) -> None:
    """Add a SKU to the registry if it isn't already recorded."""
    if not sku.strip():
        return
    key = f"{asin.upper()}/{marketplace.upper()}"
    registry = _load_sku_registry()
    existing = registry.get(key, [])
    if sku not in existing:
        existing = [sku] + existing  # most recently used first
        registry[key] = existing[:20]  # cap at 20 entries per ASIN/MP
        _save_sku_registry(registry)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
def _init_session_state() -> None:
    defaults: dict[str, Any] = {
        "cs_launches": [],
        "cs_selected_launch_id": None,
        "cs_launch_data": None,
        # Listing generation
        "cs_product_name": "",
        "cs_key_features": "",
        "cs_target_keywords": "",
        "cs_brand_voice": "Professional",
        "cs_listing_extra_instruction": "",
        "cs_include_aplus": False,
        "cs_aplus_package": None,
        "cs_aplus_asset_cache": {},
        "cs_aplus_last_run_stats": None,
        "cs_generated_listing": None,
        "cs_edited_listing": None,
        # Image gallery: slot_number -> image data dict
        "cs_image_gallery": {},
        "cs_upload_fingerprints": {},
        # Versions
        "cs_draft_versions": [],
        "cs_compare_v1": None,
        "cs_compare_v2": None,
        # RUFUS
        "cs_rufus_optimize": False,
        # Active marketplace tab
        "cs_active_marketplace": "UK",
        "cs_prefill_launch_id": None,
        "cs_key_features_prefill_attempted_launch_id": None,
        "cs_hydrated_launch_id": None,
        "cs_active_launch_id": None,
        "cs_enforce_listing_policy": True,
        "cs_additional_blocked_terms": "",
        "cs_imagen_strict_spacing": True,
        # Golden Three keyword data fetched from market_intel (None = not yet fetched)
        "cs_golden_three": None,
        "cs_golden_three_launch_id": None,
        # PPC/JS keyword pool for BST seeding (list of str)
        "cs_bst_keyword_pool": [],
        # SP-API push state — marketplace-scoped keys are created dynamically
        # in _render_push_to_amazon; nothing to pre-init here.
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
def _render_header() -> None:
    st.title("🎨 Module 4: Creative Studio")
    st.markdown(
        "Generate AI-optimized listings and product images. "
        "Use **Google Gemini** for listing copy and **Imagen 3** for product visuals."
    )
    st.divider()


# ---------------------------------------------------------------------------
# Launch selector
# ---------------------------------------------------------------------------
def _load_launches(
    include_archived: bool = False, archived_only: bool = False
) -> list[dict[str, Any]]:
    try:
        with _open_conn() as conn:
            mgr = LaunchStateManager()
            launches = mgr.list_launches(
                conn,
                limit=100,
                include_archived=include_archived,
                archived_only=archived_only,
            )
            st.session_state["cs_launches"] = launches
            return launches
    except Exception as exc:
        st.error(f"❌ Failed to load launches: {exc}")
        return []


def _format_launch_selector_label(launch: dict[str, Any]) -> str:
    launch_name = str(launch.get("launch_name") or "").strip()
    base = launch_name or str(launch.get("source_asin") or "")
    archived_badge = " • Archived" if bool(launch.get("is_archived")) else ""
    return (
        f"#{launch['launch_id']} — {base} "
        f"(Stage {launch['current_stage']}, {launch.get('pursuit_category') or 'unscored'}{archived_badge})"
    )


def _render_launch_selector() -> dict[str, Any] | None:
    st.subheader("📋 Select Launch")

    filter_col, refresh_col = st.columns([4, 1])
    with filter_col:
        status_filter = st.selectbox(
            "Launch status",
            ["Active", "Archived", "All"],
            key="cs_launch_status_filter",
        )
    with refresh_col:
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()

    launches = _load_launches(
        include_archived=(status_filter != "Active"),
        archived_only=(status_filter == "Archived"),
    )

    col_select, col_manage = st.columns([4, 2])

    with col_select:
        if not launches:
            st.warning("No launches found for this filter.")
            return None

        options = {_format_launch_selector_label(l): l["launch_id"] for l in launches}
        option_labels = list(options.keys())
        selected_launch_id = st.session_state.get("cs_selected_launch_id")
        default_index = 0
        if selected_launch_id in options.values():
            selected_label = next(
                label for label, lid in options.items() if lid == selected_launch_id
            )
            default_index = option_labels.index(selected_label)
        choice = st.selectbox(
            "Select launch",
            option_labels,
            index=default_index,
            key="cs_launch_selector",
        )
        launch_id = options[choice]
        st.session_state["cs_selected_launch_id"] = launch_id
        selected_launch = next(
            (l for l in launches if l["launch_id"] == launch_id), None
        )

    with col_manage:
        if selected_launch is None:
            return None
        launch_id = int(selected_launch["launch_id"])
        current_name = str(selected_launch.get("launch_name") or "")
        launch_name_value = st.text_input(
            "Friendly name",
            value=current_name,
            placeholder="e.g. Q2 Kitchen Expansion",
            key=f"cs_launch_name_input_{launch_id}",
        )
        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            if st.button("💾 Save Name", key=f"cs_save_launch_name_{launch_id}"):
                try:
                    with _open_conn() as conn:
                        mgr = LaunchStateManager()
                        updated = mgr.update_launch(
                            conn,
                            launch_id,
                            launch_name=launch_name_value.strip() or None,
                        )
                        conn.commit()
                    if updated:
                        st.success("Launch name saved.")
                        st.rerun()
                    else:
                        st.warning("Launch name update did not apply.")
                except Exception as exc:
                    st.error(f"Failed to save launch name: {exc}")
        with btn_col2:
            is_archived = bool(selected_launch.get("is_archived"))
            archive_label = "♻️ Unarchive" if is_archived else "🗄️ Archive"
            if st.button(archive_label, key=f"cs_toggle_archive_{launch_id}"):
                try:
                    with _open_conn() as conn:
                        mgr = LaunchStateManager()
                        updated = mgr.update_launch(
                            conn,
                            launch_id,
                            is_archived=not is_archived,
                            archived_at=(
                                datetime.utcnow() if not is_archived else None
                            ),
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
                        st.warning("Archive update did not apply.")
                except Exception as exc:
                    st.error(f"Failed to update archive status: {exc}")

    return selected_launch


def _render_launch_info(launch: dict[str, Any]) -> None:
    """Show launch summary card with product and pricing info."""
    with st.expander("📊 Launch Overview", expanded=True):
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Launch ID", f"#{launch['launch_id']}")
        col2.metric("Source ASIN", launch["source_asin"])
        col3.metric("Stage", f"{launch['current_stage']} / 5")

        score = launch.get("pursuit_score")
        category = launch.get("pursuit_category") or "—"
        if score is not None:
            col4.metric("Pursuit Score", f"{score:.1f}", delta=category)
        else:
            col4.metric("Pursuit Score", "—")

        launch_name = str(launch.get("launch_name") or "").strip()
        if launch_name:
            st.caption(f"**Friendly Name:** {launch_name}")
        if bool(launch.get("is_archived")):
            st.caption("**Status:** Archived")

        if launch.get("product_description"):
            st.caption(f"**Description:** {launch['product_description']}")

        # Show pricing data if available
        launch_id = launch["launch_id"]
        try:
            with _open_conn() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT marketplace, recommended_launch_price, margin_estimate_pct
                        FROM launchpad.pricing_analysis
                        WHERE launch_id = %s
                        ORDER BY marketplace
                        """,
                        (launch_id,),
                    )
                    pricing_rows = cur.fetchall()

            if pricing_rows:
                st.markdown("**Pricing Summary:**")
                pcols = st.columns(len(pricing_rows))
                for i, row in enumerate(pricing_rows):
                    price = row.get("recommended_launch_price")
                    margin = row.get("margin_estimate_pct")
                    label = row["marketplace"]
                    price_str = f"£{price:.2f}" if price else "—"
                    margin_str = f"{margin:.1f}%" if margin else "—"
                    pcols[i].metric(label, price_str, delta=f"Margin: {margin_str}")

        except Exception as exc:
            logger.warning("Could not load pricing/PPC data: %s", exc)


def _strip_markdown_fences(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    return text.strip()


def _sanitise_json_text(text: str) -> str:
    """Replace unescaped control characters inside JSON string literals.

    Gemini occasionally emits literal newlines / tabs / carriage-returns
    inside JSON string values, which are invalid per RFC 8259 and cause
    json.loads to raise "Invalid control character".  We scan the text
    character-by-character, tracking whether we're inside a string, and
    replace bare control characters with their safe escape sequences.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    _CTRL_REPLACE = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for ch in text:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\" and in_string:
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string and ch in _CTRL_REPLACE:
            out.append(_CTRL_REPLACE[ch])
            continue
        out.append(ch)
    return "".join(out)


def _parse_json_object_from_text(raw_text: str) -> dict[str, Any]:
    text = _strip_markdown_fences(raw_text)

    def _try_parse(s: str) -> dict[str, Any] | None:
        for attempt in (s, _sanitise_json_text(s)):
            try:
                parsed = json.loads(attempt)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return None

    result = _try_parse(text)
    if result is not None:
        return result

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        result = _try_parse(text[start : end + 1])
        if result is not None:
            return result

    raise json.JSONDecodeError("Could not parse JSON object", text, 0)


_ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _normalize_keyword_candidate(value: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""

    if "/" in text:
        parts = text.split("/", 1)
        if len(parts) == 2 and len(parts[0]) <= 3 and parts[0].isalpha():
            text = parts[1].strip()

    text_l = text.lower()
    if text_l in {"keywords_by_asin_result", "keywords_by_asin"}:
        return ""
    if _ISO_TS_RE.match(text):
        return ""
    if len(text) == 10 and text.isalnum():
        return ""
    if len(text) > 100:
        return ""
    if not any(ch.isalpha() for ch in text):
        return ""
    return text


def _normalize_backend_keywords_to_csv(raw: str) -> str:
    """Convert a backend_keywords string to comma-separated format.

    Amazon backend_keywords are stored as a space-separated string
    (e.g. "insulated bottle stainless steel water bottle").  The Target
    Keywords field is labelled "comma-separated", so we split on existing
    commas first, then fall back to splitting on whitespace, and rejoin
    with ", ".
    """
    text = (raw or "").strip()
    if not text:
        return ""
    # If commas are already present, normalise spacing around them and return.
    if "," in text:
        parts = [p.strip() for p in text.split(",")]
        return ", ".join(p for p in parts if p)
    # Otherwise treat each whitespace-delimited token as a separate keyword.
    tokens = text.split()
    return ", ".join(tokens)


def _extract_keywords_from_js_payload(payload: Any, limit: int = 20) -> list[str]:
    extracted: list[str] = []
    seen: set[str] = set()

    def _push(candidate: Any) -> None:
        if len(extracted) >= limit:
            return
        if not isinstance(candidate, str):
            return
        keyword = _normalize_keyword_candidate(candidate)
        if not keyword:
            return
        key_l = keyword.lower()
        if key_l in seen:
            return
        seen.add(key_l)
        extracted.append(keyword)

    rows: list[Any] = []
    if isinstance(payload, dict):
        data_val = payload.get("data")
        if isinstance(data_val, list):
            rows.extend(data_val)
        for key in ["keywords", "search_terms", "results"]:
            maybe_list = payload.get(key)
            if isinstance(maybe_list, list):
                rows.extend(maybe_list)
        for key in ["keyword", "search_term", "query", "term", "name"]:
            _push(payload.get(key))
    elif isinstance(payload, list):
        rows.extend(payload)

    for row in rows:
        if len(extracted) >= limit:
            break
        if isinstance(row, dict):
            attrs = row.get("attributes")
            if isinstance(attrs, dict):
                _push(attrs.get("name"))
                _push(attrs.get("keyword"))
                _push(attrs.get("search_term"))
            _push(row.get("keyword"))
            _push(row.get("search_term"))
            _push(row.get("query"))
            _push(row.get("term"))
            _push(row.get("name"))
        elif isinstance(row, str):
            _push(row)

    return extracted


def _build_opportunity_snapshot(
    launch: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    launch_id = int(launch["launch_id"])
    snapshot: dict[str, Any] = {
        "launch": {
            "launch_id": launch_id,
            "source_asin": launch.get("source_asin"),
            "source_marketplace": launch.get("source_marketplace"),
            "target_marketplaces": launch.get("target_marketplaces") or [],
            "product_category": launch.get("product_category"),
            "product_description": launch.get("product_description"),
            "pursuit_score": launch.get("pursuit_score"),
            "pursuit_category": launch.get("pursuit_category"),
        }
    }

    keyword_rows: list[tuple[Any, ...]] = []
    pricing_rows: list[dict[str, Any]] = []
    risk_rows: list[dict[str, Any]] = []
    js_keyword_payloads: list[Any] = []

    with _open_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT keyword
                FROM launchpad.ppc_simulation
                WHERE launch_id = %s
                ORDER BY keyword
                LIMIT 20
                """,
                (launch_id,),
            )
            keyword_rows = cur.fetchall()

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT marketplace, recommended_launch_price, margin_estimate_pct,
                       competitor_count, analyzed_at
                FROM launchpad.pricing_analysis
                WHERE launch_id = %s
                ORDER BY analyzed_at DESC
                LIMIT 10
                """,
                (launch_id,),
            )
            pricing_rows = list(cur.fetchall())

            cur.execute(
                """
                SELECT risk_category, risk_description, severity, mitigation, assessed_at
                FROM launchpad.risk_assessment
                WHERE launch_id = %s
                ORDER BY assessed_at DESC
                LIMIT 10
                """,
                (launch_id,),
            )
            risk_rows = list(cur.fetchall())

            source_asin = str(launch.get("source_asin") or "").strip().upper()
            source_marketplace = (
                str(launch.get("source_marketplace") or "").strip().upper()
            )
            if source_asin:
                cur.execute(
                    """
                    SELECT response_data
                    FROM launchpad.jungle_scout_cache
                    WHERE asin = %s
                      AND (%s = '' OR marketplace = %s)
                      AND endpoint = 'keywords_by_asin'
                    ORDER BY fetched_at DESC
                    LIMIT 8
                    """,
                    (source_asin, source_marketplace, source_marketplace),
                )
                js_keyword_payloads = [r.get("response_data") for r in cur.fetchall()]

    # Collect keywords from ALL available sources and merge them.
    # Priority order: PPC simulation > Jungle Scout cache > niche_keyword_bank
    all_keywords: list[str] = []
    seen_keywords: set[str] = set()

    # Source 1: PPC simulation keywords
    for row in keyword_rows:
        if row and row[0]:
            kw = str(row[0]).strip()
            key_l = kw.lower()
            if key_l and key_l not in seen_keywords:
                seen_keywords.add(key_l)
                all_keywords.append(kw)

    # Source 2: Jungle Scout cache keywords_by_asin payloads
    if js_keyword_payloads:
        for payload in js_keyword_payloads:
            if len(all_keywords) >= 60:
                break
            for kw in _extract_keywords_from_js_payload(payload, limit=60):
                key_l = kw.lower()
                if key_l in seen_keywords:
                    continue
                seen_keywords.add(key_l)
                all_keywords.append(kw)
                if len(all_keywords) >= 60:
                    break

    # Source 3: market_intel.niche_keyword_bank by source_asin
    # Critical for improvement-workflow launches and for enriching BST pool
    if source_asin:
        try:
            with _open_conn() as conn:
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
                        (source_asin,),
                    )
                    niche_kw_rows = cur.fetchall()
            for r in niche_kw_rows:
                if r and r[0]:
                    kw = str(r[0]).strip()
                    key_l = kw.lower()
                    if key_l and key_l not in seen_keywords:
                        seen_keywords.add(key_l)
                        all_keywords.append(kw)
                        if len(all_keywords) >= 60:
                            break
        except Exception as exc:
            logger.debug("niche_keyword_bank fetch failed: %s", exc)

    keywords = all_keywords

    snapshot["ppc_keywords"] = keywords
    snapshot["pricing_summary"] = pricing_rows
    snapshot["risk_summary"] = risk_rows
    return snapshot, keywords


def _generate_key_features_from_snapshot(snapshot: dict[str, Any]) -> str:
    try:
        genai = get_generative_client()
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = f"""You are drafting Amazon listing key features for the Creative Studio form.

Use the provided opportunity report snapshot and return concise, factual feature bullets.

Rules:
- Return STRICT JSON only.
- Output shape: {{"key_features": ["...", "..."]}}
- Include 4 to 6 feature bullets.
- Keep each bullet under 110 characters.
- Focus on product value/benefit statements, not pricing or risk details.
- If data is sparse, infer reasonable product benefits from product description and keywords.

Snapshot:
{json.dumps(snapshot, default=str, ensure_ascii=True)}
"""
        response = model.generate_content(prompt)
        parsed = json.loads(
            _strip_markdown_fences(str(getattr(response, "text", "") or ""))
        )
        rows = parsed.get("key_features", []) if isinstance(parsed, dict) else []
        if not isinstance(rows, list):
            return ""

        features: list[str] = []
        seen: set[str] = set()
        for row in rows:
            text = " ".join(str(row or "").strip().split())
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            features.append(text)
            if len(features) >= 6:
                break

        return "\n".join(features)
    except Exception as exc:
        logger.info("Gemini key-feature prefill unavailable: %s", exc)
        return ""


def _generate_keywords_from_snapshot(snapshot: dict[str, Any]) -> str:
    try:
        genai = get_generative_client()
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = f"""You are drafting target keywords for an Amazon listing form.

Rules:
- Return STRICT JSON only.
- Output shape: {{"target_keywords": ["...", "..."]}}
- Include 10 to 20 concise search phrases.
- Avoid duplicates and very broad one-word terms.

Snapshot:
{json.dumps(snapshot, default=str, ensure_ascii=True)}
"""
        response = model.generate_content(prompt)
        parsed = json.loads(
            _strip_markdown_fences(str(getattr(response, "text", "") or ""))
        )
        rows = parsed.get("target_keywords", []) if isinstance(parsed, dict) else []
        if not isinstance(rows, list):
            return ""

        keywords: list[str] = []
        seen: set[str] = set()
        for row in rows:
            text = " ".join(str(row or "").strip().split())
            if len(text) < 3:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            keywords.append(text)
            if len(keywords) >= 20:
                break

        return ", ".join(keywords)
    except Exception as exc:
        logger.info("Gemini keyword prefill unavailable: %s", exc)
        return ""


def _prefill_listing_inputs(launch: dict[str, Any]) -> None:
    is_improvement = launch.get("workflow_type") == WORKFLOW_ASIN_IMPROVEMENT

    launch_id = int(launch["launch_id"])
    launch_changed = st.session_state.get("cs_prefill_launch_id") != launch_id

    if launch_changed and not is_improvement:
        st.session_state["cs_prefill_launch_id"] = launch_id
        st.session_state["cs_key_features"] = ""
        st.session_state["cs_key_features_input"] = ""
        st.session_state["cs_key_features_prefill_attempted_launch_id"] = None

    # Golden Three is already fetched and cached by _hydrate_saved_creative_state.
    # Keywords and title are already applied there. This function handles the
    # remaining gap: key_features (which hydrate does not touch) and falls back
    # to PPC/JS/niche keywords if golden three was absent.
    # NOTE: _build_opportunity_snapshot must run for ALL workflow types so that
    # Fallback 3 (market_intel.niche_keyword_bank) can populate cs_bst_keyword_pool
    # for improvement-workflow launches that have no PPC simulation or JS cache.

    try:
        snapshot, keywords = _build_opportunity_snapshot(launch)
    except Exception as exc:
        logger.warning(
            "Could not prefill listing inputs for launch %s: %s", launch_id, exc
        )
        return

    # Always store the raw keyword pool for BST seeding regardless of workflow type.
    if keywords:
        st.session_state["cs_bst_keyword_pool"] = keywords

    # The remaining prefill steps (target_keywords fallback, key_features generation)
    # are only relevant for new-launch workflow; improvement launches manage their
    # own inputs via the saved draft / hydration path.
    if is_improvement:
        return

    # Fall back to PPC/JS keywords only when neither golden three nor a saved
    # draft has already populated the field.
    if not st.session_state.get("cs_target_keywords"):
        keywords_text = ", ".join(keywords) if keywords else ""
        if not keywords_text:
            keywords_text = _generate_keywords_from_snapshot(snapshot)
        if keywords_text:
            st.session_state["cs_target_keywords"] = keywords_text
            st.session_state["cs_target_keywords_input"] = keywords_text

    if st.session_state.get("cs_key_features"):
        return

    attempted_launch_id = st.session_state.get(
        "cs_key_features_prefill_attempted_launch_id"
    )
    if attempted_launch_id == launch_id:
        return

    st.session_state["cs_key_features_prefill_attempted_launch_id"] = launch_id

    generated_features = _generate_key_features_from_snapshot(snapshot)
    if generated_features:
        st.session_state["cs_key_features"] = generated_features
        st.session_state["cs_key_features_input"] = generated_features


def _reset_launch_scoped_state(launch: dict[str, Any]) -> None:
    """Reset launch-scoped creative state when switching launches."""
    launch_id = int(launch["launch_id"])
    previous_launch_id = st.session_state.get("cs_active_launch_id")
    if previous_launch_id == launch_id:
        return

    st.session_state["cs_active_launch_id"] = launch_id
    st.session_state["cs_prefill_launch_id"] = None
    st.session_state["cs_hydrated_launch_id"] = None
    st.session_state["cs_key_features_prefill_attempted_launch_id"] = None

    st.session_state["cs_product_name"] = ""
    st.session_state["cs_key_features"] = ""
    st.session_state["cs_target_keywords"] = ""
    st.session_state["cs_generated_listing"] = None
    st.session_state["cs_edited_listing"] = None
    st.session_state["cs_include_aplus"] = False
    st.session_state["cs_aplus_package"] = None
    st.session_state["cs_aplus_asset_cache"] = {}
    st.session_state["cs_aplus_last_run_stats"] = None
    st.session_state["cs_rufus_optimize"] = False
    st.session_state["cs_active_marketplace"] = "UK"
    st.session_state["cs_image_gallery"] = {}
    st.session_state["cs_upload_fingerprints"] = {}
    st.session_state["cs_golden_three"] = None
    st.session_state["cs_golden_three_launch_id"] = None
    st.session_state["cs_bst_keyword_pool"] = []
    # Clear push state — marketplace-scoped keys are cleared by prefix
    push_keys = [
        k for k in st.session_state if k.startswith("cs_push_") or k == "cs_seller_id"
    ]
    for k in push_keys:
        del st.session_state[k]

    for key in (
        "cs_product_name_input",
        "cs_key_features_input",
        "cs_target_keywords_input",
    ):
        st.session_state.pop(key, None)

    _reset_listing_editor_state()


def _reset_listing_editor_state() -> None:
    """Clear listing editor widget values to force fresh render."""
    st.session_state.pop("cs_edit_title", None)
    st.session_state.pop("cs_edit_description", None)
    st.session_state.pop("cs_edit_backend_kw", None)
    for idx in range(5):
        st.session_state.pop(f"cs_edit_bullet_{idx}", None)


def _fetch_and_cache_golden_three(launch: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch Golden Three result from market_intel and cache in session state.

    Returns the cached row (or None) for the given launch.  Safe to call
    multiple times — returns the cached value after the first call.
    """
    launch_id = int(launch["launch_id"])
    if st.session_state.get("cs_golden_three_launch_id") == launch_id:
        return st.session_state.get("cs_golden_three")

    source_asin = str(launch.get("source_asin") or "").strip().upper()
    target_mps = launch.get("target_marketplaces") or []
    primary_mp = (
        str(target_mps[0])
        if target_mps
        else str(launch.get("source_marketplace") or "UK")
    )

    golden_three: dict[str, Any] | None = None
    if source_asin:
        golden_three = fetch_golden_three(source_asin, primary_mp)

    st.session_state["cs_golden_three"] = golden_three
    st.session_state["cs_golden_three_launch_id"] = launch_id
    return golden_three


def _hydrate_saved_creative_state(launch: dict[str, Any]) -> None:
    launch_id = int(launch["launch_id"])
    if st.session_state.get("cs_hydrated_launch_id") == launch_id:
        return

    st.session_state["cs_hydrated_launch_id"] = launch_id

    target_mps = launch.get("target_marketplaces") or TARGET_MARKETPLACES
    preferred_mp = str(target_mps[0]) if target_mps else "UK"

    # Fetch Golden Three up-front so we can fill any gaps below
    golden_three = _fetch_and_cache_golden_three(launch)

    try:
        with _open_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT marketplace, title, bullets, description, backend_keywords,
                           rufus_optimized, a_plus_content
                    FROM launchpad.listing_drafts
                    WHERE launch_id = %s
                    ORDER BY (a_plus_content IS NOT NULL) DESC,
                             (marketplace = %s) DESC,
                             generated_at DESC
                    LIMIT 1
                    """,
                    (launch_id, preferred_mp),
                )
                row = cur.fetchone()

        if not row:
            # No saved draft.
            # For improvement-workflow launches, seed from the ASIN snapshot
            # (current live listing data fetched at import time).
            _reset_listing_editor_state()
            snapshot_listing = None
            if launch.get("workflow_type") == WORKFLOW_ASIN_IMPROVEMENT:
                try:
                    with _open_conn() as snap_conn:
                        snapshot_listing = load_asin_snapshot(
                            snap_conn, launch_id, preferred_mp
                        )
                except Exception as exc:
                    logger.warning(
                        "Could not load asin_snapshot for launch %s: %s",
                        launch_id,
                        exc,
                    )

            if snapshot_listing:
                snap_bullets = snapshot_listing.get("bullets") or []
                if not isinstance(snap_bullets, list):
                    snap_bullets = []
                snap_bk = str(snapshot_listing.get("backend_keywords") or "").strip()
                snap_title = str(snapshot_listing.get("title") or "").strip()
                restored = {
                    "title": snap_title,
                    "bullets": snap_bullets,
                    "description": str(snapshot_listing.get("description") or ""),
                    "backend_keywords": snap_bk,
                    "quality_score": 75,
                    "quality_notes": ["Loaded from ASIN snapshot"],
                    "optimization_suggestions": [],
                }
                st.session_state["cs_generated_listing"] = restored
                st.session_state["cs_edited_listing"] = restored
                # Keywords: Golden Three takes priority; fall back to snapshot BST
                if golden_three:
                    kw_text = build_keywords_string(golden_three)
                else:
                    kw_text = _normalize_backend_keywords_to_csv(snap_bk)
                st.session_state["cs_target_keywords"] = kw_text
                st.session_state["cs_target_keywords_input"] = kw_text
                st.session_state["cs_product_name"] = snap_title[:200]
                st.session_state["cs_product_name_input"] = snap_title[:200]
                if snap_bullets:
                    bullets_text = "\n".join(
                        str(b).strip() for b in snap_bullets if str(b).strip()
                    )
                    st.session_state["cs_key_features"] = bullets_text
                    st.session_state["cs_key_features_input"] = bullets_text
                else:
                    st.session_state["cs_key_features"] = ""
                    st.session_state["cs_key_features_input"] = ""
            else:
                # No draft and no snapshot — seed from Golden Three if available
                st.session_state["cs_generated_listing"] = None
                st.session_state["cs_edited_listing"] = None
                if golden_three:
                    kw_text = build_keywords_string(golden_three)
                    title_draft = str(golden_three.get("title_draft") or "").strip()
                    st.session_state["cs_target_keywords"] = kw_text
                    st.session_state["cs_target_keywords_input"] = kw_text
                    st.session_state["cs_product_name"] = title_draft[:200]
                    st.session_state["cs_product_name_input"] = title_draft[:200]
                else:
                    st.session_state["cs_product_name"] = ""
                    st.session_state["cs_product_name_input"] = ""
                    st.session_state["cs_target_keywords"] = ""
                    st.session_state["cs_target_keywords_input"] = ""
                st.session_state["cs_key_features"] = ""
                st.session_state["cs_key_features_input"] = ""
            return

        bullets = row.get("bullets", [])
        if isinstance(bullets, str):
            try:
                bullets = json.loads(bullets)
            except Exception:
                bullets = []
        if not isinstance(bullets, list):
            bullets = []

        aplus = row.get("a_plus_content")
        if isinstance(aplus, str):
            try:
                aplus = json.loads(aplus) if aplus else None
            except Exception:
                aplus = None

        restored_listing = {
            "title": row.get("title", ""),
            "bullets": bullets,
            "description": row.get("description", ""),
            "backend_keywords": row.get("backend_keywords", ""),
            "a_plus_content": aplus,
            "quality_score": 75,
            "quality_notes": ["Loaded from saved draft"],
            "optimization_suggestions": [],
        }

        _reset_listing_editor_state()
        st.session_state["cs_generated_listing"] = restored_listing
        st.session_state["cs_edited_listing"] = restored_listing
        st.session_state["cs_active_marketplace"] = str(
            row.get("marketplace") or preferred_mp
        )
        st.session_state["cs_rufus_optimize"] = bool(row.get("rufus_optimized"))
        st.session_state["cs_include_aplus"] = bool(aplus)
        _hydrate_aplus_package_from_listing_content(launch_id, aplus)

        # Keywords: prefer Golden Three; fall back to saved backend_keywords
        if golden_three:
            kw_text = build_keywords_string(golden_three)
        else:
            kw_text = _normalize_backend_keywords_to_csv(
                str(row.get("backend_keywords") or "")
            )
        st.session_state["cs_target_keywords"] = kw_text
        st.session_state["cs_target_keywords_input"] = kw_text

        if bullets:
            bullets_text = "\n".join(str(b).strip() for b in bullets if str(b).strip())
            st.session_state["cs_key_features"] = bullets_text
            st.session_state["cs_key_features_input"] = bullets_text
        else:
            st.session_state["cs_key_features"] = ""
            st.session_state["cs_key_features_input"] = ""

        # Product name: prefer Golden Three title_draft; fall back to saved draft title
        if golden_three:
            title_draft = str(golden_three.get("title_draft") or "").strip()
            product_name = (title_draft or str(row.get("title") or ""))[:200]
        else:
            product_name = str(row.get("title") or "")[:100]
        st.session_state["cs_product_name"] = product_name
        st.session_state["cs_product_name_input"] = product_name
    except Exception as exc:
        logger.warning(
            "Could not hydrate creative state for launch %s: %s", launch_id, exc
        )


# ---------------------------------------------------------------------------
# Stage 3 validation
# ---------------------------------------------------------------------------
def _show_stage_readiness_notice(launch: dict[str, Any]) -> None:
    """Show non-blocking readiness notice for Stage 4 inputs."""
    stage = int(launch.get("current_stage", 1))
    if stage < 3:
        st.warning(
            "⚠️ Module 3 pricing is not marked complete for this launch yet. "
            "You can still draft listings and creative assets now; save and finalize when ready."
        )


# ---------------------------------------------------------------------------
# Listing generation
# ---------------------------------------------------------------------------
def _render_listing_inputs(launch: dict[str, Any]) -> None:
    st.subheader("✍️ Listing Generation")
    render_section_save_status(int(launch["launch_id"]), "creative", "listing_draft")

    # Show banner when Golden Three data was auto-loaded from market_intel
    golden_three: dict[str, Any] | None = st.session_state.get("cs_golden_three")
    if golden_three:
        anchor = str(golden_three.get("anchor_keyword") or "").strip()
        scaler = str(golden_three.get("scaler_keyword") or "").strip()
        specialist = str(golden_three.get("specialist_keyword") or "").strip()
        engine = str(golden_three.get("engine_mode") or "").strip()
        warning = str(golden_three.get("warning") or "").strip()
        kw_parts = " · ".join(k for k in [anchor, scaler, specialist] if k)
        banner_lines = [
            f"**Golden Three keywords loaded** from `market_intel` ({engine} mode): "
            f"**{kw_parts}**",
            "Title draft and keywords have been pre-populated below. Edit freely.",
        ]
        if warning:
            banner_lines.append(f"Engine warning: {warning}")
        st.info("\n\n".join(banner_lines))

    launch_description = launch.get("product_description") or ""

    if "cs_product_name_input" not in st.session_state:
        st.session_state["cs_product_name_input"] = (
            st.session_state.get("cs_product_name") or launch_description[:100]
        )
    if "cs_key_features_input" not in st.session_state:
        st.session_state["cs_key_features_input"] = st.session_state.get(
            "cs_key_features", ""
        )
    if "cs_target_keywords_input" not in st.session_state:
        st.session_state["cs_target_keywords_input"] = st.session_state.get(
            "cs_target_keywords", ""
        )
    if "cs_listing_extra_instruction_input" not in st.session_state:
        st.session_state["cs_listing_extra_instruction_input"] = st.session_state.get(
            "cs_listing_extra_instruction", ""
        )

    col1, col2 = st.columns(2)

    with col1:
        product_name = st.text_input(
            "Product Name / Title",
            placeholder="e.g. Premium Stainless Steel Water Bottle 32oz",
            key="cs_product_name_input",
            help="Auto-populated from Module 1 product description.",
        )
        st.session_state["cs_product_name"] = product_name

        key_features = st.text_area(
            "Key Features (one per line)",
            placeholder="BPA-free stainless steel\nDouble-wall vacuum insulation\nLeakproof lid\n24-hour cold / 12-hour hot",
            height=120,
            key="cs_key_features_input",
            help="Bullet points from your product analysis.",
        )
        st.session_state["cs_key_features"] = key_features

        extra_instruction = st.text_area(
            "Additional Prompt Instruction (optional)",
            placeholder=(
                "e.g. Use the phrase 'clinically tested' at least once, "
                "or maintain a concise technical tone."
            ),
            height=90,
            key="cs_listing_extra_instruction_input",
            help="Appended to the hidden Gemini prompt for this listing generation run.",
        )
        st.session_state["cs_listing_extra_instruction"] = extra_instruction

    with col2:
        target_keywords = st.text_area(
            "Target Keywords (comma-separated)",
            placeholder="water bottle, insulated bottle, stainless steel bottle",
            height=80,
            key="cs_target_keywords_input",
            help="From Stage 3 PPC analysis — auto-populated if available.",
        )
        st.session_state["cs_target_keywords"] = target_keywords

        brand_voice = st.selectbox(
            "Brand Voice",
            BRAND_VOICES,
            index=BRAND_VOICES.index(
                st.session_state.get("cs_brand_voice", "Professional")
            ),
            key="cs_brand_voice_input",
        )
        st.session_state["cs_brand_voice"] = brand_voice

        st.caption("A+ Content is generated in the section below Image Gallery.")

        rufus_optimize = st.checkbox(
            "🤖 Optimize for Amazon RUFUS AI",
            value=st.session_state.get("cs_rufus_optimize", False),
            key="cs_rufus_input",
            help="Adds natural language patterns, conversational Q&A format, and semantic keyword optimization for Amazon's AI shopping assistant.",
        )
        st.session_state["cs_rufus_optimize"] = rufus_optimize

        enforce_policy = st.checkbox(
            "🛡️ Enforce listing compliance guardrails",
            value=st.session_state.get("cs_enforce_listing_policy", True),
            key="cs_enforce_listing_policy_input",
            help="Applies hard length limits and removes risky prohibited terms before save.",
        )
        st.session_state["cs_enforce_listing_policy"] = enforce_policy

        blocked_terms = st.text_area(
            "Additional blocked terms / phrases (one per line)",
            value=st.session_state.get("cs_additional_blocked_terms", ""),
            placeholder="Brand names, legal claims, competitor names...",
            height=80,
            key="cs_additional_blocked_terms_input",
            help="Use this to block product-specific risky terms in generated listing text.",
        )
        st.session_state["cs_additional_blocked_terms"] = blocked_terms

        with st.expander("Policy phrase lists used by guardrails", expanded=False):
            policy_terms = _load_listing_policy_terms()
            source_label = (
                "database"
                if policy_terms.get("source") == "db"
                else "built-in defaults"
            )
            st.caption(f"Current source: {source_label}")
            if st.button("🔄 Refresh policy terms", key="cs_refresh_policy_terms"):
                _load_listing_policy_terms.clear()
                st.rerun()

            st.caption(
                "EU/UK restricted marketing claims (auto-applied for UK/DE/FR/IT/ES):"
            )
            st.code("\n".join(policy_terms.get("eu_uk", [])), language="text")
            st.caption("Global prohibited terms (all marketplaces):")
            st.code("\n".join(policy_terms.get("global", [])), language="text")

            st.caption(
                "Policy editor (writes to DB table launchpad.listing_policy_terms)"
            )
            global_terms_text = st.text_area(
                "Global prohibited terms",
                value="\n".join(policy_terms.get("global", [])),
                height=120,
                key="cs_policy_global_editor",
            )
            eu_terms_text = st.text_area(
                "EU/UK restricted terms",
                value="\n".join(policy_terms.get("eu_uk", [])),
                height=120,
                key="cs_policy_euuk_editor",
            )
            if st.button("💾 Save policy terms to DB", key="cs_save_policy_terms_db"):
                if _save_listing_policy_terms_to_db(global_terms_text, eu_terms_text):
                    _load_listing_policy_terms.clear()
                    st.success("Policy terms saved to DB.")
                    st.rerun()


@st.cache_data(show_spinner=False, ttl=300)
def _load_listing_policy_terms() -> dict[str, Any]:
    fallback = {
        "global": list(DEFAULT_GLOBAL_PROHIBITED_LISTING_TERMS),
        "eu_uk": list(DEFAULT_EU_UK_RESTRICTED_MARKETING_PHRASES),
        "source": "defaults",
    }
    try:
        with _open_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT scope, term
                    FROM launchpad.listing_policy_terms
                    WHERE is_active = TRUE
                    ORDER BY scope, term
                    """
                )
                rows = cur.fetchall()
        if not rows:
            return fallback
        scoped = normalize_policy_rows([dict(row) for row in rows])
        if not scoped["global"] and not scoped["eu_uk"]:
            return fallback
        return {"global": scoped["global"], "eu_uk": scoped["eu_uk"], "source": "db"}
    except errors.UndefinedTable:
        return fallback
    except Exception as exc:
        logger.warning("Could not load listing policy terms from DB: %s", exc)
        return fallback


def _save_listing_policy_terms_to_db(global_terms: str, eu_uk_terms: str) -> bool:
    try:
        desired = {
            "global": split_phrase_lines(global_terms),
            "eu_uk": split_phrase_lines(eu_uk_terms),
        }
        with _open_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT scope, term
                    FROM launchpad.listing_policy_terms
                    WHERE scope IN ('global', 'eu_uk')
                    """
                )
                existing_rows = cur.fetchall()

                existing_map: dict[str, set[str]] = {"global": set(), "eu_uk": set()}
                for row in existing_rows:
                    scope = str(row.get("scope") or "").strip().lower()
                    term = " ".join(str(row.get("term") or "").strip().split())
                    if scope in existing_map and term:
                        existing_map[scope].add(term.lower())

                for scope, wanted_terms in desired.items():
                    wanted_norm = {term.lower() for term in wanted_terms}

                    for term in wanted_terms:
                        cur.execute(
                            """
                            INSERT INTO launchpad.listing_policy_terms (scope, term, notes, is_active)
                            VALUES (%s, %s, %s, TRUE)
                            ON CONFLICT (scope, term_normalized)
                            DO UPDATE SET is_active = TRUE, term = EXCLUDED.term, updated_at = now()
                            """,
                            (scope, term, "Managed from Creative Studio UI"),
                        )

                    to_deactivate = existing_map.get(scope, set()) - wanted_norm
                    for lower_term in to_deactivate:
                        cur.execute(
                            """
                            UPDATE launchpad.listing_policy_terms
                            SET is_active = FALSE, updated_at = now()
                            WHERE scope = %s
                              AND term_normalized = %s
                              AND is_active = TRUE
                            """,
                            (scope, lower_term),
                        )

            conn.commit()
        return True
    except errors.UndefinedTable:
        st.error(
            "❌ Policy term table not found. Run migration 013 first: "
            "migrations/013_listing_policy_terms.sql"
        )
        return False
    except Exception as exc:
        st.error(f"❌ Failed to save policy terms: {exc}")
        logger.error("Failed to save listing policy terms: %s", exc)
        return False


def _current_policy_terms() -> dict[str, Any]:
    return _load_listing_policy_terms()


def _effective_blocked_phrases(marketplace: str) -> list[str]:
    return effective_blocked_phrases(
        marketplace=marketplace,
        policy_terms=_current_policy_terms(),
        additional_terms_raw=st.session_state.get("cs_additional_blocked_terms", ""),
    )


def _normalize_listing_with_policy(
    listing: dict[str, Any], marketplace: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    return normalize_listing_with_policy(
        listing=listing,
        marketplace=marketplace,
        amazon_limits=AMAZON_LIMITS,
        enforce_policy=st.session_state.get("cs_enforce_listing_policy", True),
        blocked_phrases=_effective_blocked_phrases(marketplace),
    )


def _build_listing_prompt(
    product_name: str,
    key_features: str,
    target_keywords: str,
    brand_voice: str,
    rufus_optimize: bool,
    marketplace: str = "UK",
    blocked_phrases: list[str] | None = None,
    extra_instruction: str = "",
    bst_keyword_pool: list[str] | None = None,
) -> str:
    voice_desc = {
        "Professional": "authoritative, clear, and business-like",
        "Friendly": "warm, approachable, and conversational",
        "Technical": "precise, specification-focused, and detail-oriented",
        "Luxury": "premium, aspirational, and sophisticated",
    }.get(brand_voice, "professional")

    rufus_note = ""
    if rufus_optimize:
        rufus_note = """
RUFUS AI OPTIMIZATION:
- Include natural language patterns that answer common shopper questions
- Add conversational phrases like "perfect for...", "ideal when...", "great choice if..."
- Use semantic variations of keywords (not just exact match)
- Structure content to answer: What is it? Who is it for? Why buy it?
"""

    blocked_lines = ""
    if blocked_phrases:
        blocked_lines = "\n".join(f"- {p}" for p in blocked_phrases[:80])

    extra_instruction_block = ""
    if extra_instruction.strip():
        extra_instruction_block = (
            "\nADDITIONAL USER INSTRUCTION (highest priority unless it conflicts with policy/safety rules):\n"
            f"{extra_instruction.strip()}\n"
        )

    # BST keyword pool — PPC/JS long-tail keywords available for the BST field
    bst_pool_block = ""
    if bst_keyword_pool:
        pool_str = " ".join(bst_keyword_pool[:60])  # cap to avoid prompt bloat
        bst_pool_block = f"\nBST KEYWORD POOL (long-tail candidates for backend_keywords):\n{pool_str}\n"

    return f"""You are an expert Amazon listing copywriter specializing in {marketplace} marketplace.
Write a complete, optimized Amazon product listing in a {voice_desc} brand voice.

PRODUCT: {product_name}
KEY FEATURES:
{key_features}

TARGET KEYWORDS: {target_keywords}
{rufus_note}
{bst_pool_block}
{extra_instruction_block}

OUTPUT FORMAT (respond with valid JSON only, no markdown):
{{
  "title": "Optimized product title (max 200 characters, include primary keyword near start)",
  "bullets": [
    "Bullet 1 (max 500 chars, start with ALL CAPS benefit, include keyword)",
    "Bullet 2 (max 500 chars)",
    "Bullet 3 (max 500 chars)",
    "Bullet 4 (max 500 chars)",
    "Bullet 5 (max 500 chars)"
  ],
  "description": "HTML-formatted product description (max 2000 chars, use <b> and <br> tags)",
  "backend_keywords": "Space-separated backend search terms — see BST RULES below",
  "quality_score": 85,
  "quality_notes": ["Note 1", "Note 2"],
  "optimization_suggestions": ["Suggestion 1", "Suggestion 2"]
}}

RULES:
- Title: max 200 characters, primary keyword in first 80 chars
- Each bullet: max 500 characters, start with capitalized benefit phrase
- Description: HTML formatted, max 2000 characters
- quality_score: integer 0-100 based on keyword density, readability, compliance
- Do NOT include markdown code blocks in response, return raw JSON only
- Prohibited words/phrases that must NOT appear in title, bullets, description, or backend keywords:
{blocked_lines}

BST RULES (backend_keywords field — the SEO engine room):
- Separator: single spaces only. NO commas, semicolons, or quotes. Commas waste byte budget.
- Hard limit: 249 bytes (UTF-8). Stay at or under 249 bytes total.
- NEVER repeat any word already in the title or bullet points. Amazon indexes the whole listing as one bag of words — repetition wastes space with zero benefit.
- NEVER use plurals or word stems separately — Amazon's engine maps "balm" to "balms" automatically. Use the singular root form only.
- NEVER include brand names (yours or competitors'). Brand name targeting violates Amazon ToS.
- Fill the BST with content that did NOT make it into the title or bullets:
  1. High-volume synonyms (e.g. if title says "bucket", BST should contain "pail" "container" "vessel")
  2. Use cases and personas (e.g. "gym garage beginners rehabilitation physical therapy")
  3. Common misspellings of high-volume terms (exact-match misspellings still get a relevancy edge)
  4. Multi-language translations for the top 3-5 concepts (Spanish is high-traffic; German/French for EU)
  5. Long-tail attributes: materials, sizes, technical specs not in the bullets
- If a BST KEYWORD POOL was provided above, draw from it — prioritise terms with highest search volume that are absent from the title and bullets.
- Target as close to 249 bytes as possible without exceeding it. An empty or short BST is a wasted opportunity.
"""


def _generate_listing(
    product_name: str,
    key_features: str,
    target_keywords: str,
    brand_voice: str,
    rufus_optimize: bool,
    marketplace: str = "UK",
    extra_instruction: str = "",
    bst_keyword_pool: list[str] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Generate listing and return (normalized_listing, policy_report)."""
    try:
        genai = get_generative_client()
        model = genai.GenerativeModel(GEMINI_MODEL)

        prompt = _build_listing_prompt(
            product_name,
            key_features,
            target_keywords,
            brand_voice,
            rufus_optimize,
            marketplace,
            _effective_blocked_phrases(marketplace),
            extra_instruction,
            bst_keyword_pool,
        )

        response = model.generate_content(prompt)
        parsed = _parse_json_object_from_text(str(getattr(response, "text", "") or ""))
        if not isinstance(parsed, dict):
            st.error("❌ AI response did not match expected listing object.")
            return None, None
        raw_bk = str(parsed.get("backend_keywords") or "").strip()
        logger.info(
            "Gemini raw backend_keywords (%d chars): %r",
            len(raw_bk),
            raw_bk[:120],
        )
        normalized, report = _normalize_listing_with_policy(parsed, marketplace)
        post_bk = str(normalized.get("backend_keywords") or "").strip()
        logger.info(
            "Post-normalization backend_keywords (%d chars / %d bytes): %r",
            len(post_bk),
            len(post_bk.encode("utf-8")),
            post_bk[:120],
        )
        return normalized, report

    except json.JSONDecodeError as exc:
        st.error(f"❌ AI returned invalid JSON: {exc}")
        logger.error("JSON parse error from Gemini: %s", exc)
        return None, None
    except FileNotFoundError as exc:
        st.error(f"❌ Google credentials not found: {exc}")
        return None, None
    except Exception as exc:
        st.error(f"❌ Listing generation failed: {exc}")
        logger.error("Listing generation error: %s", exc)
        return None, None


def _compute_listing_constraint_score(listing: dict[str, Any]) -> tuple[int, list[str]]:
    title = str(listing.get("title") or "")
    bullets = listing.get("bullets") or []
    if not isinstance(bullets, list):
        bullets = []
    bullets_clean = [str(b or "").strip() for b in bullets if str(b or "").strip()]
    description = str(listing.get("description") or "")
    backend_keywords = str(listing.get("backend_keywords") or "")

    target_keywords = [
        k.strip().lower()
        for k in re.split(
            r"[,\n]", str(st.session_state.get("cs_target_keywords") or "")
        )
        if k.strip()
    ]
    primary_kw = target_keywords[0] if target_keywords else ""

    checks = [
        (
            len(title) <= AMAZON_LIMITS["title"] and bool(title.strip()),
            "Title present and within 200 characters",
        ),
        (
            (not primary_kw) or (primary_kw in title[:80].lower()),
            "Primary keyword appears in first 80 title characters",
        ),
        (
            len(bullets_clean) >= 5,
            "Five non-empty bullet points",
        ),
        (
            all(len(b) <= AMAZON_LIMITS["bullet"] for b in bullets_clean),
            "All bullets within 500 characters",
        ),
        (
            len(description) <= AMAZON_LIMITS["description"]
            and len(description.strip()) >= 200,
            "Description within 2000 characters and sufficiently detailed",
        ),
        (
            len(backend_keywords.encode("utf-8")) <= AMAZON_LIMITS["backend_keywords"]
            and bool(backend_keywords.strip()),
            "Backend keywords within 249 bytes and non-empty",
        ),
    ]

    passed = [label for ok, label in checks if ok]
    failed = [label for ok, label in checks if not ok]
    score = int(round((len(passed) / max(1, len(checks))) * 100))
    return score, failed


def _render_listing_display(listing: dict[str, Any]) -> dict[str, Any]:
    """Render editable listing fields with character counters. Returns edited listing."""
    st.markdown("### 📝 Generated Listing")

    edited = dict(listing)

    policy_report = st.session_state.get("cs_listing_policy_report")
    if isinstance(policy_report, dict):
        truncated = policy_report.get("truncated_fields") or {}
        removed = policy_report.get("removed_phrases") or []
        if truncated:
            details = ", ".join(
                f"{k} (-{v})"
                for k, v in sorted(truncated.items(), key=lambda item: item[0])
            )
            st.warning(f"Length guardrails applied: {details}")
        if removed:
            st.warning(
                "Removed prohibited phrases: " + ", ".join(str(p) for p in removed[:20])
            )

    # Title
    title_val = st.text_area(
        "Title",
        value=listing.get("title", ""),
        height=80,
        key="cs_edit_title",
    )
    title_len = len(title_val or "")
    title_color = "🔴" if title_len > AMAZON_LIMITS["title"] else "🟢"
    st.caption(f"{title_color} {title_len} / {AMAZON_LIMITS['title']} characters")
    edited["title"] = title_val

    # Bullets
    st.markdown("**Bullet Points:**")
    bullets = listing.get("bullets", [""] * 5)
    edited_bullets = []
    for i, bullet in enumerate(bullets[:5]):
        b_val = st.text_area(
            f"Bullet {i + 1}",
            value=bullet,
            height=80,
            key=f"cs_edit_bullet_{i}",
        )
        b_len = len(b_val or "")
        b_color = "🔴" if b_len > AMAZON_LIMITS["bullet"] else "🟢"
        st.caption(f"{b_color} {b_len} / {AMAZON_LIMITS['bullet']} characters")
        edited_bullets.append(b_val)
    edited["bullets"] = edited_bullets

    # Description
    desc_val = st.text_area(
        "Product Description (HTML)",
        value=listing.get("description", ""),
        height=150,
        key="cs_edit_description",
    )
    desc_len = len(desc_val or "")
    desc_color = "🔴" if desc_len > AMAZON_LIMITS["description"] else "🟢"
    st.caption(f"{desc_color} {desc_len} / {AMAZON_LIMITS['description']} characters")
    edited["description"] = desc_val

    # Backend keywords
    # Debug: Show what value is being passed to the text area
    bk_from_listing = listing.get("backend_keywords", "")
    st.caption(
        f"Debug: backend_keywords in listing = {len(bk_from_listing.encode('utf-8'))} bytes"
    )
    bk_val = st.text_area(
        "Backend Keywords",
        value=bk_from_listing,
        height=80,
        key="cs_edit_backend_kw",
        help="Space-separated, max 249 bytes. No commas, no brand names, no repetition of words already in title or bullets.",
    )
    bk_bytes = len((bk_val or "").encode("utf-8"))
    bk_color = "🔴" if bk_bytes > AMAZON_LIMITS["backend_keywords"] else "🟢"
    st.caption(f"{bk_color} {bk_bytes} / {AMAZON_LIMITS['backend_keywords']} bytes")
    edited["backend_keywords"] = bk_val

    # Quality score
    quality_score = listing.get("quality_score", 0)
    st.markdown("**Listing Quality Score:**")
    col_q1, col_q2 = st.columns([1, 3])
    with col_q1:
        q_color = (
            "#27ae60"
            if quality_score >= 80
            else "#f39c12"
            if quality_score >= 60
            else "#e74c3c"
        )
        st.markdown(
            f"<div style='text-align:center; padding:12px; border-radius:8px; "
            f"background:{q_color}; color:white; font-size:1.5em; font-weight:bold;'>"
            f"{quality_score}</div>",
            unsafe_allow_html=True,
        )
    with col_q2:
        st.progress(quality_score / 100.0)
        notes = listing.get("quality_notes", [])
        for note in notes:
            st.caption(f"• {note}")

    constraint_score, improvement_items = _compute_listing_constraint_score(edited)
    with st.expander("ℹ️ How this score works", expanded=False):
        st.markdown(
            "- `quality_score` is generated by Gemini as a model self-assessment, so it is advisory rather than deterministic."
        )
        st.markdown(
            f"- Constraint score: **{constraint_score}/100** based on enforceable checks (length, structure, keyword placement)."
        )
        if improvement_items:
            st.markdown("- To move toward **100/100**, address these items:")
            for item in improvement_items:
                st.markdown(f"  - {item}")
        else:
            st.markdown(
                "- All enforceable checks pass. To reach 100/100 model score, improve clarity, keyword naturalness, and conversion strength, then regenerate."
            )

    # Optimization suggestions
    suggestions = listing.get("optimization_suggestions", [])
    if suggestions:
        with st.expander("💡 Optimization Suggestions", expanded=False):
            for s in suggestions:
                st.markdown(f"• {s}")

    # A+ Content preview
    aplus = listing.get("a_plus_content")
    if aplus:
        with st.expander("✨ A+ Content Modules", expanded=False):
            st.json(aplus)

    # Preview mode
    with st.expander("👁️ Preview (as Amazon listing)", expanded=False):
        st.markdown(f"### {edited.get('title', '')}")
        for b in edited.get("bullets", []):
            if b:
                st.markdown(f"• {b}")
        st.markdown("---")
        st.markdown(edited.get("description", ""), unsafe_allow_html=True)

    return edited


def _save_listing_draft(
    launch_id: int,
    marketplace: str,
    listing: dict[str, Any],
    rufus_optimized: bool,
) -> bool:
    """Save listing draft to DB, auto-incrementing version. Returns True on success."""
    try:
        listing_to_save = dict(listing)
        if st.session_state.get("cs_enforce_listing_policy", True):
            listing_to_save, report = _normalize_listing_with_policy(
                listing_to_save, marketplace
            )
            st.session_state["cs_listing_policy_report"] = report

        with _open_conn() as conn:
            # Get next version number
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM launchpad.listing_drafts
                    WHERE launch_id = %s AND marketplace = %s
                    """,
                    (launch_id, marketplace),
                )
                next_version_row = cur.fetchone()
                next_version = next_version_row[0] if next_version_row else 1

            aplus = listing.get("a_plus_content")

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO launchpad.listing_drafts
                        (launch_id, marketplace, version, title, bullets, description,
                         backend_keywords, rufus_optimized, a_plus_content, generated_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        launch_id,
                        marketplace,
                        next_version,
                        listing_to_save.get("title", ""),
                        json.dumps(listing_to_save.get("bullets", [])),
                        listing_to_save.get("description", ""),
                        listing_to_save.get("backend_keywords", ""),
                        rufus_optimized,
                        json.dumps(aplus) if aplus else None,
                        GEMINI_MODEL,
                    ),
                )
            conn.commit()
            return True
    except Exception as exc:
        st.error(f"❌ Failed to save draft: {exc}")
        logger.error("Draft save error: %s", exc)
        return False


def _load_draft_versions(launch_id: int, marketplace: str) -> list[dict[str, Any]]:
    """Load all saved draft versions for a launch/marketplace."""
    try:
        with _open_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT draft_id, version, title, bullets, description,
                           backend_keywords, rufus_optimized, a_plus_content,
                           generated_by, generated_at
                    FROM launchpad.listing_drafts
                    WHERE launch_id = %s AND marketplace = %s
                    ORDER BY version DESC
                    """,
                    (launch_id, marketplace),
                )
                return list(cur.fetchall())
    except Exception as exc:
        logger.warning("Could not load draft versions: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Image gallery
# ---------------------------------------------------------------------------
def _build_image_prompt(
    slot: dict[str, str],
    product_name: str,
    product_description: str,
) -> str:
    slot_prompts = {
        "main_white_bg": (
            f"Professional product photography of {product_name}. "
            "Pure white background, studio lighting, centered composition, "
            "no shadows, no text, no props. High resolution, commercial quality."
        ),
        "lifestyle": (
            f"Lifestyle photography showing {product_name} being used in a natural setting. "
            "Warm, aspirational atmosphere. Real person using the product. "
            "Natural lighting, modern environment."
        ),
        "infographic": (
            f"Product infographic for {product_name}. "
            "Clean white background with product in center. "
            "Callout lines pointing to key features with bold text labels. "
            "Professional graphic design style."
        ),
        "comparison": (
            f"Product comparison image for {product_name}. "
            "Side-by-side layout showing advantages over generic alternatives. "
            "Clean, professional design with checkmarks and clear labels."
        ),
        "dimensions": (
            f"Product dimensions diagram for {product_name}. "
            "White background, product shown with measurement arrows and dimensions labeled. "
            "Include size reference object (hand or common item). Technical illustration style."
        ),
        "packaging": (
            f"Unboxing / what's in the box image for {product_name}. "
            "All included items laid out flat on white background. "
            "Clean, organized arrangement. Professional product photography."
        ),
        "in_use": (
            f"Close-up action shot of hands using {product_name}. "
            "Focus on the interaction between hands and product. "
            "Natural lighting, lifestyle feel. Shows ease of use."
        ),
        "before_after": (
            f"Before-and-after results image for {product_name}. "
            "Split visual clearly showing transformation outcome. "
            "Realistic, trustworthy presentation. No exaggerated or misleading claims."
        ),
        "ingredients": (
            f"Key ingredients callout image for {product_name}. "
            "Highlight 3-5 active ingredients with clean labels and concise benefit cues. "
            "Modern infographic style with product prominently visible."
        ),
        "how_to_use": (
            f"Step-by-step how-to-use guide for {product_name}. "
            "Three clear usage steps with simple icons and short captions. "
            "Readable, clean layout designed for Amazon listing images."
        ),
        "sensory": (
            f"Macro sensory close-up image for {product_name}. "
            "Show texture, lather, or material detail in a premium visual style. "
            "Soft lighting, high detail, aspirational but realistic."
        ),
        "certifications": (
            f"Claims and certifications image for {product_name}. "
            "Show product with clean badge area for certifications and standards. "
            "Minimalist trust-focused design, no competitor references."
        ),
        "social_proof": (
            f"Social proof image for {product_name}. "
            "Present customer sentiment snippets in a tasteful, readable layout. "
            "UGC-inspired composition with product hero focus."
        ),
        "variant_range": (
            f"Variant range image for {product_name}. "
            "Show all available variants in a cohesive lineup with consistent styling. "
            "Clear differentiation by color, scent, or size."
        ),
    }
    return slot_prompts.get(
        slot["type"], f"Professional product image of {product_name}."
    )


def _detect_image_mime(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _extract_generated_image_bytes(result: Any) -> bytes | None:
    generated_images = getattr(result, "generated_images", None) or []
    if not generated_images:
        return None

    image_obj = getattr(generated_images[0], "image", None)
    image_bytes = getattr(image_obj, "image_bytes", None)
    if not image_bytes:
        return None
    return bytes(image_bytes)


@st.cache_resource(show_spinner=False)
def _image_gallery_supports_binary() -> bool:
    """Return True when launchpad.image_gallery has image_bytes column."""
    return image_gallery_supports_binary(_open_conn, logger)


def _is_quota_error(exc: Exception) -> bool:
    return quota_is_quota_error(exc)


def _seconds_until_next_image_request(enforce_strict_spacing: bool = False) -> int:
    return quota_seconds_until_next_request(
        session=st.session_state,
        strict_spacing_seconds=IMAGEN_STRICT_CALL_SPACING_SECONDS,
        enforce_strict_spacing=enforce_strict_spacing,
    )


def _mark_imagen_request_attempt() -> None:
    quota_mark_request_attempt(st.session_state)


def _call_with_quota_retry(op_name: str, fn: Any) -> Any:
    return quota_call_with_retry(
        op_name=op_name,
        fn=fn,
        session=st.session_state,
        max_attempts=IMAGEN_RETRY_MAX_ATTEMPTS,
        base_seconds=IMAGEN_RETRY_BASE_SECONDS,
        max_seconds=IMAGEN_RETRY_MAX_SECONDS,
        quota_cooldown_seconds=IMAGEN_QUOTA_COOLDOWN_SECONDS,
        logger=logger,
    )


def _generate_image_with_imagen(
    prompt: str,
    reference_image_bytes: bytes | None = None,
    aspect_ratio: str = "1:1",
    enforce_strict_spacing: bool = False,
) -> tuple[bytes | None, bool]:
    """Generate image using Vertex Imagen via google.genai SDK."""
    try:
        remaining = _seconds_until_next_image_request(
            enforce_strict_spacing=enforce_strict_spacing
        )
        if remaining > 0:
            st.warning(
                f"⏳ Cooling down due to quota pacing. Please wait about {remaining}s before the next image request."
            )
            return None, False

        _mark_imagen_request_attempt()
        client = get_vertex_genai_client(location=VERTEX_LOCATION)
        used_reference = False

        if reference_image_bytes:
            try:
                source = {
                    "prompt": prompt,
                    "product_images": [
                        {
                            "product_image": {
                                "image_bytes": reference_image_bytes,
                                "mime_type": _detect_image_mime(reference_image_bytes),
                            }
                        }
                    ],
                }
                result = _call_with_quota_retry(
                    "Imagen recontext_image",
                    lambda: client.models.recontext_image(
                        model=IMAGEN_MODEL,
                        source=source,
                        config={
                            "number_of_images": 1,
                            "safety_filter_level": "block_some",
                            "person_generation": "allow_adult",
                        },
                    ),
                )
                image_bytes = _extract_generated_image_bytes(result)
                if image_bytes:
                    return image_bytes, True
                logger.warning(
                    "Imagen recontext returned no generated image, falling back to text-only generation"
                )
            except Exception as ref_exc:
                logger.warning(
                    "Reference-based image generation unavailable, falling back: %s",
                    ref_exc,
                )

        result = _call_with_quota_retry(
            "Imagen generate_images",
            lambda: client.models.generate_images(
                model=IMAGEN_MODEL,
                prompt=prompt,
                config={
                    "number_of_images": 1,
                    "aspect_ratio": aspect_ratio,
                    "safety_filter_level": "block_some",
                    "person_generation": "allow_adult",
                },
            ),
        )
        image_bytes = _extract_generated_image_bytes(result)
        if not image_bytes:
            logger.warning("Imagen returned no generated_images: %s", result)
            return None, used_reference

        st.session_state["cs_imagen_quota_cooldown_until"] = 0.0

        return image_bytes, used_reference
    except ImportError as exc:
        st.error(f"❌ Image generation dependency missing: {exc}")
        return None, False
    except FileNotFoundError as exc:
        st.error(f"❌ Google credentials not found: {exc}")
        return None, False
    except Exception as exc:
        err_text = str(exc)
        if _is_quota_error(exc):
            quota_hits = int(st.session_state.get("cs_imagen_quota_error_count", 0))
            st.session_state["cs_imagen_quota_error_count"] = quota_hits + 1
            st.session_state["cs_imagen_quota_last_error_at"] = time.time()
            if "cs_aplus_quota_hits_current_run" in st.session_state:
                st.session_state["cs_aplus_quota_hits_current_run"] = (
                    int(st.session_state.get("cs_aplus_quota_hits_current_run", 0)) + 1
                )
            st.error(
                "❌ Image generation is rate-limited by Google quota. "
                f"A cooldown of about {int(IMAGEN_QUOTA_COOLDOWN_SECONDS)}s has been applied. "
                "Please retry after cooldown, reduce selected assets, or request a quota increase."
            )
            logger.warning("Image generation quota limit: %s", exc)
            return None, False
        if "PERMISSION_DENIED" in err_text or "SERVICE_DISABLED" in err_text:
            st.error(
                "❌ Image generation is not authorized. "
                "Enable Vertex AI API and grant this service account a Vertex AI role "
                "(for example, Vertex AI User)."
            )
        st.error(f"❌ Image generation failed: {exc}")
        logger.error("Image generation error: %s", exc)
        return None, False


def _save_image_to_gallery(
    launch_id: int,
    slot_number: int,
    image_type: str,
    prompt_used: str,
    image_bytes: bytes | None,
    model_used: str,
    storage_path: str | None = None,
) -> bool:
    """Save image metadata to DB. Returns True on success."""
    return save_image_to_gallery(
        open_conn=_open_conn,
        launch_id=launch_id,
        slot_number=slot_number,
        image_type=image_type,
        prompt_used=prompt_used,
        image_bytes=image_bytes,
        model_used=model_used,
        storage_path=storage_path,
        supports_binary=_image_gallery_supports_binary(),
        logger=logger,
    )


def _load_image_gallery(launch_id: int) -> dict[int, dict[str, Any]]:
    """Load image gallery records for a launch. Returns dict keyed by slot_number."""
    return load_image_gallery(
        open_conn=_open_conn,
        launch_id=launch_id,
        supports_binary=_image_gallery_supports_binary(),
        logger=logger,
    )


def _slot_has_image(data: dict[str, Any] | None) -> bool:
    if not data:
        return False
    return bool(data.get("image_bytes") or data.get("storage_path"))


# ---------------------------------------------------------------------------
# Per-launch slot configuration helpers
# ---------------------------------------------------------------------------


def _get_slot_config_key(launch_id: int) -> str:
    return f"cs_slot_config_{launch_id}"


def _infer_category_overrides(product_desc: str) -> dict[int, str]:
    """Return slot overrides based on product category keywords in the description."""
    desc_lower = (product_desc or "").lower()
    for keyword, overrides in CATEGORY_SLOT_OVERRIDES.items():
        if keyword in desc_lower:
            return overrides
    return {}


def _get_active_slot_types(launch_id: int, product_desc: str) -> dict[int, str]:
    """Return the active slot-type mapping for this launch.

    Precedence:
      1. Per-launch session-state config (user choices)
      2. Category-aware defaults inferred from product description
      3. DEFAULT_SLOT_TYPES
    """
    config_key = _get_slot_config_key(launch_id)
    if config_key not in st.session_state:
        # Seed with defaults, then apply category overrides
        base = dict(DEFAULT_SLOT_TYPES)
        base.update(_infer_category_overrides(product_desc))
        st.session_state[config_key] = base
    return st.session_state[config_key]


def _get_slot_info(slot_num: int, launch_id: int, product_desc: str) -> dict[str, str]:
    """Return the resolved slot-info dict (type + catalogue metadata) for a slot."""
    active_types = _get_active_slot_types(launch_id, product_desc)
    type_key = active_types.get(slot_num, DEFAULT_SLOT_TYPES.get(slot_num, "lifestyle"))
    entry = IMAGE_TYPE_CATALOGUE.get(type_key, IMAGE_TYPE_CATALOGUE["lifestyle"])
    return {"type": type_key, **entry}


def _render_slot_type_selector(
    slot_num: int,
    launch_id: int,
    product_desc: str,
) -> dict[str, str]:
    """Render the type-selector dropdown for a configurable slot (2-7).

    Returns the resolved slot_info for the currently selected type.
    """
    config_key = _get_slot_config_key(launch_id)
    active_types = _get_active_slot_types(launch_id, product_desc)
    current_type = active_types.get(slot_num, DEFAULT_SLOT_TYPES[slot_num])

    # Build display labels for selectbox
    type_keys = list(IMAGE_TYPE_CATALOGUE.keys())
    # Exclude main_white_bg from configurable slots
    type_keys = [k for k in type_keys if k != "main_white_bg"]
    labels = [
        f"{IMAGE_TYPE_CATALOGUE[k]['icon']} {IMAGE_TYPE_CATALOGUE[k]['name']}"
        for k in type_keys
    ]
    current_idx = type_keys.index(current_type) if current_type in type_keys else 0

    selected_label = st.selectbox(
        "Image type",
        options=labels,
        index=current_idx,
        key=f"cs_slot_type_select_{launch_id}_{slot_num}",
        label_visibility="collapsed",
        help="Choose what kind of image this slot should show.",
    )
    selected_type = type_keys[labels.index(selected_label)]

    # Persist if changed; also reset cached prompt so it regenerates for the new type
    if selected_type != current_type:
        active_types[slot_num] = selected_type
        st.session_state[config_key] = active_types
        prompt_key = f"cs_slot_prompt_{launch_id}_{slot_num}"
        if prompt_key in st.session_state:
            del st.session_state[prompt_key]
        input_key = f"{prompt_key}_input"
        if input_key in st.session_state:
            del st.session_state[input_key]
        prompt_mode_input_key = f"{prompt_key}_prompt_mode_input"
        if prompt_mode_input_key in st.session_state:
            del st.session_state[prompt_mode_input_key]

    entry = IMAGE_TYPE_CATALOGUE[selected_type]
    return {"type": selected_type, **entry}


def _render_image_prompt_cards(
    launch_id: int,
    product_name: str,
    product_desc: str,
) -> None:
    """Prompt-mode view: display the 7 image prompts as copyable cards."""
    st.markdown(
        "Copy each prompt below into **Gemini 3** (or any image generator) "
        "to produce an Amazon-ready image for that slot."
    )
    total_slots = len(DEFAULT_SLOT_TYPES)
    for slot_num in range(1, total_slots + 1):
        prompt_key = f"cs_slot_prompt_{launch_id}_{slot_num}"
        with st.container(border=True):
            slot_info = _get_slot_info(slot_num, launch_id, product_desc)
            hdr_col, type_col = st.columns([6, 4])
            with hdr_col:
                st.markdown(
                    f"**Slot {slot_num} — {slot_info['icon']} {slot_info['name']}**  \n"
                    f"<span style='color:grey;font-size:0.85em'>{slot_info['desc']}</span>",
                    unsafe_allow_html=True,
                )
            with type_col:
                if slot_num > 1:
                    slot_info = _render_slot_type_selector(
                        slot_num, launch_id, product_desc
                    )

            if prompt_key not in st.session_state:
                st.session_state[prompt_key] = _build_image_prompt(
                    slot_info, product_name, product_desc
                )

            prompt_text = st.text_area(
                "Prompt",
                value=st.session_state.get(prompt_key, ""),
                key=f"{prompt_key}_prompt_mode_input",
                height=110,
                label_visibility="collapsed",
                help="Edit then copy into Gemini 3 or another image tool.",
            )
            st.session_state[prompt_key] = prompt_text
            st.caption(
                f"**Amazon spec:** 2000×2000 px minimum, RGB JPEG, sRGB colour space. "
                f"Slot 1 (Main Image): pure white background, product fills ≥85% of frame."
                if slot_num == 1
                else f"**Amazon spec:** 2000×2000 px minimum, RGB JPEG, sRGB colour space."
            )


def _render_image_gallery(launch: dict[str, Any]) -> None:
    st.subheader("🖼️ Image Gallery (7 Slots)")
    st.caption("Amazon requires 7 images for optimal listing performance.")

    # --- Output mode toggle ---
    mode_col, spacer = st.columns([2, 5])
    with mode_col:
        prompt_mode = st.toggle(
            "Prompt mode (no image generation)",
            value=st.session_state.get("cs_gallery_prompt_mode", False),
            key="cs_gallery_prompt_mode_toggle",
            help="Switch between generating images via Vertex AI Imagen and outputting "
            "ready-to-use Gemini 3 prompts for each slot.",
        )
    st.session_state["cs_gallery_prompt_mode"] = prompt_mode

    launch_id = launch["launch_id"]
    launch_description = launch.get("product_description") or ""
    product_name = (
        st.session_state.get("cs_product_name") or launch_description or "Product"
    )
    product_desc = launch_description

    if prompt_mode:
        _render_image_prompt_cards(launch_id, product_name, product_desc)
        return

    pacing_col1, pacing_col2 = st.columns([2, 3])
    with pacing_col1:
        strict_spacing = st.checkbox(
            "Conservative quota mode (60s between calls)",
            value=st.session_state.get("cs_imagen_strict_spacing", True),
            key="cs_imagen_strict_spacing_input",
            help="Reduces 429 quota errors by forcing a cooldown between image generation requests.",
        )
        st.session_state["cs_imagen_strict_spacing"] = strict_spacing
    with pacing_col2:
        remaining = _seconds_until_next_image_request(enforce_strict_spacing=True)
        if remaining > 0:
            st.info(f"Next image request available in ~{remaining}s")
        else:
            st.caption("Image generation ready")

    # Load existing gallery from DB
    db_gallery = _load_image_gallery(launch_id)

    # Merge with session state
    gallery = st.session_state.get("cs_image_gallery", {})
    for slot_num, db_data in db_gallery.items():
        if slot_num not in gallery or not gallery[slot_num].get("image_bytes"):
            gallery[slot_num] = {"status": "db_record", **db_data}
    st.session_state["cs_image_gallery"] = gallery

    # Render in 2-column grid
    total_slots = len(DEFAULT_SLOT_TYPES)
    slot_items = [
        (slot_num, _get_slot_info(slot_num, launch_id, product_desc))
        for slot_num in range(1, total_slots + 1)
    ]
    for row_start in range(0, len(slot_items), 2):
        cols = st.columns(2)
        for col_idx, (slot_num, slot_info) in enumerate(
            slot_items[row_start : row_start + 2]
        ):
            with cols[col_idx]:
                _render_image_slot(
                    launch_id, slot_num, slot_info, product_name, product_desc, gallery
                )

    # Summary
    filled_slots = sum(
        1
        for s in range(1, total_slots + 1)
        if _slot_has_image(gallery.get(s)) or _slot_has_image(db_gallery.get(s))
    )
    st.markdown(f"**Gallery Progress:** {filled_slots} / {total_slots} slots filled")
    if filled_slots < total_slots:
        st.progress(
            filled_slots / float(total_slots),
            text=f"{filled_slots}/{total_slots} images ready",
        )
    else:
        st.success(f"✅ All {total_slots} image slots filled!")


def _render_image_slot(
    launch_id: int,
    slot_num: int,
    slot_info: dict[str, str],
    product_name: str,
    product_desc: str,
    gallery: dict[int, Any],
) -> None:
    slot_data = gallery.get(slot_num, {})
    has_image = bool(slot_data.get("image_bytes") or slot_data.get("storage_path"))
    status_icon = "✅" if has_image else "⬜"
    prompt_key = f"cs_slot_prompt_{launch_id}_{slot_num}"

    with st.container(border=True):
        header_col, selector_col = st.columns([6, 4])
        with header_col:
            st.markdown(
                f"**{status_icon} Slot {slot_num}: {slot_info['icon']} {slot_info['name']}**"
            )
            st.caption(slot_info["desc"])
        with selector_col:
            if slot_num > 1:
                slot_info = _render_slot_type_selector(
                    slot_num, launch_id, product_desc
                )
                st.caption(f"Selected: {slot_info['icon']} {slot_info['name']}")

        if prompt_key not in st.session_state:
            st.session_state[prompt_key] = _build_image_prompt(
                slot_info, product_name, product_desc
            )

        # Show image if available
        if slot_data.get("image_bytes"):
            st.image(slot_data["image_bytes"], use_container_width=True)
        elif slot_data.get("uploaded_file"):
            st.image(slot_data["uploaded_file"], use_container_width=True)
        elif has_image:
            st.info(f"📁 Image saved: {slot_data.get('storage_path', 'DB record')}")

        # Requirements checklist
        with st.expander("📋 Requirements", expanded=False):
            reqs = _get_slot_requirements(slot_info.get("type", "lifestyle"))
            for req in reqs:
                st.markdown(f"• {req}")

        prompt_text = st.text_area(
            "Generation Prompt",
            value=st.session_state.get(prompt_key, ""),
            key=f"{prompt_key}_input",
            height=100,
            help="Edit this prompt before generating the slot image.",
        )
        st.session_state[prompt_key] = prompt_text

        # Action buttons
        btn_col1, btn_col2 = st.columns(2)

        has_reference = bool(slot_data.get("image_bytes"))
        use_reference_default = bool(
            has_reference and slot_info.get("type") in {"lifestyle", "in_use"}
        )
        use_reference = st.checkbox(
            "Use current slot image as inspiration",
            value=use_reference_default,
            key=f"cs_use_ref_img_{slot_num}",
            disabled=not has_reference,
            help=(
                "Uses the currently stored slot image as a product reference for new generation. "
                "Upload a product photo first if this slot is empty."
            ),
        )

        with btn_col1:
            if st.button(
                "🤖 Generate",
                key=f"cs_gen_img_{slot_num}",
                use_container_width=True,
                help="Generate with Google Imagen 3",
            ):
                prompt = (prompt_text or "").strip() or _build_image_prompt(
                    slot_info, product_name, product_desc
                )
                reference_image = (
                    slot_data.get("image_bytes") if use_reference else None
                )
                with st.spinner(f"Generating {slot_info['name']}..."):
                    img_bytes, used_reference = _generate_image_with_imagen(
                        prompt,
                        reference_image_bytes=reference_image,
                        enforce_strict_spacing=True,
                    )

                if img_bytes:
                    prompt_used = (
                        f"{prompt}\n[reference_image:slot_{slot_num}]"
                        if used_reference
                        else prompt
                    )
                    gallery[slot_num] = {
                        "image_bytes": img_bytes,
                        "prompt_used": prompt_used,
                        "model_used": (
                            f"{IMAGEN_MODEL}:recontext"
                            if used_reference
                            else IMAGEN_MODEL
                        ),
                        "status": (
                            "generated_with_reference"
                            if used_reference
                            else "generated"
                        ),
                    }
                    st.session_state["cs_image_gallery"] = gallery
                    _save_image_to_gallery(
                        launch_id,
                        slot_num,
                        slot_info["type"],
                        prompt_used,
                        img_bytes,
                        f"{IMAGEN_MODEL}:recontext" if used_reference else IMAGEN_MODEL,
                    )
                    if used_reference:
                        st.success(
                            f"✅ {slot_info['name']} generated using your reference image!"
                        )
                    else:
                        st.success(f"✅ {slot_info['name']} generated!")
                    st.rerun()
                else:
                    st.warning(
                        "⚠️ Image generation returned no results. "
                        "Verify Vertex AI access and service account permissions."
                    )

        with btn_col2:
            uploaded = st.file_uploader(
                "Upload",
                type=["jpg", "jpeg", "png", "webp"],
                key=f"cs_upload_img_{slot_num}",
                label_visibility="collapsed",
            )
            if uploaded is not None:
                img_bytes = uploaded.getvalue()
                fingerprint = hashlib.sha1(img_bytes).hexdigest()
                uploaded_fingerprints = st.session_state.get(
                    "cs_upload_fingerprints", {}
                )
                previous_fingerprint = uploaded_fingerprints.get(slot_num)

                if previous_fingerprint != fingerprint:
                    gallery[slot_num] = {
                        "image_bytes": img_bytes,
                        "uploaded_file": img_bytes,
                        "prompt_used": "manual_upload",
                        "model_used": "upload",
                        "status": "uploaded",
                    }
                    st.session_state["cs_image_gallery"] = gallery
                    uploaded_fingerprints[slot_num] = fingerprint
                    st.session_state["cs_upload_fingerprints"] = uploaded_fingerprints
                    _save_image_to_gallery(
                        launch_id,
                        slot_num,
                        slot_info["type"],
                        "manual_upload",
                        img_bytes,
                        "upload",
                    )
                    st.success(f"✅ {slot_info['name']} uploaded!")
                    st.rerun()


def _get_slot_requirements(slot_type: str) -> list[str]:
    requirements = {
        "main_white_bg": [
            "Pure white background (RGB 255,255,255)",
            "Product fills 85%+ of frame",
            "No text, logos, or watermarks",
            "Min 1000x1000px, max 10,000px",
            "JPEG or PNG format",
        ],
        "lifestyle": [
            "Shows product in real-world use",
            "Aspirational lifestyle setting",
            "High quality, well-lit",
            "No competitor products visible",
        ],
        "infographic": [
            "Key features labeled with callouts",
            "Clean, readable typography",
            "Product clearly visible",
            "Benefits-focused messaging",
        ],
        "comparison": [
            "Fair, accurate comparison",
            "No competitor brand names",
            "Clear advantage indicators",
            "Professional design",
        ],
        "dimensions": [
            "Accurate measurements shown",
            "Size reference included",
            "All dimensions labeled",
            "Technical accuracy required",
        ],
        "packaging": [
            "All included items visible",
            "Clean flat-lay arrangement",
            "White or neutral background",
            "Every accessory shown",
        ],
        "in_use": [
            "Shows ease of use",
            "Hands/person interacting with product",
            "Natural, authentic feel",
            "Focus on user experience",
        ],
        "before_after": [
            "Transformation shown clearly and honestly",
            "Consistent lighting/composition between before and after",
            "No exaggerated or unverifiable claims",
            "Readable labels if split-screen",
        ],
        "ingredients": [
            "Key ingredients clearly named",
            "Benefit callouts are concise and readable",
            "Product remains prominent",
            "Design remains clean and compliant",
        ],
        "how_to_use": [
            "3-4 clear usage steps",
            "Simple visual sequence",
            "Readable text at mobile size",
            "Guidance is practical and unambiguous",
        ],
        "sensory": [
            "Texture/detail shown with high clarity",
            "Premium lighting and color accuracy",
            "Visual evokes feel/scent/experience",
            "Product identity remains clear",
        ],
        "certifications": [
            "Claims are factual and supportable",
            "Badges/cert marks are clear but not cluttered",
            "No misleading medical/compliance implication",
            "Trust-focused clean layout",
        ],
        "social_proof": [
            "Customer sentiment is readable and concise",
            "No unverifiable superlative claims",
            "Product remains hero element",
            "Authentic, non-spammy visual style",
        ],
        "variant_range": [
            "All variants are clearly differentiated",
            "Consistent styling across variants",
            "Variant labels are readable",
            "Range breadth is obvious at a glance",
        ],
    }
    return requirements.get(slot_type, ["High quality product image"])


def _infer_aspect_ratio(width: int, height: int) -> str:
    ratio = width / max(1, height)
    if ratio >= 2.0:
        return "21:9"
    if ratio >= 1.45:
        return "16:9"
    if ratio >= 1.2:
        return "4:3"
    if ratio <= 0.8:
        return "3:4"
    return "1:1"


def _resize_cover(image_bytes: bytes, width: int, height: int) -> bytes | None:
    try:
        from PIL import Image
    except Exception as exc:
        logger.error("Pillow is required for A+ resizing: %s", exc)
        return None

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            src_w, src_h = img.size
            if src_w <= 0 or src_h <= 0:
                return None

            scale = max(width / src_w, height / src_h)
            resized = img.resize(
                (max(1, int(src_w * scale)), max(1, int(src_h * scale))),
                Image.Resampling.LANCZOS,
            )

            left = max(0, (resized.width - width) // 2)
            top = max(0, (resized.height - height) // 2)
            cropped = resized.crop((left, top, left + width, top + height)).convert(
                "RGB"
            )

            buf = io.BytesIO()
            cropped.save(buf, format="JPEG", quality=95, optimize=True)
            return buf.getvalue()
    except Exception as exc:
        logger.error("Failed to resize A+ image to %sx%s: %s", width, height, exc)
        return None


def _generate_aplus_copy(
    product_name: str,
    listing: dict[str, Any] | None,
    brand_voice: str,
) -> dict[str, Any] | None:
    try:
        genai = get_generative_client()
        model = genai.GenerativeModel(GEMINI_MODEL)
        listing_payload = {
            "title": (listing or {}).get("title", ""),
            "bullets": (listing or {}).get("bullets", []),
            "description": (listing or {}).get("description", ""),
            "backend_keywords": (listing or {}).get("backend_keywords", ""),
        }
        prompt = f"""You are creating Amazon A+ Content copy and image prompts.

Brand voice: {brand_voice}
Product: {product_name}
Listing context:
{json.dumps(listing_payload, ensure_ascii=True)}

Return STRICT JSON only in this schema:
{{
  "hero": {{"headline": "...", "body": "...", "image_prompt": "..."}},
  "brand_story": {{"headline": "...", "body": "...", "image_prompt": "..."}},
  "feature_tiles": [
    {{"title": "...", "body": "...", "image_prompt": "..."}},
    {{"title": "...", "body": "...", "image_prompt": "..."}},
    {{"title": "...", "body": "...", "image_prompt": "..."}}
  ],
  "comparison": {{"title": "...", "body": "...", "image_prompt": "..."}}
}}

Rules:
- Keep headlines short and punchy.
- Keep each body under 220 characters.
- Image prompts must describe realistic Amazon-safe product visuals.
- Avoid medical/legal claims.
"""
        response = model.generate_content(prompt)
        parsed = _parse_json_object_from_text(str(getattr(response, "text", "") or ""))
        if not isinstance(parsed, dict):
            return None
        return parsed
    except json.JSONDecodeError as exc:
        st.error(
            f"❌ A+ copy generation returned invalid JSON: {exc}. "
            "Try regenerate; if repeated, reduce prompt complexity."
        )
        logger.error("A+ copy JSON parse failed: %s", exc)
        return None
    except FileNotFoundError as exc:
        st.error(f"❌ Google credentials not found: {exc}")
        logger.error("A+ credentials missing: %s", exc)
        return None
    except Exception as exc:
        st.error(f"❌ A+ copy generation error: {exc}")
        logger.error("A+ copy generation failed: %s", exc)
        return None


def _build_aplus_image_prompts(aplus_copy: dict[str, Any]) -> dict[str, str]:
    prompts: dict[str, str] = {}
    hero = aplus_copy.get("hero", {}) if isinstance(aplus_copy, dict) else {}
    story = aplus_copy.get("brand_story", {}) if isinstance(aplus_copy, dict) else {}
    comparison = (
        aplus_copy.get("comparison", {}) if isinstance(aplus_copy, dict) else {}
    )
    tiles = aplus_copy.get("feature_tiles", []) if isinstance(aplus_copy, dict) else []

    prompts["hero_banner"] = str(
        hero.get("image_prompt") or "Premium hero banner product photography."
    )
    prompts["brand_story"] = str(
        story.get("image_prompt") or "Product story image with human context."
    )
    prompts["comparison"] = str(
        comparison.get("image_prompt") or "Clean comparison visual for product."
    )

    for idx in range(3):
        row = (
            tiles[idx]
            if isinstance(tiles, list)
            and idx < len(tiles)
            and isinstance(tiles[idx], dict)
            else {}
        )
        prompts[f"feature_{idx + 1}"] = str(
            row.get("image_prompt") or f"Feature tile {idx + 1} product image."
        )
    return prompts


def _serialize_aplus_assets_for_storage(
    assets: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    serialized: dict[str, dict[str, Any]] = {}
    for asset_key, asset in (assets or {}).items():
        raw_bytes = asset.get("bytes") if isinstance(asset, dict) else None
        encoded = (
            base64.b64encode(raw_bytes).decode("ascii")
            if isinstance(raw_bytes, bytes)
            else None
        )
        serialized[asset_key] = {
            "width": int(asset.get("width") or 0),
            "height": int(asset.get("height") or 0),
            "prompt": str(asset.get("prompt") or ""),
            "used_reference": bool(asset.get("used_reference")),
            "image_base64": encoded,
        }
    return serialized


def _deserialize_aplus_assets_from_storage(
    payload: Any,
) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    restored: dict[str, dict[str, Any]] = {}
    for asset_key, row in payload.items():
        if not isinstance(row, dict):
            continue
        encoded = row.get("image_base64")
        image_bytes: bytes | None = None
        if isinstance(encoded, str) and encoded:
            try:
                image_bytes = base64.b64decode(encoded, validate=True)
            except Exception:
                image_bytes = None
        if image_bytes:
            restored[str(asset_key)] = {
                "bytes": image_bytes,
                "width": int(row.get("width") or 0),
                "height": int(row.get("height") or 0),
                "prompt": str(row.get("prompt") or ""),
                "used_reference": bool(row.get("used_reference")),
            }
    return restored


def _hydrate_aplus_package_from_listing_content(
    launch_id: int,
    aplus_content: Any,
) -> None:
    if not isinstance(aplus_content, dict):
        return
    assets = _deserialize_aplus_assets_from_storage(aplus_content.get("assets"))
    if not assets:
        return
    st.session_state["cs_aplus_package"] = {
        "launch_id": launch_id,
        "generated_at": str(aplus_content.get("generated_at") or ""),
        "copy": aplus_content.get("copy")
        if isinstance(aplus_content.get("copy"), dict)
        else {},
        "assets": assets,
    }


def _aplus_asset_signature(
    launch_id: int,
    asset_key: str,
    prompt: str,
    width: int,
    height: int,
    reference_bytes: bytes | None,
    use_gallery_reference: bool,
) -> str:
    reference_hash = (
        hashlib.sha1(reference_bytes).hexdigest() if reference_bytes else "no_reference"
    )
    payload = {
        "launch_id": launch_id,
        "asset_key": asset_key,
        "prompt": prompt.strip(),
        "width": width,
        "height": height,
        "aspect_ratio": _infer_aspect_ratio(width, height),
        "reference_hash": reference_hash,
        "use_gallery_reference": bool(use_gallery_reference),
        "model": IMAGEN_MODEL,
        "vertex_location": VERTEX_LOCATION,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()


def _render_aplus_prompt_cards(
    product_name: str,
    listing: dict[str, Any] | None,
    brand_voice: str,
) -> None:
    """Prompt-mode view: call Gemini for A+ copy+prompts then display as cards."""
    run_col, _ = st.columns([2, 5])
    with run_col:
        generate = st.button(
            "Generate A+ Prompts",
            type="primary",
            use_container_width=True,
            key="cs_aplus_gen_prompts_btn",
        )

    if generate:
        with st.spinner("Generating A+ copy and image prompts via Gemini..."):
            result = _generate_aplus_copy(product_name, listing, brand_voice)
        if result:
            st.session_state["cs_aplus_prompt_mode_copy"] = result
        else:
            st.warning("Could not generate A+ prompts. Review the error above.")
            return

    copy = st.session_state.get("cs_aplus_prompt_mode_copy")
    if not copy:
        st.info(
            "Click **Generate A+ Prompts** to produce image prompts and copy via Gemini."
        )
        return

    prompt_map = _build_aplus_image_prompts(copy)

    # A+ copy section
    with st.expander("📄 A+ Copy (headlines, body text)", expanded=True):
        hero = copy.get("hero", {})
        story = copy.get("brand_story", {})
        tiles = copy.get("feature_tiles", [])
        comparison = copy.get("comparison", {})

        st.markdown("**Hero Banner**")
        st.markdown(f"Headline: *{hero.get('headline', '—')}*")
        st.markdown(f"Body: {hero.get('body', '—')}")
        st.divider()

        st.markdown("**Brand Story**")
        st.markdown(f"Headline: *{story.get('headline', '—')}*")
        st.markdown(f"Body: {story.get('body', '—')}")
        st.divider()

        for i, tile in enumerate(tiles[:3], 1):
            st.markdown(f"**Feature Tile {i}**")
            st.markdown(f"Title: *{tile.get('title', '—')}*")
            st.markdown(f"Body: {tile.get('body', '—')}")
        st.divider()

        st.markdown("**Comparison**")
        st.markdown(f"Title: *{comparison.get('title', '—')}*")
        st.markdown(f"Body: {comparison.get('body', '—')}")

    # Image prompt cards
    st.markdown("### Image Prompts")
    st.markdown(
        "Copy each prompt into **Gemini 3** (or another image tool), "
        "then resize to the Amazon A+ module dimensions shown."
    )

    for asset_key, spec in APLUS_IMAGE_SPECS.items():
        prompt_text = prompt_map.get(asset_key, "")
        prompt_ss_key = f"cs_aplus_prompt_{asset_key}"
        if prompt_ss_key not in st.session_state or generate:
            st.session_state[prompt_ss_key] = prompt_text

        with st.container(border=True):
            label = asset_key.replace("_", " ").title()
            st.markdown(
                f"**{label}** &nbsp; "
                f"<span style='color:grey;font-size:0.85em'>"
                f"{spec['width']}×{spec['height']} px — {spec['desc']}"
                f"</span>",
                unsafe_allow_html=True,
            )
            edited = st.text_area(
                "Prompt",
                value=st.session_state[prompt_ss_key],
                key=f"{prompt_ss_key}_input",
                height=100,
                label_visibility="collapsed",
                help="Edit then copy into Gemini 3 or another image tool.",
            )
            st.session_state[prompt_ss_key] = edited
            st.caption(
                f"Amazon A+ spec: exactly {spec['width']}×{spec['height']} px, JPEG."
            )

    # Copy-all bundle
    with st.expander("📋 All prompts as plain text (copy-all)", expanded=False):
        bundle_lines = []
        for asset_key, spec in APLUS_IMAGE_SPECS.items():
            label = asset_key.replace("_", " ").title()
            p = st.session_state.get(f"cs_aplus_prompt_{asset_key}", "")
            bundle_lines.append(
                f"--- {label} ({spec['width']}x{spec['height']}px) ---\n{p}\n"
            )
        st.text_area(
            "All prompts",
            value="\n".join(bundle_lines),
            height=300,
            label_visibility="collapsed",
            key="cs_aplus_prompt_bundle",
        )


def _render_aplus_engine(
    launch: dict[str, Any], listing: dict[str, Any] | None
) -> None:
    st.subheader("🧩 A+ Content Studio")
    st.caption(
        "Generates Amazon-ready A+ copy + images using your listing and gallery images. "
        "Assets are resized to Amazon module dimensions."
    )

    # --- Output mode toggle ---
    mode_col, spacer = st.columns([2, 5])
    with mode_col:
        prompt_mode = st.toggle(
            "Prompt mode (no image generation)",
            value=st.session_state.get("cs_aplus_prompt_mode", False),
            key="cs_aplus_prompt_mode_toggle",
            help="Switch between generating images via Vertex AI Imagen and outputting "
            "ready-to-use Gemini 3 prompts for each A+ module.",
        )
    st.session_state["cs_aplus_prompt_mode"] = prompt_mode

    launch_id = int(launch["launch_id"])
    if not st.session_state.get("cs_aplus_package") and listing is not None:
        _hydrate_aplus_package_from_listing_content(
            launch_id, listing.get("a_plus_content")
        )
    gallery = st.session_state.get("cs_image_gallery", {})
    product_name = (
        st.session_state.get("cs_product_name")
        or launch.get("product_description")
        or "Product"
    )

    if prompt_mode:
        _render_aplus_prompt_cards(
            product_name=product_name,
            listing=listing,
            brand_voice=st.session_state.get("cs_brand_voice", "Professional"),
        )
        return

    last_stats = st.session_state.get("cs_aplus_last_run_stats") or {}
    if int(last_stats.get("launch_id") or -1) == launch_id:
        quota_failures = int(last_stats.get("quota_limited_failures", 0))
        cooldown_seconds = 60 if quota_failures > 0 else 0
        ended_at = float(last_stats.get("ended_at_epoch") or 0.0)
        remaining_seconds = max(
            0, int(cooldown_seconds - max(0.0, time.time() - ended_at))
        )

        st.markdown("**Quota Dashboard**")
        metric_cols = st.columns(4)
        metric_cols[0].metric("Generated", int(last_stats.get("generated", 0)))
        metric_cols[1].metric("Cached", int(last_stats.get("cached", 0)))
        metric_cols[2].metric("Failed", int(last_stats.get("failed", 0)))
        metric_cols[3].metric(
            "Recommended wait",
            f"{remaining_seconds}s" if remaining_seconds else "ready",
        )
        if quota_failures > 0 and remaining_seconds > 0:
            st.info(
                f"Quota throttling detected in last run ({quota_failures} failed requests). "
                f"Recommended cooldown: ~{remaining_seconds}s before next full batch."
            )

    existing_package = st.session_state.get("cs_aplus_package") or {}
    existing_assets = (
        dict(existing_package.get("assets", {}))
        if int(existing_package.get("launch_id") or -1) == launch_id
        else {}
    )

    col_a1, col_a2, col_a3 = st.columns([2, 1, 1])
    with col_a2:
        use_gallery_reference = st.checkbox(
            "Use slot images as references",
            value=True,
            key="cs_aplus_use_gallery_ref",
            help="Uses existing slot images to preserve product consistency in generated A+ assets.",
        )
    with col_a3:
        skip_cached_assets = st.checkbox(
            "Reuse cached assets",
            value=True,
            key="cs_aplus_reuse_cached",
            help="Skips API calls when prompt and dimensions have already been generated.",
        )

    selected_assets = st.multiselect(
        "A+ image modules to generate",
        options=list(APLUS_IMAGE_SPECS.keys()),
        default=list(APLUS_IMAGE_SPECS.keys()),
        key="cs_aplus_selected_assets",
        format_func=lambda key: (
            f"{key} ({APLUS_IMAGE_SPECS[key]['width']}x{APLUS_IMAGE_SPECS[key]['height']})"
        ),
        help="Generate only the assets you need right now to reduce quota bursts.",
    )
    preserve_unselected_assets = st.checkbox(
        "Keep previously generated assets for unselected modules",
        value=True,
        key="cs_aplus_keep_unselected",
    )

    with col_a1:
        if st.button(
            "🚀 Generate A+ Package", type="primary", use_container_width=True
        ):
            if not selected_assets:
                st.warning("Select at least one image module before generating.")
                return

            with st.spinner("Generating A+ copy and images..."):
                st.session_state["cs_aplus_quota_hits_current_run"] = 0
                aplus_copy = _generate_aplus_copy(
                    product_name=product_name,
                    listing=listing,
                    brand_voice=st.session_state.get("cs_brand_voice", "Professional"),
                )
                if not aplus_copy:
                    st.session_state.pop("cs_aplus_quota_hits_current_run", None)
                    st.warning(
                        "A+ copy could not be generated. Review the detailed error above and retry."
                    )
                    return

                prompt_map = _build_aplus_image_prompts(aplus_copy)
                assets: dict[str, dict[str, Any]] = (
                    dict(existing_assets) if preserve_unselected_assets else {}
                )
                asset_cache = st.session_state.get("cs_aplus_asset_cache", {})

                generated_count = 0
                cached_count = 0
                failed_count = 0

                for asset_key, spec in APLUS_IMAGE_SPECS.items():
                    if asset_key not in selected_assets:
                        continue

                    prompt = prompt_map.get(asset_key, "Professional product image")
                    slot_ref = next(
                        (
                            data.get("image_bytes")
                            for slot, data in gallery.items()
                            if _get_slot_info(
                                int(slot),
                                launch_id,
                                launch.get("product_description") or "",
                            ).get("type")
                            == spec["slot_type"]
                            and data.get("image_bytes")
                        ),
                        None,
                    )
                    reference_bytes = slot_ref if use_gallery_reference else None
                    cache_key = _aplus_asset_signature(
                        launch_id=launch_id,
                        asset_key=asset_key,
                        prompt=prompt,
                        width=spec["width"],
                        height=spec["height"],
                        reference_bytes=reference_bytes,
                        use_gallery_reference=use_gallery_reference,
                    )

                    if skip_cached_assets and cache_key in asset_cache:
                        cached_asset = dict(asset_cache[cache_key])
                        cached_asset["cache_hit"] = True
                        assets[asset_key] = cached_asset
                        cached_count += 1
                        continue

                    raw_bytes, used_ref = _generate_image_with_imagen(
                        prompt,
                        reference_image_bytes=reference_bytes,
                        aspect_ratio=_infer_aspect_ratio(spec["width"], spec["height"]),
                    )
                    if not raw_bytes:
                        logger.warning("A+ asset generation failed for %s", asset_key)
                        failed_count += 1
                        continue

                    final_bytes = _resize_cover(
                        raw_bytes, spec["width"], spec["height"]
                    )
                    if not final_bytes:
                        failed_count += 1
                        continue

                    asset_record = {
                        "bytes": final_bytes,
                        "width": spec["width"],
                        "height": spec["height"],
                        "prompt": prompt,
                        "used_reference": used_ref,
                        "cache_key": cache_key,
                        "cache_hit": False,
                    }
                    assets[asset_key] = asset_record
                    asset_cache[cache_key] = dict(asset_record)
                    generated_count += 1

                    if APLUS_IMAGE_CALL_SPACING_SECONDS > 0:
                        time.sleep(APLUS_IMAGE_CALL_SPACING_SECONDS)

                st.session_state["cs_aplus_asset_cache"] = asset_cache

                quota_limited_failures = int(
                    st.session_state.get("cs_aplus_quota_hits_current_run", 0)
                )
                st.session_state["cs_aplus_last_run_stats"] = {
                    "launch_id": launch_id,
                    "generated": generated_count,
                    "cached": cached_count,
                    "failed": failed_count,
                    "quota_limited_failures": quota_limited_failures,
                    "selected_assets": list(selected_assets),
                    "ended_at_epoch": time.time(),
                }
                st.session_state.pop("cs_aplus_quota_hits_current_run", None)

                package = {
                    "launch_id": launch_id,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "copy": aplus_copy,
                    "assets": assets,
                }
                st.session_state["cs_aplus_package"] = package

                if listing is not None:
                    serialized_assets = _serialize_aplus_assets_for_storage(assets)
                    listing["a_plus_content"] = {
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "copy": aplus_copy,
                        "assets": serialized_assets,
                    }
                    st.session_state["cs_edited_listing"] = listing

                    active_marketplace = str(
                        st.session_state.get("cs_active_marketplace")
                        or (launch.get("target_marketplaces") or ["UK"])[0]
                    )
                    persisted = _save_listing_draft(
                        launch_id=launch_id,
                        marketplace=active_marketplace,
                        listing=listing,
                        rufus_optimized=st.session_state.get(
                            "cs_rufus_optimize", False
                        ),
                    )
                    if persisted:
                        record_section_save(launch_id, "creative", "listing_draft")
                    else:
                        st.warning(
                            "A+ assets were generated but could not be auto-saved to draft storage. "
                            "Use Save Draft on the listing page to persist them."
                        )

            if generated_count:
                st.success(
                    f"✅ A+ package generated. New: {generated_count}, cached reuse: {cached_count}, failed: {failed_count}."
                )
            elif cached_count:
                st.success(
                    f"✅ A+ package ready from cache reuse ({cached_count} assets, {failed_count} failed)."
                )
            else:
                st.warning("⚠️ A+ generation finished with no new assets.")
            st.rerun()

    package = st.session_state.get("cs_aplus_package")
    if package and int(package.get("launch_id") or -1) != launch_id:
        package = None
    if not package:
        st.info("Generate an A+ package to preview and download Amazon-ready assets.")
        return

    copy = package.get("copy", {})
    assets = package.get("assets", {})

    with st.expander("📄 A+ Copy", expanded=True):
        st.json(copy)

    if assets:
        st.markdown("**A+ Image Assets**")
        for asset_key, spec in APLUS_IMAGE_SPECS.items():
            asset = assets.get(asset_key)
            if not asset:
                st.warning(f"⚠️ {asset_key}: generation missing")
                continue
            st.caption(f"{asset_key}: {spec['width']}x{spec['height']} px")
            st.image(asset["bytes"], use_container_width=True)

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "a_plus_copy.json",
                json.dumps(copy, ensure_ascii=True, indent=2),
            )
            for asset_key, asset in assets.items():
                zf.writestr(
                    f"{asset_key}_{asset['width']}x{asset['height']}.jpg",
                    asset["bytes"],
                )

        st.download_button(
            "⬇️ Download A+ Package (ZIP)",
            data=zip_buf.getvalue(),
            file_name=f"launch_{launch_id}_a_plus_package.zip",
            mime="application/zip",
            use_container_width=True,
            on_click="ignore",
        )


# ---------------------------------------------------------------------------
# Version management
# ---------------------------------------------------------------------------
def _render_version_management(launch_id: int, marketplace: str) -> None:
    st.subheader("📚 Version History")

    versions = _load_draft_versions(launch_id, marketplace)
    if not versions:
        st.info(
            "No saved drafts yet. Generate and save a listing to create version history."
        )
        return

    st.markdown(f"**{len(versions)} saved version(s) for {marketplace}:**")

    for v in versions:
        with st.expander(
            f"v{v['version']} — {v['generated_at'].strftime('%Y-%m-%d %H:%M') if v.get('generated_at') else 'Unknown'} "
            f"({'RUFUS ✓' if v.get('rufus_optimized') else 'Standard'})",
            expanded=False,
        ):
            col_v1, col_v2 = st.columns([3, 1])
            with col_v1:
                st.markdown(f"**Title:** {v.get('title', '—')}")
                bullets = v.get("bullets")
                if bullets:
                    if isinstance(bullets, str):
                        bullets = json.loads(bullets)
                    for b in bullets[:2]:
                        st.caption(f"• {b[:80]}...")
            with col_v2:
                if st.button("♻️ Restore", key=f"cs_restore_v{v['draft_id']}"):
                    bullets = v.get("bullets", [])
                    if isinstance(bullets, str):
                        bullets = json.loads(bullets)
                    aplus = v.get("a_plus_content")
                    if isinstance(aplus, str):
                        aplus = json.loads(aplus) if aplus else None

                    restored = {
                        "title": v.get("title", ""),
                        "bullets": bullets,
                        "description": v.get("description", ""),
                        "backend_keywords": v.get("backend_keywords", ""),
                        "a_plus_content": aplus,
                        "quality_score": 75,
                        "quality_notes": ["Restored from version history"],
                        "optimization_suggestions": [],
                    }
                    _reset_listing_editor_state()
                    st.session_state["cs_generated_listing"] = restored
                    st.session_state["cs_edited_listing"] = restored
                    _hydrate_aplus_package_from_listing_content(launch_id, aplus)
                    st.success(f"✅ Restored v{v['version']}!")
                    st.rerun()

                if st.button("🗑️ Delete", key=f"cs_delete_v{v['draft_id']}"):
                    try:
                        with _open_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "DELETE FROM launchpad.listing_drafts WHERE draft_id = %s",
                                    (v["draft_id"],),
                                )
                            conn.commit()
                        st.success(f"✅ Deleted v{v['version']}")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"❌ Delete failed: {exc}")

    # Side-by-side comparison
    if len(versions) >= 2:
        st.markdown("**Compare Versions:**")
        col_c1, col_c2 = st.columns(2)
        v_options = {f"v{v['version']}": v for v in versions}
        with col_c1:
            v1_key = st.selectbox(
                "Version A", list(v_options.keys()), key="cs_compare_v1_sel"
            )
        with col_c2:
            v2_key = st.selectbox(
                "Version B",
                list(v_options.keys()),
                index=min(1, len(v_options) - 1),
                key="cs_compare_v2_sel",
            )

        if st.button("🔍 Compare Side-by-Side"):
            v1 = v_options[v1_key]
            v2 = v_options[v2_key]
            col_left, col_right = st.columns(2)
            with col_left:
                st.markdown(f"**{v1_key}**")
                st.markdown(f"*Title:* {v1.get('title', '—')}")
            with col_right:
                st.markdown(f"**{v2_key}**")
                st.markdown(f"*Title:* {v2.get('title', '—')}")


# ---------------------------------------------------------------------------
# Marketplace tabs
# ---------------------------------------------------------------------------
def _render_marketplace_tabs(launch: dict[str, Any]) -> str:
    """Render marketplace tabs and return selected marketplace."""
    target_mps = launch.get("target_marketplaces") or TARGET_MARKETPLACES
    tabs = st.tabs([f"🌍 {mp}" for mp in target_mps])
    selected_mp = st.session_state.get("cs_active_marketplace", target_mps[0])

    for i, (tab, mp) in enumerate(zip(tabs, target_mps)):
        with tab:
            if mp != selected_mp:
                if st.button(f"Switch to {mp}", key=f"cs_switch_mp_{mp}"):
                    st.session_state["cs_active_marketplace"] = mp
                    st.rerun()
            else:
                st.caption(f"Active marketplace: **{mp}**")

    return selected_mp


# ---------------------------------------------------------------------------
# Push to Amazon (SP-API Listings Items)
# ---------------------------------------------------------------------------


def _get_launchpad_dsn() -> str:
    """Return LAUNCHPAD_DB_DSN from the environment."""
    return resolve_dsn("LAUNCHPAD_DB_DSN", "MARKET_INTEL_DSN", "PG_DSN")


def _resolve_seller_id_cached() -> str | None:
    """Return cached seller_id from session state, reading AMAZON_SELLER_ID env var."""
    if st.session_state.get("cs_seller_id"):
        return st.session_state["cs_seller_id"]
    try:
        seller_id = _sp_api.get_seller_id()
        st.session_state["cs_seller_id"] = seller_id
        return seller_id
    except Exception as exc:
        logger.warning("Could not resolve seller_id: %s", exc)
        return None


def _render_push_to_amazon(
    launch: dict[str, Any],
    marketplace: str,
    listing: dict[str, Any] | None,
) -> None:
    """Render the Push to Amazon panel below the Save Draft section."""

    st.subheader("🚀 Push to Amazon")

    # Marketplace-scoped session state keys — prevents a cached SKU/validation
    # from one marketplace bleeding into another marketplace's push panel.
    mp_key = marketplace.upper()
    _sku_key = f"cs_push_sku_{mp_key}"
    _pt_key = f"cs_push_product_type_{mp_key}"
    _val_key = f"cs_push_validation_result_{mp_key}"

    if not listing:
        st.info("Generate and save a listing draft before pushing to Amazon.")
        return

    asin = str(launch.get("source_asin") or "").strip().upper()
    if not asin:
        st.warning("No ASIN associated with this launch — cannot push.")
        return

    # ----------------------------------------------------------------
    # Resolve seller ID (env var AMAZON_SELLER_ID)
    # ----------------------------------------------------------------
    seller_id = _resolve_seller_id_cached()
    if not seller_id:
        st.error(
            "**AMAZON_SELLER_ID** is not set. "
            "Add it to your `.env` file: `AMAZON_SELLER_ID=<your seller ID>`"
        )
        return

    # ----------------------------------------------------------------
    # product_type from asin_snapshots; SKU from registry or entered manually
    # ----------------------------------------------------------------
    auto_meta = None
    try:
        auto_meta = _sp_api.lookup_product_type(_get_launchpad_dsn(), asin, marketplace)
    except Exception as exc:
        logger.warning("product_type lookup failed: %s", exc)

    if auto_meta and not st.session_state.get(_pt_key):
        st.session_state[_pt_key] = auto_meta.product_type

    if auto_meta:
        st.caption(f"Product type from snapshot: **{auto_meta.product_type}**")
    else:
        st.info(
            f"No snapshot found for **{asin}** / **{marketplace}**. "
            "Enter the product type manually."
        )

    with st.expander("SKU & Product Type", expanded=True):
        sku_col, pt_col = st.columns(2)
        with sku_col:
            known_skus = _get_skus_for_asin_mp(asin, marketplace)
            _new_sku_sentinel = "— Enter new SKU —"
            sku_options = known_skus + [_new_sku_sentinel]

            # If there are no known SKUs, skip the selectbox and go straight to text_input
            if known_skus:
                # Default selection: prefer previously used session value, else first known
                current_sku_val = st.session_state.get(_sku_key, "")
                if current_sku_val and current_sku_val in known_skus:
                    default_idx = sku_options.index(current_sku_val)
                else:
                    default_idx = 0

                selected_option = st.selectbox(
                    "SKU",
                    options=sku_options,
                    index=default_idx,
                    key=f"cs_push_sku_select_{mp_key}",
                    help="Select a previously used SKU or choose '— Enter new SKU —' to add one.",
                )
                if selected_option == _new_sku_sentinel:
                    new_sku = st.text_input(
                        "New SKU",
                        value="",
                        key=f"cs_push_sku_new_{mp_key}",
                        placeholder="e.g. MYPRODUCT-DE-001",
                        help="Enter the exact Amazon seller SKU for this ASIN.",
                    ).strip()
                    st.session_state[_sku_key] = new_sku
                else:
                    st.session_state[_sku_key] = selected_option
            else:
                st.session_state[_sku_key] = (
                    st.text_input(
                        "SKU",
                        value=st.session_state.get(_sku_key, ""),
                        key=f"cs_push_sku_input_{mp_key}",
                        placeholder="e.g. MYPRODUCT-DE-001",
                        help="Your Amazon seller SKU for this ASIN. Saved for future use.",
                    )
                    or ""
                ).strip()

        with pt_col:
            st.session_state[_pt_key] = st.text_input(
                "Product Type",
                value=st.session_state.get(_pt_key, ""),
                key=f"cs_push_product_type_input_{mp_key}",
                placeholder="e.g. PET_FOOD",
                help="Amazon product type. Auto-filled from snapshot if available.",
            )

    sku = st.session_state.get(_sku_key, "").strip()
    product_type = st.session_state.get(_pt_key, "").strip()

    if not sku or not product_type:
        st.warning("Enter a valid SKU and product type to enable push.")
        return

    # ----------------------------------------------------------------
    # Content summary
    # ----------------------------------------------------------------
    title = listing.get("title", "")
    bullets = listing.get("bullets") or []
    description = listing.get("description", "")
    keywords = listing.get("backend_keywords", "")

    with st.expander("Content to push", expanded=False):
        st.markdown(f"**Title** ({len(title)} chars): {title}")
        st.markdown(
            f"**Bullets**: {len([b for b in bullets if str(b).strip()])} bullet(s)"
        )
        for i, b in enumerate(bullets, 1):
            if str(b).strip():
                st.markdown(f"  {i}. {b}")
        st.markdown(f"**Description** ({len(description)} chars)")
        kw_space = " ".join(keywords.replace(",", " ").split())
        st.markdown(f"**Backend keywords** ({len(kw_space.encode())} bytes)")

    # ----------------------------------------------------------------
    # Client-side validation
    # ----------------------------------------------------------------
    client_check = _sp_api.validate_content(
        title, bullets or None, description or None, keywords or None
    )
    if not client_check.ok:
        st.error("Fix these issues before pushing:")
        for err in client_check.errors:
            st.markdown(f"- {err}")
        return

    # ----------------------------------------------------------------
    # Validate Only (dry run)
    # ----------------------------------------------------------------
    val_col, push_col = st.columns(2)

    last_validation: _sp_api.PushResult | None = st.session_state.get(_val_key)

    with val_col:
        if st.button(
            "🔍 Validate with Amazon",
            use_container_width=True,
            key=f"cs_btn_validate_{mp_key}",
        ):
            with st.spinner("Running validation preview..."):
                result = _sp_api.push_listing_content(
                    seller_id=seller_id,
                    sku=sku,
                    marketplace=marketplace,
                    product_type=product_type,
                    title=title or None,
                    bullets=bullets or None,
                    description=description or None,
                    keywords=keywords or None,
                    preview=True,
                )
            st.session_state[_val_key] = result
            last_validation = result
            # Save this SKU for future use as soon as the user runs a validation
            if sku:
                _register_sku(asin, marketplace, sku)
            st.rerun()

    # ----------------------------------------------------------------
    # Display last validation result
    # ----------------------------------------------------------------
    if last_validation is not None:
        if last_validation.status in ("VALID", "ACCEPTED"):
            st.success("Validation passed — Amazon accepted the content (dry run).")
        elif last_validation.status == "INVALID":
            st.error("Validation failed — Amazon rejected the content.")
        elif last_validation.status == "ERROR":
            st.error(f"Validation error: {last_validation.error_message}")
        else:
            st.warning(f"Unexpected validation status: {last_validation.status}")

        if last_validation.errors:
            with st.expander(
                f"{len(last_validation.errors)} error(s) from Amazon", expanded=True
            ):
                for issue in last_validation.errors:
                    st.markdown(
                        f"- **[{issue.get('severity', '?')}]** "
                        f"`{issue.get('attributeName', '?')}`: {issue.get('message', '')}"
                    )
        if last_validation.warnings:
            with st.expander(
                f"{len(last_validation.warnings)} warning(s) from Amazon",
                expanded=False,
            ):
                for issue in last_validation.warnings:
                    st.markdown(
                        f"- **[{issue.get('severity', '?')}]** "
                        f"`{issue.get('attributeName', '?')}`: {issue.get('message', '')}"
                    )

    # ----------------------------------------------------------------
    # Live push — only offered after a successful validation
    # ----------------------------------------------------------------
    validation_ok = (
        last_validation is not None
        and last_validation.status in ("VALID", "ACCEPTED")
        and not last_validation.errors
    )

    with push_col:
        push_disabled = not validation_ok
        push_btn = st.button(
            "📤 Push Live to Amazon",
            use_container_width=True,
            key=f"cs_btn_push_live_{mp_key}",
            disabled=push_disabled,
            type="primary",
        )

    if (
        push_disabled
        and last_validation is not None
        and last_validation.status not in ("VALID", "ACCEPTED")
    ):
        st.caption("Run a successful validation before pushing live.")

    if push_btn and validation_ok:
        st.session_state[f"cs_push_confirm_pending_{mp_key}"] = True

    if st.session_state.get(f"cs_push_confirm_pending_{mp_key}"):
        with st.expander("⚠️ Confirm Live Push", expanded=True):
            st.warning(
                f"You are about to **push changes live to Amazon** for "
                f"ASIN **{asin}** / SKU **{sku}** on **{marketplace}**. "
                "This will update the live listing immediately."
            )
            confirm = st.checkbox(
                "I understand this will update the live Amazon listing.",
                key=f"cs_push_confirm_checkbox_{mp_key}",
            )
            col_confirm, col_cancel = st.columns([1, 1])
            with col_cancel:
                if st.button(
                    "Cancel",
                    key=f"cs_btn_push_cancel_{mp_key}",
                    use_container_width=True,
                ):
                    st.session_state[f"cs_push_confirm_pending_{mp_key}"] = False
                    st.rerun()
            with col_confirm:
                if st.button(
                    "✅ Confirm Push",
                    key=f"cs_btn_push_confirm_{mp_key}",
                    type="primary",
                    disabled=not confirm,
                    use_container_width=True,
                ):
                    st.session_state[f"cs_push_confirm_pending_{mp_key}"] = False
                    with st.spinner("Pushing to Amazon..."):
                        live_result = _sp_api.push_listing_content(
                            seller_id=seller_id,
                            sku=sku,
                            marketplace=marketplace,
                            product_type=product_type,
                            title=title or None,
                            bullets=bullets or None,
                            description=description or None,
                            keywords=keywords or None,
                            preview=False,
                        )
                    if live_result.status in ("VALID", "ACCEPTED"):
                        st.success(
                            f"Listing pushed successfully! "
                            f"Submission ID: `{live_result.submission_id}`"
                        )
                        _register_sku(asin, marketplace, sku)
                        st.session_state[_val_key] = None
                    elif (
                        live_result.status == "ERROR"
                        and "429" in live_result.error_message
                    ):
                        st.error(live_result.error_message)
                        st.info(
                            "Amazon has rate-limited this request. Wait and try again."
                        )
                    else:
                        st.error(
                            f"Push failed (status={live_result.status}): "
                            f"{live_result.error_message or 'See issues below.'}"
                        )
                        if live_result.errors:
                            for issue in live_result.errors:
                                st.markdown(
                                    f"- **{issue.get('attributeName', '?')}**: {issue.get('message', '')}"
                                )


# ---------------------------------------------------------------------------
# Final review & export
# ---------------------------------------------------------------------------
def _render_final_review(
    launch: dict[str, Any], listing: dict[str, Any] | None
) -> None:
    st.subheader("🔍 Final Review & Export")

    launch_id = launch["launch_id"]
    gallery = st.session_state.get("cs_image_gallery", {})
    db_gallery = _load_image_gallery(launch_id)
    total_slots = len(DEFAULT_SLOT_TYPES)

    filled_slots = sum(
        1
        for s in range(1, total_slots + 1)
        if _slot_has_image(gallery.get(s)) or _slot_has_image(db_gallery.get(s))
    )

    # Validation checklist
    st.markdown("**Launch Readiness Checklist:**")
    checks = {
        "✅ Listing generated" if listing else "❌ Listing not generated": bool(
            listing
        ),
        f"{'✅' if filled_slots == total_slots else '⚠️'} Images: {filled_slots}/{total_slots} slots filled": filled_slots
        == total_slots,
        "✅ Title within 200 chars"
        if listing and len(listing.get("title", "")) <= 200
        else "❌ Title too long": listing and len(listing.get("title", "")) <= 200,
        "✅ All 5 bullets present"
        if listing and len(listing.get("bullets", [])) >= 5
        else "❌ Missing bullets": listing and len(listing.get("bullets", [])) >= 5,
    }

    for label, passed in checks.items():
        if passed:
            st.success(label)
        else:
            st.warning(label)

    all_ready = all(checks.values())

    # Finalize button
    st.divider()
    if st.button(
        "🚀 Finalize Launch (Mark as Stage 5 — Launch Ready)",
        type="primary",
        disabled=not all_ready,
        use_container_width=True,
    ):
        if not all_ready:
            st.error("❌ Complete all checklist items before finalizing.")
        else:
            try:
                with _open_conn() as conn:
                    mgr = LaunchStateManager()
                    advanced = mgr.advance_stage(conn, launch_id, validate=False)
                    if advanced:
                        conn.commit()
                        st.success(
                            "🎉 Launch finalized! Status: **Launch Ready** (Stage 5)"
                        )
                        st.balloons()
                        _load_launches()
                        st.rerun()
                    else:
                        st.warning(
                            "⚠️ Could not advance stage. Launch may already be complete."
                        )
            except Exception as exc:
                st.error(f"❌ Failed to finalize launch: {exc}")

    # Export options
    st.divider()
    st.markdown("**Export Options:**")
    col_e1, col_e2, col_e3 = st.columns(3)

    csv_payload: str | None = None
    if listing:
        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf)
        writer.writerow(["Field", "Value"])
        writer.writerow(["Title", listing.get("title", "")])
        for i, b in enumerate(listing.get("bullets", []), 1):
            writer.writerow([f"Bullet {i}", b])
        writer.writerow(["Description", listing.get("description", "")])
        writer.writerow(["Backend Keywords", listing.get("backend_keywords", "")])
        csv_payload = csv_buf.getvalue()

    gallery_sources: dict[int, dict[str, Any]] = {}
    for slot_num in range(1, total_slots + 1):
        if gallery.get(slot_num):
            gallery_sources[slot_num] = dict(gallery.get(slot_num) or {})
        if db_gallery.get(slot_num):
            merged = gallery_sources.get(slot_num, {})
            merged.update(
                {k: v for k, v in (db_gallery.get(slot_num) or {}).items() if v}
            )
            gallery_sources[slot_num] = merged

    gallery_images: dict[int, bytes] = {}
    for slot_num, data in gallery_sources.items():
        maybe_image = data.get("image_bytes")
        if isinstance(maybe_image, memoryview):
            gallery_images[slot_num] = maybe_image.tobytes()
        elif isinstance(maybe_image, bytes) and maybe_image:
            gallery_images[slot_num] = maybe_image

    images_zip_payload: bytes | None = None
    if gallery_images:
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for slot_num, img_bytes in gallery_images.items():
                slot_name = (
                    _get_slot_info(
                        slot_num,
                        launch_id,
                        launch.get("product_description") or "",
                    )["name"]
                    .replace(" ", "_")
                    .lower()
                )
                zf.writestr(f"slot_{slot_num}_{slot_name}.jpg", img_bytes)
        images_zip_payload = zip_buf.getvalue()

    report_lines = [
        f"# Amazon Launch Report - Launch #{launch_id}",
        f"ASIN: {launch['source_asin']}",
        f"Category: {launch.get('product_category', '-')}",
        f"Pursuit Score: {launch.get('pursuit_score', '-')} ({launch.get('pursuit_category', '-')})",
        f"Images: {filled_slots}/{len(DEFAULT_SLOT_TYPES)} slots filled",
        "",
    ]
    if listing:
        report_lines += [
            "## Listing",
            f"Title: {listing.get('title', '')}",
            "",
            "### Bullets",
        ]
        for b in listing.get("bullets", []):
            report_lines.append(f"- {b}")
        report_lines += [
            "",
            "### Backend Keywords",
            listing.get("backend_keywords", ""),
        ]
    report_text = "\n".join(report_lines)

    with col_e1:
        st.download_button(
            "📄 Download Listing CSV",
            data=csv_payload or "",
            file_name=f"listing_{launch_id}.csv",
            mime="text/csv",
            use_container_width=True,
            disabled=not bool(csv_payload),
            on_click="ignore",
        )

    with col_e2:
        st.download_button(
            "🖼️ Download Images ZIP",
            data=images_zip_payload or b"",
            file_name=f"images_{launch_id}.zip",
            mime="application/zip",
            use_container_width=True,
            disabled=not bool(images_zip_payload),
            on_click="ignore",
        )

    with col_e3:
        st.download_button(
            "📊 Download Launch Report",
            data=report_text,
            file_name=f"launch_report_{launch_id}.md",
            mime="text/markdown",
            use_container_width=True,
            on_click="ignore",
        )


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(
        page_title="Module 4: Creative Studio",
        page_icon="🎨",
        layout="wide",
    )

    _init_session_state()
    _render_header()

    # --- Launch selector ---
    selected_launch = _render_launch_selector()
    if selected_launch is None:
        st.stop()

    try:
        with _open_conn() as conn:
            render_readiness_panel(conn, int(selected_launch["launch_id"]), "Creative")
    except Exception:
        pass

    # --- Non-blocking stage readiness notice ---
    _show_stage_readiness_notice(selected_launch)

    _reset_launch_scoped_state(selected_launch)
    _render_launch_info(selected_launch)
    _hydrate_saved_creative_state(selected_launch)
    _prefill_listing_inputs(selected_launch)

    # Debug: Show BST keyword pool status
    bst_pool = st.session_state.get("cs_bst_keyword_pool", [])
    if bst_pool:
        st.success(f"✅ BST keyword pool loaded: {len(bst_pool)} keywords")
    else:
        st.error(
            "❌ BST keyword pool is EMPTY - no keywords available for backend generation"
        )

    st.divider()

    launch_id = selected_launch["launch_id"]

    # --- Marketplace tabs ---
    st.subheader("🌍 Target Marketplace")
    target_mps = selected_launch.get("target_marketplaces") or TARGET_MARKETPLACES
    mp_tabs = st.tabs([f"🌍 {mp}" for mp in target_mps])

    # Use first tab as primary for listing generation
    active_marketplace = target_mps[0]

    with mp_tabs[0]:
        st.caption(f"Primary marketplace: **{active_marketplace}**")

        # --- Listing inputs ---
        _render_listing_inputs(selected_launch)

        # --- Generate button ---
        st.divider()
        gen_col, _ = st.columns([1, 3])
        with gen_col:
            if st.button(
                "⚡ Generate Listing", type="primary", use_container_width=True
            ):
                product_name = st.session_state.get("cs_product_name", "")
                if not product_name:
                    st.error("❌ Please enter a Product Name.")
                else:
                    with st.spinner("🤖 Generating optimized listing with Gemini..."):
                        # Build BST pool: golden three words + PPC/JS long-tail
                        _gt = st.session_state.get("cs_golden_three") or {}
                        _bst_pool = [
                            w
                            for w in [
                                str(_gt.get("anchor_keyword") or "").strip(),
                                str(_gt.get("scaler_keyword") or "").strip(),
                                str(_gt.get("specialist_keyword") or "").strip(),
                            ]
                            if w
                        ] + list(st.session_state.get("cs_bst_keyword_pool") or [])
                        listing, policy_report = _generate_listing(
                            product_name=product_name,
                            key_features=st.session_state.get("cs_key_features", ""),
                            target_keywords=st.session_state.get(
                                "cs_target_keywords", ""
                            ),
                            brand_voice=st.session_state.get(
                                "cs_brand_voice", "Professional"
                            ),
                            rufus_optimize=st.session_state.get(
                                "cs_rufus_optimize", False
                            ),
                            marketplace=active_marketplace,
                            extra_instruction=st.session_state.get(
                                "cs_listing_extra_instruction", ""
                            ),
                            bst_keyword_pool=_bst_pool or None,
                        )
                    if listing:
                        _reset_listing_editor_state()
                        st.session_state["cs_generated_listing"] = listing
                        st.session_state["cs_edited_listing"] = listing
                        st.session_state["cs_listing_policy_report"] = (
                            policy_report or {}
                        )
                        # Surface BST diagnostic info so empty-field issues are visible
                        bk_val = str(listing.get("backend_keywords") or "").strip()
                        bst_pool_used = _bst_pool or []
                        # Store diagnostic info in session state so it persists after rerun
                        st.session_state["cs_bst_diagnostic"] = {
                            "pool_size": len(bst_pool_used),
                            "pool_terms": bst_pool_used[:20],
                            "backend_bytes": len(bk_val.encode("utf-8")),
                            "backend_value": bk_val or "(EMPTY - this is the problem!)",
                        }
                        st.success("✅ Listing generated!")
                        st.rerun()

        # --- Display & edit generated listing ---
        generated = st.session_state.get("cs_generated_listing")
        if generated:
            st.divider()
            # Show BST diagnostic info if available
            bst_diag = st.session_state.get("cs_bst_diagnostic")
            if bst_diag:
                with st.expander("🔍 BST Diagnostic (click to expand)", expanded=True):
                    st.write(f"**BST pool size:** {bst_diag['pool_size']} terms")
                    if bst_diag["pool_terms"]:
                        st.caption(
                            f"Sample terms: {', '.join(bst_diag['pool_terms'][:10])}"
                        )
                    st.write(f"**Backend keywords:** {bst_diag['backend_bytes']} bytes")
                    st.code(bst_diag["backend_value"], language=None)
            edited = _render_listing_display(generated)
            st.session_state["cs_edited_listing"] = edited

            # Save draft button
            save_col, _ = st.columns([1, 3])
            with save_col:
                if st.button("💾 Save Draft", use_container_width=True):
                    listing_for_save = dict(edited)
                    if st.session_state.get("cs_enforce_listing_policy", True):
                        listing_for_save, policy_report = (
                            _normalize_listing_with_policy(
                                listing_for_save, active_marketplace
                            )
                        )
                        st.session_state["cs_listing_policy_report"] = policy_report
                        st.session_state["cs_generated_listing"] = listing_for_save
                        st.session_state["cs_edited_listing"] = listing_for_save
                    success = _save_listing_draft(
                        launch_id=launch_id,
                        marketplace=active_marketplace,
                        listing=listing_for_save,
                        rufus_optimized=st.session_state.get(
                            "cs_rufus_optimize", False
                        ),
                    )
                    if success:
                        record_section_save(launch_id, "creative", "listing_draft")
                        st.success("✅ Draft saved!")
                        st.rerun()

            st.divider()
            _render_push_to_amazon(
                launch=selected_launch,
                marketplace=active_marketplace,
                listing=st.session_state.get("cs_edited_listing") or generated,
            )

    # Localized listings for other marketplaces
    for i, (tab, mp) in enumerate(zip(mp_tabs[1:], target_mps[1:]), 1):
        with tab:
            st.markdown(f"### 🌍 {mp} Localized Listing")
            st.info(
                f"Generate a localized listing for **{mp}** marketplace. "
                "The AI will adapt language, pricing references, and cultural context."
            )

            mp_gen_col, _ = st.columns([1, 3])
            with mp_gen_col:
                if st.button(
                    f"⚡ Generate {mp} Listing",
                    key=f"cs_gen_{mp}",
                    use_container_width=True,
                ):
                    product_name = st.session_state.get("cs_product_name", "")
                    if not product_name:
                        st.error("❌ Set Product Name in the UK tab first.")
                    else:
                        with st.spinner(f"🤖 Generating {mp} listing..."):
                            _gt = st.session_state.get("cs_golden_three") or {}
                            _bst_pool = [
                                w
                                for w in [
                                    str(_gt.get("anchor_keyword") or "").strip(),
                                    str(_gt.get("scaler_keyword") or "").strip(),
                                    str(_gt.get("specialist_keyword") or "").strip(),
                                ]
                                if w
                            ] + list(st.session_state.get("cs_bst_keyword_pool") or [])
                            mp_listing, policy_report = _generate_listing(
                                product_name=product_name,
                                key_features=st.session_state.get(
                                    "cs_key_features", ""
                                ),
                                target_keywords=st.session_state.get(
                                    "cs_target_keywords", ""
                                ),
                                brand_voice=st.session_state.get(
                                    "cs_brand_voice", "Professional"
                                ),
                                rufus_optimize=st.session_state.get(
                                    "cs_rufus_optimize", False
                                ),
                                marketplace=mp,
                                extra_instruction=st.session_state.get(
                                    "cs_listing_extra_instruction", ""
                                ),
                                bst_keyword_pool=_bst_pool or None,
                            )
                        if mp_listing:
                            st.session_state[f"cs_listing_{mp}"] = mp_listing
                            st.session_state["cs_listing_policy_report"] = (
                                policy_report or {}
                            )
                            st.success(f"✅ {mp} listing generated!")
                            st.rerun()

            mp_listing = st.session_state.get(f"cs_listing_{mp}")
            if mp_listing:
                edited_mp = _render_listing_display(mp_listing)
                save_mp_col, _ = st.columns([1, 3])
                with save_mp_col:
                    if st.button(
                        f"💾 Save {mp} Draft",
                        key=f"cs_save_{mp}",
                        use_container_width=True,
                    ):
                        listing_for_save = dict(edited_mp)
                        if st.session_state.get("cs_enforce_listing_policy", True):
                            listing_for_save, policy_report = (
                                _normalize_listing_with_policy(listing_for_save, mp)
                            )
                            st.session_state["cs_listing_policy_report"] = policy_report
                            st.session_state[f"cs_listing_{mp}"] = listing_for_save
                        success = _save_listing_draft(
                            launch_id=launch_id,
                            marketplace=mp,
                            listing=listing_for_save,
                            rufus_optimized=st.session_state.get(
                                "cs_rufus_optimize", False
                            ),
                        )
                        if success:
                            record_section_save(launch_id, "creative", "listing_draft")
                            st.success(f"✅ {mp} draft saved!")

    st.divider()

    st.subheader("🧭 Creative Workspaces")
    st.caption(
        "Image generation and A+ package generation are now in dedicated pages for faster loads."
    )
    nav_col1, nav_col2 = st.columns(2)
    with nav_col1:
        st.page_link(
            "pages/5_Creative_Images.py",
            label="🖼️ Open Creative Images",
            use_container_width=True,
        )
    with nav_col2:
        st.page_link(
            "pages/6_Aplus_Studio.py",
            label="🧩 Open A+ Content Studio",
            use_container_width=True,
        )

    current_listing = st.session_state.get("cs_edited_listing") or st.session_state.get(
        "cs_generated_listing"
    )

    st.divider()

    # --- Version management ---
    _render_version_management(launch_id, active_marketplace)

    st.divider()

    # --- Final review ---
    _render_final_review(selected_launch, current_listing)


if __name__ == "__main__":
    main()
