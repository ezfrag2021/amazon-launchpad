"""
Amazon Launchpad — Main Streamlit entrypoint.

Serves as the home dashboard:
  - Sidebar navigation to the 4 stage pages
  - Active launches table with pursuit category colour-coding
  - New launch creation form
"""

from __future__ import annotations

import base64
import os
from datetime import datetime
from pathlib import Path

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
    page_title="Amazon Launchpad | Bodhi & Digby",
    page_icon="Logos/favicon.ico",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Environment & DB connection
# ---------------------------------------------------------------------------
load_dotenv()


LOGO_PATH = Path(__file__).parent / "Logos" / "Logo Only - No text .png"
try:
    with LOGO_PATH.open("rb") as _f:
        _logo_b64 = base64.b64encode(_f.read()).decode()
    _logo_uri = f"data:image/png;base64,{_logo_b64}"
except FileNotFoundError:
    _logo_uri = ""

LOGO_URI = _logo_uri

st.markdown(
    """
    <script>
    (function() {
        var params = new URLSearchParams(window.location.search);
        var hour = new Date().getHours();
        if (params.get('hour') !== String(hour)) {
            params.set('hour', hour);
            window.location.replace(window.location.pathname + '?' + params.toString());
        }
    })();
    </script>
    """,
    unsafe_allow_html=True,
)

try:
    hour = int(st.query_params.get("hour", -1))
except (ValueError, TypeError):
    hour = -1

if hour == -1:
    hour = datetime.now().hour

dark_mode = hour >= 20 or hour < 7

