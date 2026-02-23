"""Shared Bodhi & Digby visual shell for Streamlit pages."""

from __future__ import annotations

import base64
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st

_LOGO_PATH = Path(__file__).resolve().parents[1] / "Logos" / "Logo Only - No text .png"


@st.cache_resource(show_spinner=False)
def _logo_uri() -> str:
    try:
        with _LOGO_PATH.open("rb") as fh:
            logo_b64 = base64.b64encode(fh.read()).decode()
        return f"data:image/png;base64,{logo_b64}"
    except FileNotFoundError:
        return ""


def _inject_hour_query_param() -> None:
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


def _resolve_hour() -> int:
    try:
        hour = int(st.query_params.get("hour", -1))
    except (ValueError, TypeError):
        hour = -1
    if hour == -1:
        hour = datetime.now().hour
    return hour


def _dark_css() -> str:
    return """
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
        font-size: 1.2rem;
        max-width: 800px;
        margin: 0 auto 3rem auto;
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
        text-decoration: none !important;
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
        letter-spacing: 0.05em;
        color: #cbd5e1;
    }
    </style>
    """


def _light_css() -> str:
    return """
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
    .eyebrow { color: #1d5f82; font-weight: 600; font-size: 1rem; margin-bottom: 0.5rem; }
    .sub-header { color: #64748b; font-size: 1.25rem; max-width: 750px; margin: 0 auto; }
    div[data-testid="stLinkButton"] a,
    div[data-testid="stButton"] button {
        background-color: #1a4a6b !important;
        color: #ffffff !important;
        border: none !important;
        font-weight: 600 !important;
        border-radius: 6px !important;
        text-decoration: none !important;
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
        letter-spacing: 0.05em;
        color: #334155;
    }
    </style>
    """


def apply_bdl_theme(
    subtitle: str, eyebrow: str = "Bodhi & Digby Business Suite"
) -> dict[str, Any]:
    """Render BDL shell (script + theme css + header)."""
    _inject_hour_query_param()
    hour = _resolve_hour()
    dark_mode = hour >= 20 or hour < 7
    st.markdown(_dark_css() if dark_mode else _light_css(), unsafe_allow_html=True)

    logo_uri = _logo_uri()
    safe_subtitle = escape(subtitle)
    safe_eyebrow = escape(eyebrow)
    if logo_uri:
        main_title_html = (
            f'<img src="{logo_uri}" alt="Bodhi &amp; Digby" '
            'style="max-width:180px; width:100%; height:auto; display:block; margin:0 auto;"/>'
        )
    else:
        main_title_html = (
            '<div style="font-weight:700; font-size:1.6rem;">Bodhi &amp; Digby</div>'
        )

    st.markdown(
        f"""
        <div class="header-container">
            <div class="eyebrow">{safe_eyebrow}</div>
            <div class="main-title">{main_title_html}</div>
            <div class="sub-header">{safe_subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return {"dark_mode": dark_mode, "hour": hour}


def render_bdl_footer(theme_state: dict[str, Any] | None) -> None:
    """Render standard BDL footer and mode badge."""
    dark_mode = bool((theme_state or {}).get("dark_mode", False))
    hour = int((theme_state or {}).get("hour", datetime.now().hour))
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
