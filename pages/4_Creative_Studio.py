"""
Stage 4: Creative Studio

AI-powered listing generation and image gallery management.
Uses Google Gemini for text generation and Google Imagen 3 for image generation.
Manages 7-slot image gallery following Amazon best practices.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import zipfile
from typing import Any

import psycopg
import streamlit as st
from dotenv import load_dotenv
from psycopg.rows import dict_row

from services.auth_manager import get_generative_client
from services.db_connection import connect, resolve_dsn
from services.launch_state import LaunchStateManager
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
    page_title="Module 4: Creative Studio",
    page_icon="🎨",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TARGET_MARKETPLACES = ["UK", "DE", "FR", "IT", "ES"]

BRAND_VOICES = ["Professional", "Friendly", "Technical", "Luxury"]

IMAGE_SLOTS: dict[int, dict[str, str]] = {
    1: {
        "name": "Main Image",
        "type": "main_white_bg",
        "desc": "White background, product only",
        "icon": "🖼️",
    },
    2: {
        "name": "Lifestyle",
        "type": "lifestyle",
        "desc": "Product in use / lifestyle context",
        "icon": "🌟",
    },
    3: {
        "name": "Infographic",
        "type": "infographic",
        "desc": "Features & benefits callouts",
        "icon": "📊",
    },
    4: {
        "name": "Comparison",
        "type": "comparison",
        "desc": "vs. competitors / before-after",
        "icon": "⚖️",
    },
    5: {
        "name": "Dimensions",
        "type": "dimensions",
        "desc": "Size reference / measurements",
        "icon": "📐",
    },
    6: {
        "name": "Packaging",
        "type": "packaging",
        "desc": "What's in the box",
        "icon": "📦",
    },
    7: {
        "name": "In-Use",
        "type": "in_use",
        "desc": "Hands using the product",
        "icon": "🤲",
    },
}

AMAZON_LIMITS = {
    "title": 200,
    "bullet": 500,
    "description": 2000,
    "backend_keywords": 250,
}

GEMINI_MODEL = "gemini-2.0-flash"
IMAGEN_MODEL = "imagen-3.0-generate-001"

# Load environment variables
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
        "cs_include_aplus": False,
        "cs_generated_listing": None,
        "cs_edited_listing": None,
        # Image gallery: slot_number -> image data dict
        "cs_image_gallery": {},
        # Versions
        "cs_draft_versions": [],
        "cs_compare_v1": None,
        "cs_compare_v2": None,
        # RUFUS
        "cs_rufus_optimize": False,
        # Active marketplace tab
        "cs_active_marketplace": "UK",
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
def _load_launches() -> list[dict[str, Any]]:
    try:
        with _open_conn() as conn:
            mgr = LaunchStateManager()
            launches = mgr.list_launches(conn, limit=100)
            st.session_state["cs_launches"] = launches
            return launches
    except Exception as exc:
        st.error(f"❌ Failed to load launches: {exc}")
        return []


def _render_launch_selector() -> dict[str, Any] | None:
    st.subheader("📋 Select Launch")

    launches = _load_launches()

    col_select, col_refresh = st.columns([4, 1])

    with col_select:
        if not launches:
            st.warning("No launches found. Complete Stage 1 first.")
            return None

        options = {
            f"#{l['launch_id']} — {l['source_asin']} (Stage {l['current_stage']}, {l.get('pursuit_category') or 'unscored'})": l[
                "launch_id"
            ]
            for l in launches
        }
        choice = st.selectbox(
            "Select launch", list(options.keys()), key="cs_launch_selector"
        )
        launch_id = options[choice]
        st.session_state["cs_selected_launch_id"] = launch_id
        return next((l for l in launches if l["launch_id"] == launch_id), None)

    with col_refresh:
        if st.button("🔄 Refresh", use_container_width=True):
            _load_launches()
            st.rerun()

    return None


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

            # Load PPC keywords for pre-population
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
                    kw_rows = cur.fetchall()
            if kw_rows and not st.session_state.get("cs_target_keywords"):
                st.session_state["cs_target_keywords"] = ", ".join(
                    r[0] for r in kw_rows
                )

        except Exception as exc:
            logger.warning("Could not load pricing/PPC data: %s", exc)


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
    launch_description = launch.get("product_description") or ""

    col1, col2 = st.columns(2)

    with col1:
        product_name = st.text_input(
            "Product Name / Title",
            value=st.session_state.get("cs_product_name") or launch_description[:100],
            placeholder="e.g. Premium Stainless Steel Water Bottle 32oz",
            key="cs_product_name_input",
            help="Auto-populated from Module 1 product description.",
        )
        st.session_state["cs_product_name"] = product_name

        key_features = st.text_area(
            "Key Features (one per line)",
            value=st.session_state.get("cs_key_features", ""),
            placeholder="BPA-free stainless steel\nDouble-wall vacuum insulation\nLeakproof lid\n24-hour cold / 12-hour hot",
            height=120,
            key="cs_key_features_input",
            help="Bullet points from your product analysis.",
        )
        st.session_state["cs_key_features"] = key_features

    with col2:
        target_keywords = st.text_area(
            "Target Keywords (comma-separated)",
            value=st.session_state.get("cs_target_keywords", ""),
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

        include_aplus = st.checkbox(
            "Include A+ Content modules",
            value=st.session_state.get("cs_include_aplus", False),
            key="cs_include_aplus_input",
            help="Generate structured A+ Content (Enhanced Brand Content) modules.",
        )
        st.session_state["cs_include_aplus"] = include_aplus

        rufus_optimize = st.checkbox(
            "🤖 Optimize for Amazon RUFUS AI",
            value=st.session_state.get("cs_rufus_optimize", False),
            key="cs_rufus_input",
            help="Adds natural language patterns, conversational Q&A format, and semantic keyword optimization for Amazon's AI shopping assistant.",
        )
        st.session_state["cs_rufus_optimize"] = rufus_optimize


def _build_listing_prompt(
    product_name: str,
    key_features: str,
    target_keywords: str,
    brand_voice: str,
    include_aplus: bool,
    rufus_optimize: bool,
    marketplace: str = "UK",
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

    aplus_note = ""
    if include_aplus:
        aplus_note = """