if dark_mode:
    css = """
    <style>
    #MainMenu, footer, header { visibility: hidden; }
    .stApp {
        background-color: #0b1a2e;
        color: #ffffff;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    .stApp,
    .stApp p,
    .stApp li,
    .stApp label,
    .stApp span,
    .stApp h1,
    .stApp h2,
    .stApp h3,
    .stApp h4,
    .stApp h5,
    .stApp h6,
    .stApp [data-testid="stMarkdownContainer"] p,
    .stApp [data-testid="stText"],
    .stApp [data-testid="stMetricLabel"],
    .stApp [data-testid="stMetricValue"],
    .stApp [data-testid="stMetricDelta"],
    .stApp [data-testid="stCaptionContainer"] p {
        color: #e8f1ff !important;
    }
    .stApp [data-testid="stCaptionContainer"],
    .stApp [data-testid="stCaptionContainer"] p,
    .stApp small,
    .stApp .stCaption {
        color: #d7e8ff !important;
    }
    .stApp a,
    .stApp a:visited {
        color: #8fd9ff !important;
    }
    .stApp [data-baseweb="input"] input,
    .stApp textarea,
    .stApp [data-baseweb="select"] * {
        color: #f5faff !important;
    }
    .stApp [data-baseweb="input"],
    .stApp textarea,
    .stApp [data-baseweb="select"] {
        background-color: #10233b !important;
        border-color: #2f5f8a !important;
    }
    .stApp [data-baseweb="select"] > div,
    .stApp [data-baseweb="select"] [role="combobox"],
    body [data-baseweb="popover"],
    body [role="listbox"],
    body [role="option"],
    body ul[role="listbox"],
    body li[role="option"] {
        background-color: #10233b !important;
        color: #e8f1ff !important;
        border-color: #2f5f8a !important;
    }
    body li[role="option"][aria-selected="true"],
    body li[role="option"]:hover {
        background-color: #1a3556 !important;
        color: #f5faff !important;
    }
    .stApp [data-testid="stTextInputRootElement"] > div,
    .stApp [data-testid="stTextArea"] textarea,
    .stApp [data-testid="stNumberInput"] div[data-baseweb="input"],
    .stApp [data-testid="stNumberInput"] div[data-baseweb="base-input"],
    .stApp [data-testid="stNumberInput"] div[data-baseweb="input"] > div,
    .stApp [data-testid="stNumberInput"] div[data-baseweb="base-input"] > div,
    .stApp [data-testid="stDateInput"] div[data-baseweb="input"],
    .stApp [data-testid="stTimeInput"] div[data-baseweb="input"],
    .stApp [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
    .stApp [data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
        background-color: #10233b !important;
        color: #e8f1ff !important;
        border-color: #2f5f8a !important;
    }
    .stApp [data-testid="stTextInputRootElement"] input,
    .stApp [data-testid="stNumberInput"] input,
    .stApp [data-testid="stNumberInput"] input[type="number"],
    .stApp [data-testid="stDateInput"] input,
    .stApp [data-testid="stTimeInput"] input,
    .stApp [data-testid="stTextArea"] textarea,
    .stApp [data-testid="stSelectbox"] input,
    .stApp [data-testid="stMultiSelect"] input {
        color: #f5faff !important;
        background-color: #10233b !important;
        -webkit-text-fill-color: #f5faff !important;
    }
    .stApp [data-testid="stNumberInput"] button {
        background-color: #10233b !important;
        color: #e8f1ff !important;
        border-color: #2f5f8a !important;
    }
    .stApp [data-testid="stNumberInput"] button:hover {
        background-color: #1a3556 !important;
        color: #f8fbff !important;
    }
    .stApp input:-webkit-autofill,
    .stApp input:-webkit-autofill:hover,
    .stApp input:-webkit-autofill:focus,
    .stApp textarea:-webkit-autofill,
    .stApp textarea:-webkit-autofill:hover,
    .stApp textarea:-webkit-autofill:focus {
        -webkit-text-fill-color: #f5faff !important;
        -webkit-box-shadow: 0 0 0 1000px #10233b inset !important;
        transition: background-color 9999s ease-in-out 0s;
    }
    .stApp input::placeholder,
    .stApp textarea::placeholder {
        color: #b7cbe3 !important;
        opacity: 1 !important;
    }
    .stApp [data-baseweb="tag"] {
        background-color: #1a3556 !important;
        color: #e8f1ff !important;
        border: 1px solid #2f5f8a !important;
    }
    section[data-testid="stSidebar"] {
        background-color: #081625 !important;
        border-right: 1px solid #1f4567 !important;
    }
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
    section[data-testid="stSidebar"] [data-testid="stText"],
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] span {
        color: #e8f1ff !important;
    }
    section[data-testid="stSidebar"] a,
    section[data-testid="stSidebar"] a:visited {
        color: #8fd9ff !important;
    }
    section[data-testid="stSidebar"] [data-testid="stSidebarNav"] {
        background-color: transparent !important;
    }
    section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] {
        background-color: #0f2439 !important;
        border: 1px solid #1f4567 !important;
        border-radius: 8px !important;
        margin-bottom: 0.25rem !important;
    }
    section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"]:hover {
        background-color: #1a3556 !important;
    }
    .stApp [data-testid="stTabs"] [role="tablist"] {
        border-bottom: 1px solid #2f5f8a !important;
    }
    .stApp [data-testid="stTabs"] [role="tab"] {
        color: #cfe2f7 !important;
    }
    .stApp [data-testid="stTabs"] [role="tab"][aria-selected="true"] {
        color: #f8fbff !important;
        border-bottom: 2px solid #29aae1 !important;
    }
    .stApp [data-testid="stExpander"] details {
        background-color: #0f2439 !important;
        border: 1px solid #2f5f8a !important;
        border-radius: 8px !important;
    }
    .stApp [data-testid="stExpander"] summary,
    .stApp [data-testid="stExpander"] summary * {
        color: #e8f1ff !important;
        background-color: #0f2439 !important;
    }
    .stApp [data-testid="stExpander"] summary:hover,
    .stApp [data-testid="stExpander"] summary:hover * {
        background-color: #1a3556 !important;
        color: #f8fbff !important;
    }
    section[data-testid="stSidebar"] [data-testid="stExpander"] details {
        background-color: #0d2034 !important;
        border: 1px solid #2f5f8a !important;
    }
    section[data-testid="stSidebar"] [data-testid="stExpander"] summary,
    section[data-testid="stSidebar"] [data-testid="stExpander"] summary * {
        color: #e8f1ff !important;
        background-color: #0d2034 !important;
    }
    section[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover,
    section[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover * {
        background-color: #1a3556 !important;
        color: #f8fbff !important;
    }
    .stApp [data-testid="stAlert"] {
        color: #e8f1ff !important;
        border: 1px solid #2f5f8a !important;
    }
    .stApp [data-testid="stCodeBlock"] pre,
    .stApp pre code {
        background: #0f2439 !important;
        color: #e8f1ff !important;
        border: 1px solid #2f5f8a !important;
    }
    .stApp [data-testid="stFileUploaderDropzone"] {
        background: #10233b !important;
        border: 1px dashed #2f5f8a !important;
        color: #e8f1ff !important;
    }
    div[data-testid="stDownloadButton"] > button {
        background-color: #29aae1 !important;
        color: #ffffff !important;
        border: none !important;
    }
    div[data-testid="stDownloadButton"] > button:hover {
        background-color: #1d8cb5 !important;
    }
    .stApp input:disabled,
    .stApp textarea:disabled,
    .stApp [aria-disabled="true"] {
        color: #afc3da !important;
        background: #0e2035 !important;
        opacity: 1 !important;
    }
    .stApp input:disabled,
    .stApp textarea:disabled {
        -webkit-text-fill-color: #e8f1ff !important;
    }
    .stApp [data-testid="stTextInput"] label,
    .stApp [data-testid="stTextInputRootElement"] label,
    .stApp [data-testid="stSelectbox"] label {
        color: #d7e8ff !important;
    }
    .stApp [data-testid="stTextInputRootElement"] > div:has(input:disabled),
    .stApp [data-testid="stTextInputRootElement"] > div:has(textarea:disabled) {
        background-color: #0e2035 !important;
        border-color: #3a6a97 !important;
    }
    .stApp [data-baseweb="select"] > div:hover,
    .stApp [data-baseweb="select"] [role="combobox"]:hover,
    .stApp [data-baseweb="select"] [role="button"]:hover,
    .stApp [data-baseweb="select"] [aria-expanded="true"],
    .stApp [data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover,
    .stApp [data-testid="stMultiSelect"] div[data-baseweb="select"] > div:hover {
        background-color: #1a3556 !important;
        color: #f5faff !important;
        border-color: #3a6a97 !important;
    }
    .block-container { padding-top: 0.5rem !important; }
    .header-container {
        padding: 0.75rem 2rem 1.5rem 2rem;
        text-align: center;
        border-top: 6px solid #8dc63f;
    }
    .eyebrow {
        color: #29aae1;
        text-transform: uppercase;
        letter-spacing: 0.2em;
        font-weight: 800;
        font-size: 0.9rem;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        color: #b0c4de;
        font-size: 1.1rem;
        max-width: 900px;
        margin: 0 auto 2rem auto;
        line-height: 1.6;
    }
    .status-chip {
        display: inline-block;
        margin-top: 0.25rem;
        background: rgba(41,170,225,0.18);
        border: 1px solid rgba(41,170,225,0.55);
        color: #dbeafe;
        border-radius: 999px;
        padding: 0.25rem 0.75rem;
        font-size: 0.78rem;
        letter-spacing: 0.03em;
        text-transform: uppercase;
    }
    .launch-table-wrap table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.92rem;
        border: 1px solid rgba(255,255,255,0.14);
        border-radius: 10px;
        overflow: hidden;
    }
    .launch-table-wrap thead tr { background: rgba(41,170,225,0.22); }
    .launch-table-wrap th,
    .launch-table-wrap td {
        padding: 8px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
    }
    .launch-table-wrap code {
        color: #d8f4ff;
        background: rgba(0,0,0,0.2);
        padding: 2px 6px;
        border-radius: 5px;
    }
    div[data-testid="stLinkButton"] a,
    div[data-testid="stButton"] button {
        background-color: #29aae1 !important;
        color: #ffffff !important;
        border: none !important;
        font-weight: 700 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.05em !important;
        border-radius: 8px !important;
    }
    div[data-testid="stLinkButton"] a:hover,
    div[data-testid="stButton"] button:hover {
        background-color: #1d8cb5 !important;
    }
    .footer {
        text-align: center;
        padding: 4rem 2rem 2rem 2rem;
        color: #64748b;
        font-size: 0.85rem;
        border-top: 1px solid rgba(255,255,255,0.05);
        margin-top: 4rem;
    }
    .theme-badge {
        position: fixed;
        bottom: 1rem;
        right: 1.2rem;
        font-size: 0.7rem;
        color: #cbd5e1;
        letter-spacing: 0.05em;
    }
    </style>
    """
