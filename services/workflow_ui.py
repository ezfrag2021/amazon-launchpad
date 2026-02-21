from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import streamlit as st

from services.launch_state import LaunchStateManager


def _save_key(launch_id: int, module_key: str, section_key: str) -> str:
    return f"save_ts:{launch_id}:{module_key}:{section_key}"


def record_section_save(launch_id: int, module_key: str, section_key: str) -> None:
    st.session_state[_save_key(launch_id, module_key, section_key)] = datetime.now(
        timezone.utc
    ).isoformat()


def render_section_save_status(
    launch_id: int, module_key: str, section_key: str
) -> None:
    raw_ts = st.session_state.get(_save_key(launch_id, module_key, section_key))
    if not raw_ts:
        st.caption("Not saved yet")
        return

    try:
        dt = datetime.fromisoformat(str(raw_ts))
        stamp = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        stamp = str(raw_ts)

    st.caption(f"Autosaved: {stamp}")


def render_readiness_panel(
    conn: psycopg.Connection,
    launch_id: int,
    current_module: str,
) -> None:
    mgr = LaunchStateManager()

    try:
        summary = mgr.get_launch_summary(conn, launch_id)
    except Exception as exc:  # noqa: BLE001
        st.sidebar.warning(f"Could not load readiness panel: {exc}")
        return

    compliance = summary.get("compliance_progress") or {}
    compliance_total = int(compliance.get("total", 0) or 0)
    compliance_pending = int(compliance.get("pending", 0) or 0)
    compliance_blocked = int(compliance.get("blocked", 0) or 0)

    module_checks = [
        ("Opportunity", summary.get("pursuit_score") is not None),
        (
            "Compliance",
            compliance_total > 0
            and compliance_pending == 0
            and compliance_blocked == 0,
        ),
        ("Risk & Pricing", bool(summary.get("has_pricing_analysis"))),
        ("Creative", bool(summary.get("has_listing_drafts"))),
    ]

    with st.sidebar.expander("Launch Readiness", expanded=True):
        st.caption(f"Launch #{launch_id}")
        st.caption(f"Current module: {current_module}")

        for label, done in module_checks:
            icon = "✅" if done else "⚪"
            st.markdown(f"{icon} {label}")

        blockers = summary.get("blockers") or []
        if blockers:
            st.caption("Open blockers")
            for blocker in blockers[:3]:
                st.markdown(f"- {blocker}")