A+ CONTENT MODULES (include as JSON after the main listing):
Generate 3 A+ Content modules:
1. "hero_banner": {"headline": "...", "body": "...", "cta": "..."}
2. "feature_grid": [{"icon": "emoji", "title": "...", "desc": "..."}] (4 items)
3. "comparison_chart": {"headers": [...], "rows": [[...]]} (3 rows)
"""

    return f"""You are an expert Amazon listing copywriter specializing in {marketplace} marketplace.
Write a complete, optimized Amazon product listing in a {voice_desc} brand voice.

PRODUCT: {product_name}
KEY FEATURES:
{key_features}

TARGET KEYWORDS: {target_keywords}
{rufus_note}
{aplus_note}

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
  "backend_keywords": "Space-separated backend search terms (max 250 bytes, no repetition of title/bullet keywords)",
  "quality_score": 85,
  "quality_notes": ["Note 1", "Note 2"],
  "optimization_suggestions": ["Suggestion 1", "Suggestion 2"]
  {', "a_plus_content": {{...}}' if include_aplus else ""}
}}

RULES:
- Title: max 200 characters, primary keyword in first 80 chars
- Each bullet: max 500 characters, start with capitalized benefit phrase
- Description: HTML formatted, max 2000 characters
- Backend keywords: max 250 bytes total, space-separated, no commas
- quality_score: integer 0-100 based on keyword density, readability, compliance
- Do NOT include markdown code blocks in response, return raw JSON only
"""


def _generate_listing(
    product_name: str,
    key_features: str,
    target_keywords: str,
    brand_voice: str,
    include_aplus: bool,
    rufus_optimize: bool,
    marketplace: str = "UK",
) -> dict[str, Any] | None:
    """Call Gemini to generate listing content. Returns parsed dict or None."""
    try:
        genai = get_generative_client()
        model = genai.GenerativeModel(GEMINI_MODEL)

        prompt = _build_listing_prompt(
            product_name,
            key_features,
            target_keywords,
            brand_voice,
            include_aplus,
            rufus_optimize,
            marketplace,
        )

        response = model.generate_content(prompt)
        raw_text = response.text.strip()

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            raw_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        return json.loads(raw_text)

    except json.JSONDecodeError as exc:
        st.error(f"❌ AI returned invalid JSON: {exc}")
        logger.error("JSON parse error from Gemini: %s", exc)
        return None
    except FileNotFoundError as exc:
        st.error(f"❌ Google credentials not found: {exc}")
        return None
    except Exception as exc:
        st.error(f"❌ Listing generation failed: {exc}")
        logger.error("Listing generation error: %s", exc)
        return None


def _render_listing_display(listing: dict[str, Any]) -> dict[str, Any]:
    """Render editable listing fields with character counters. Returns edited listing."""
    st.markdown("### 📝 Generated Listing")

    edited = dict(listing)

    # Title
    title_val = st.text_area(
        "Title",
        value=listing.get("title", ""),
        height=80,
        key="cs_edit_title",
    )
    title_len = len(title_val)
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
        b_len = len(b_val)
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
    desc_len = len(desc_val)
    desc_color = "🔴" if desc_len > AMAZON_LIMITS["description"] else "🟢"
    st.caption(f"{desc_color} {desc_len} / {AMAZON_LIMITS['description']} characters")
    edited["description"] = desc_val

    # Backend keywords
    bk_val = st.text_area(
        "Backend Keywords",
        value=listing.get("backend_keywords", ""),
        height=80,
        key="cs_edit_backend_kw",
        help="Space-separated, max 250 bytes. No commas, no repetition of title/bullet keywords.",
    )
    bk_bytes = len(bk_val.encode("utf-8"))
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
                next_version = cur.fetchone()[0]

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
                        listing.get("title", ""),
                        json.dumps(listing.get("bullets", [])),
                        listing.get("description", ""),
                        listing.get("backend_keywords", ""),
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
    }
    return slot_prompts.get(
        slot["type"], f"Professional product image of {product_name}."
    )


def _generate_image_with_imagen(prompt: str) -> bytes | None:
    """Generate image using Google Imagen 3. Returns raw bytes or None."""
    try:
        genai = get_generative_client()

        # Use the imagen model via the generate_images API
        imagen = genai.ImageGenerationModel(IMAGEN_MODEL)
        result = imagen.generate_images(
            prompt=prompt,
            number_of_images=1,
            aspect_ratio="1:1",
            safety_filter_level="block_some",
            person_generation="allow_adult",
        )

        if result.images:
            return result.images[0]._image_bytes
        return None

    except AttributeError:
        # Fallback: try via generative model if ImageGenerationModel not available
        try:
            genai_module = get_generative_client()
            model = genai_module.GenerativeModel("gemini-2.0-flash-exp")
            response = model.generate_content(
                [f"Generate a product image: {prompt}"],
                generation_config={"response_mime_type": "image/png"},
            )
            if response.candidates and response.candidates[0].content.parts:
                part = response.candidates[0].content.parts[0]
                if hasattr(part, "inline_data"):
                    return base64.b64decode(part.inline_data.data)
        except Exception as inner_exc:
            logger.error("Fallback image generation failed: %s", inner_exc)
        return None
    except FileNotFoundError as exc:
        st.error(f"❌ Google credentials not found: {exc}")
        return None
    except Exception as exc:
        st.error(f"❌ Image generation failed: {exc}")
        logger.error("Image generation error: %s", exc)
        return None


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
    try:
        with _open_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO launchpad.image_gallery
                        (launch_id, slot_number, image_type, prompt_used, storage_path, model_used)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (launch_id, slot_number) DO UPDATE SET
                        image_type   = EXCLUDED.image_type,
                        prompt_used  = EXCLUDED.prompt_used,
                        storage_path = EXCLUDED.storage_path,
                        model_used   = EXCLUDED.model_used,
                        generated_at = now()
                    """,
                    (
                        launch_id,
                        slot_number,
                        image_type,
                        prompt_used,
                        storage_path,
                        model_used,
                    ),
                )
            conn.commit()
            return True
    except Exception as exc:
        logger.error("Image gallery save error: %s", exc)
        return False