else:
    css = """
    <style>
    #MainMenu, footer, header { visibility: hidden; }
    .stApp {
        background-color: #f4f7fa;
        color: #1a4a6b;
        font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    }
    .block-container { padding-top: 0.5rem !important; }
    .header-container {
        padding: 0.75rem 2rem 2rem 2rem;
        text-align: center;
    }
    .eyebrow {
        color: #1d5f82;
        font-weight: 600;
        font-size: 1rem;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        color: #64748b;
        font-size: 1.15rem;
        max-width: 850px;
        margin: 0 auto 2rem auto;
        line-height: 1.6;
    }
    .status-chip {
        display: inline-block;
        margin-top: 0.25rem;
        background: #e2eff7;
        border: 1px solid #9ec7de;
        color: #1d5f82;
        border-radius: 999px;
        padding: 0.25rem 0.75rem;
        font-size: 0.78rem;
        letter-spacing: 0.03em;
        text-transform: uppercase;
    }
    .launch-table-wrap table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.92rem;
        background: #ffffff;
        border: 1px solid #dbe7ef;
        border-radius: 10px;
        overflow: hidden;
    }
    .launch-table-wrap thead tr { background: #eaf2f8; }
    .launch-table-wrap th,
    .launch-table-wrap td {
        padding: 8px;
        border-bottom: 1px solid #eef3f7;
    }
    .launch-table-wrap code {
        color: #1a4a6b;
        background: #edf5fb;
        padding: 2px 6px;
        border-radius: 5px;
    }
    div[data-testid="stLinkButton"] a,
    div[data-testid="stButton"] button {
        background-color: #1a4a6b !important;
        color: #ffffff !important;
        border: none !important;
        font-weight: 600 !important;
        border-radius: 6px !important;
    }
    div[data-testid="stLinkButton"] a:hover,
    div[data-testid="stButton"] button:hover {
        background-color: #29aae1 !important;
    }
    .footer {
        text-align: center;
        padding: 5rem 2rem 3rem 2rem;
        color: #94a3b8;
        font-size: 0.9rem;
    }
    .theme-badge {
        position: fixed;
        bottom: 1rem;
        right: 1.2rem;
        font-size: 0.7rem;
        color: #334155;
        letter-spacing: 0.05em;
    }
    </style>
    """

st.markdown(css, unsafe_allow_html=True)


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


def _safe_rollback(conn_obj: psycopg.Connection | None) -> None:
    if conn_obj is None:
        return
    try:
        conn_obj.rollback()
    except Exception:
        pass


def _get_live_connection() -> psycopg.Connection | None:
    """Return a healthy DB connection, refreshing cached one if needed."""
    st.session_state["home_db_status"] = "checking"
    conn_obj = get_connection()
    if conn_obj is None:
        st.session_state["home_db_status"] = "offline"
        return None

    try:
        with conn_obj.cursor() as cur:
            cur.execute("SELECT 1")
        conn_obj.commit()
        st.session_state["home_db_status"] = "connected"
        return conn_obj
    except Exception:
        _safe_rollback(conn_obj)
        try:
            conn_obj.close()
        except Exception:
            pass

    st.session_state["home_db_status"] = "reconnecting"
    get_connection.clear()
    refreshed = get_connection()
    if refreshed is None:
        st.session_state["home_db_status"] = "offline"
        return None

    try:
        with refreshed.cursor() as cur:
            cur.execute("SELECT 1")
        refreshed.commit()
        st.session_state["home_db_status"] = "reconnected"
        return refreshed
    except Exception:
        _safe_rollback(refreshed)
        st.session_state["home_db_status"] = "offline"
        return None


conn = _get_live_connection()

# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("Amazon Launchpad 🚀")
    st.markdown("---")
    st.caption("Amazon Launchpad v0.1")
    sidebar_status = st.session_state.get("home_db_status", "unknown")
    if conn is None or sidebar_status == "offline":
        st.caption("DB: offline")
    elif sidebar_status == "reconnected":
        st.caption("DB: reconnected")
    elif sidebar_status == "reconnecting":
        st.caption("DB: reconnecting")
    else:
        st.caption("DB: connected")

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


def render_db_status_caption(conn_obj: psycopg.Connection | None) -> None:
    status = st.session_state.get("home_db_status", "unknown")
    if conn_obj is None or status == "offline":
        st.caption("DB: offline")
        return
    if status == "reconnected":
        st.caption("DB: reconnected")
        return
    if status == "reconnecting":
        st.caption("DB: reconnecting")
        return
    st.caption("DB: connected")


# ---------------------------------------------------------------------------
# Dashboard header
# ---------------------------------------------------------------------------
st.markdown(
    f"""
    <div class="header-container">
        <div class="eyebrow">Bodhi &amp; Digby Business Suite</div>
        <div class="main-title">
            <img src="{LOGO_URI}" alt="Bodhi &amp; Digby"
                 style="max-width:180px; width:100%; height:auto; display:block; margin:0 auto;"/>
        </div>
        <div class="sub-header">
            Amazon Launchpad helps teams validate, de-risk, and ship new Amazon products
            across target marketplaces without leaving one workflow.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
status_label = st.session_state.get("home_db_status", "unknown").replace("_", " ")
st.markdown(
    f"<div style='text-align:center;'><span class='status-chip'>DB: {status_label}</span></div>",
    unsafe_allow_html=True,
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
        _safe_rollback(conn)
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
                        _safe_rollback(conn)
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
        <div class='launch-table-wrap'>
        <table>
          <thead>
            <tr style='text-align:left'>
              <th>Launch ID</th>
              <th>Launch Name</th>
              <th>Source ASIN</th>
              <th>Current Stage</th>
              <th>Pursuit Category</th>
              <th>Created</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
        </div>
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
                _safe_rollback(conn)
                st.error(f"Could not load launch details: {exc}")

st.markdown(
    """
    <div class="footer">
        🔒 Secure internal tools &mdash; access managed via
        <a href="https://www.cloudflare.com/zero-trust/" target="_blank"
           style="color:inherit;">Cloudflare Zero Trust</a>.
        &nbsp;|&nbsp; Bodhi &amp; Digby Ltd &copy; 2026
    </div>
    """,
    unsafe_allow_html=True,
)

mode_label = f"{'🌙 Dark' if dark_mode else '☀️ Light'} mode · {hour:02d}:xx"
st.markdown(f'<div class="theme-badge">{mode_label}</div>', unsafe_allow_html=True)