def _load_image_gallery(launch_id: int) -> dict[int, dict[str, Any]]:
    """Load image gallery records for a launch. Returns dict keyed by slot_number."""
    try:
        with _open_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT slot_number, image_type, prompt_used, storage_path,
                           model_used, generated_at
                    FROM launchpad.image_gallery
                    WHERE launch_id = %s
                    ORDER BY slot_number
                    """,
                    (launch_id,),
                )
                rows = cur.fetchall()
        return {int(r["slot_number"]): dict(r) for r in rows}
    except Exception as exc:
        logger.warning("Could not load image gallery: %s", exc)
        return {}


def _render_image_gallery(launch: dict[str, Any]) -> None:
    st.subheader("🖼️ Image Gallery (7 Slots)")
    st.caption("Amazon requires 7 images for optimal listing performance.")

    launch_id = launch["launch_id"]
    launch_description = launch.get("product_description") or ""
    product_name = (
        st.session_state.get("cs_product_name") or launch_description or "Product"
    )
    product_desc = launch_description

    # Load existing gallery from DB
    db_gallery = _load_image_gallery(launch_id)

    # Merge with session state
    gallery = st.session_state.get("cs_image_gallery", {})
    for slot_num, db_data in db_gallery.items():
        if slot_num not in gallery:
            gallery[slot_num] = {"status": "db_record", **db_data}
    st.session_state["cs_image_gallery"] = gallery

    # Render in 2-column grid
    slot_items = list(IMAGE_SLOTS.items())
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
        for s in range(1, 8)
        if s in gallery and gallery[s].get("image_bytes") or s in db_gallery
    )
    st.markdown(f"**Gallery Progress:** {filled_slots} / 7 slots filled")
    if filled_slots < 7:
        st.progress(filled_slots / 7.0, text=f"{filled_slots}/7 images ready")
    else:
        st.success("✅ All 7 image slots filled!")


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

    with st.container(border=True):
        st.markdown(
            f"**{status_icon} Slot {slot_num}: {slot_info['icon']} {slot_info['name']}**"
        )
        st.caption(slot_info["desc"])

        # Show image if available
        if slot_data.get("image_bytes"):
            st.image(slot_data["image_bytes"], use_container_width=True)
        elif slot_data.get("uploaded_file"):
            st.image(slot_data["uploaded_file"], use_container_width=True)
        elif has_image:
            st.info(f"📁 Image saved: {slot_data.get('storage_path', 'DB record')}")

        # Requirements checklist
        with st.expander("📋 Requirements", expanded=False):
            reqs = _get_slot_requirements(slot_num)
            for req in reqs:
                st.markdown(f"• {req}")

        # Action buttons
        btn_col1, btn_col2 = st.columns(2)

        with btn_col1:
            if st.button(
                "🤖 Generate",
                key=f"cs_gen_img_{slot_num}",
                use_container_width=True,
                help="Generate with Google Imagen 3",
            ):
                prompt = _build_image_prompt(slot_info, product_name, product_desc)
                with st.spinner(f"Generating {slot_info['name']}..."):
                    img_bytes = _generate_image_with_imagen(prompt)

                if img_bytes:
                    gallery[slot_num] = {
                        "image_bytes": img_bytes,
                        "prompt_used": prompt,
                        "model_used": IMAGEN_MODEL,
                        "status": "generated",
                    }
                    st.session_state["cs_image_gallery"] = gallery
                    _save_image_to_gallery(
                        launch_id,
                        slot_num,
                        slot_info["type"],
                        prompt,
                        img_bytes,
                        IMAGEN_MODEL,
                    )
                    st.success(f"✅ {slot_info['name']} generated!")
                    st.rerun()
                else:
                    st.warning(
                        "⚠️ Image generation returned no result. Check credentials."
                    )

        with btn_col2:
            uploaded = st.file_uploader(
                "Upload",
                type=["jpg", "jpeg", "png", "webp"],
                key=f"cs_upload_img_{slot_num}",
                label_visibility="collapsed",
            )
            if uploaded is not None:
                img_bytes = uploaded.read()
                gallery[slot_num] = {
                    "image_bytes": img_bytes,
                    "uploaded_file": img_bytes,
                    "prompt_used": "manual_upload",
                    "model_used": "upload",
                    "status": "uploaded",
                }
                st.session_state["cs_image_gallery"] = gallery
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


def _get_slot_requirements(slot_num: int) -> list[str]:
    requirements = {
        1: [
            "Pure white background (RGB 255,255,255)",
            "Product fills 85%+ of frame",
            "No text, logos, or watermarks",
            "Min 1000x1000px, max 10,000px",
            "JPEG or PNG format",
        ],
        2: [
            "Shows product in real-world use",
            "Aspirational lifestyle setting",
            "High quality, well-lit",
            "No competitor products visible",
        ],
        3: [
            "Key features labeled with callouts",
            "Clean, readable typography",
            "Product clearly visible",
            "Benefits-focused messaging",
        ],
        4: [
            "Fair, accurate comparison",
            "No competitor brand names",
            "Clear advantage indicators",
            "Professional design",
        ],
        5: [
            "Accurate measurements shown",
            "Size reference included",
            "All dimensions labeled",
            "Technical accuracy required",
        ],
        6: [
            "All included items visible",
            "Clean flat-lay arrangement",
            "White or neutral background",
            "Every accessory shown",
        ],
        7: [
            "Shows ease of use",
            "Hands/person interacting with product",
            "Natural, authentic feel",
            "Focus on user experience",
        ],
    }
    return requirements.get(slot_num, ["High quality product image"])


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
                    st.session_state["cs_generated_listing"] = restored
                    st.session_state["cs_edited_listing"] = restored
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
# Final review & export
# ---------------------------------------------------------------------------
def _render_final_review(
    launch: dict[str, Any], listing: dict[str, Any] | None
) -> None:
    st.subheader("🔍 Final Review & Export")

    launch_id = launch["launch_id"]
    gallery = st.session_state.get("cs_image_gallery", {})
    db_gallery = _load_image_gallery(launch_id)

    filled_slots = sum(
        1
        for s in range(1, 8)
        if (s in gallery and gallery[s].get("image_bytes")) or s in db_gallery
    )

    # Validation checklist
    st.markdown("**Launch Readiness Checklist:**")
    checks = {
        "✅ Listing generated" if listing else "❌ Listing not generated": bool(
            listing
        ),
        f"{'✅' if filled_slots == 7 else '⚠️'} Images: {filled_slots}/7 slots filled": filled_slots
        == 7,
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

    with col_e1:
        if listing and st.button("📄 Download Listing CSV", use_container_width=True):
            csv_buf = io.StringIO()
            writer = csv.writer(csv_buf)
            writer.writerow(["Field", "Value"])
            writer.writerow(["Title", listing.get("title", "")])
            for i, b in enumerate(listing.get("bullets", []), 1):
                writer.writerow([f"Bullet {i}", b])
            writer.writerow(["Description", listing.get("description", "")])
            writer.writerow(["Backend Keywords", listing.get("backend_keywords", "")])
            st.download_button(
                "⬇️ Download CSV",
                data=csv_buf.getvalue(),
                file_name=f"listing_{launch_id}.csv",
                mime="text/csv",
            )

    with col_e2:
        gallery_images = {
            slot_num: data.get("image_bytes")
            for slot_num, data in gallery.items()
            if data.get("image_bytes")
        }
        if gallery_images and st.button(
            "🖼️ Download Images ZIP", use_container_width=True
        ):
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for slot_num, img_bytes in gallery_images.items():
                    slot_name = IMAGE_SLOTS[slot_num]["name"].replace(" ", "_").lower()
                    zf.writestr(f"slot_{slot_num}_{slot_name}.jpg", img_bytes)
            st.download_button(
                "⬇️ Download ZIP",
                data=zip_buf.getvalue(),
                file_name=f"images_{launch_id}.zip",
                mime="application/zip",
            )

    with col_e3:
        if st.button("📊 Generate Launch Report", use_container_width=True):
            report_lines = [
                f"# Amazon Launch Report — Launch #{launch_id}",
                f"ASIN: {launch['source_asin']}",
                f"Category: {launch.get('product_category', '—')}",
                f"Pursuit Score: {launch.get('pursuit_score', '—')} ({launch.get('pursuit_category', '—')})",
                f"Images: {filled_slots}/7 slots filled",
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
            st.download_button(
                "⬇️ Download Report",
                data=report_text,
                file_name=f"launch_report_{launch_id}.md",
                mime="text/markdown",
            )


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
def main() -> None:
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

    _render_launch_info(selected_launch)
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
                        listing = _generate_listing(
                            product_name=product_name,
                            key_features=st.session_state.get("cs_key_features", ""),
                            target_keywords=st.session_state.get(
                                "cs_target_keywords", ""
                            ),
                            brand_voice=st.session_state.get(
                                "cs_brand_voice", "Professional"
                            ),
                            include_aplus=st.session_state.get(
                                "cs_include_aplus", False
                            ),
                            rufus_optimize=st.session_state.get(
                                "cs_rufus_optimize", False
                            ),
                            marketplace=active_marketplace,
                        )
                    if listing:
                        st.session_state["cs_generated_listing"] = listing
                        st.session_state["cs_edited_listing"] = listing
                        st.success("✅ Listing generated!")
                        st.rerun()

        # --- Display & edit generated listing ---
        generated = st.session_state.get("cs_generated_listing")
        if generated:
            st.divider()
            edited = _render_listing_display(generated)
            st.session_state["cs_edited_listing"] = edited

            # Save draft button
            save_col, _ = st.columns([1, 3])
            with save_col:
                if st.button("💾 Save Draft", use_container_width=True):
                    success = _save_listing_draft(
                        launch_id=launch_id,
                        marketplace=active_marketplace,
                        listing=edited,
                        rufus_optimized=st.session_state.get(
                            "cs_rufus_optimize", False
                        ),
                    )
                    if success:
                        record_section_save(launch_id, "creative", "listing_draft")
                        st.success("✅ Draft saved!")
                        st.rerun()

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
                            mp_listing = _generate_listing(
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
                                include_aplus=st.session_state.get(
                                    "cs_include_aplus", False
                                ),
                                rufus_optimize=st.session_state.get(
                                    "cs_rufus_optimize", False
                                ),
                                marketplace=mp,
                            )
                        if mp_listing:
                            st.session_state[f"cs_listing_{mp}"] = mp_listing
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
                        success = _save_listing_draft(
                            launch_id=launch_id,
                            marketplace=mp,
                            listing=edited_mp,
                            rufus_optimized=st.session_state.get(
                                "cs_rufus_optimize", False
                            ),
                        )
                        if success:
                            record_section_save(launch_id, "creative", "listing_draft")
                            st.success(f"✅ {mp} draft saved!")

    st.divider()

    # --- Image gallery ---
    _render_image_gallery(selected_launch)

    st.divider()

    # --- Version management ---
    _render_version_management(launch_id, active_marketplace)

    st.divider()

    # --- Final review ---
    current_listing = st.session_state.get("cs_edited_listing") or st.session_state.get(
        "cs_generated_listing"
    )
    _render_final_review(selected_launch, current_listing)


if __name__ == "__main__":
    main()
