"""Dedicated Creative Images workspace."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import streamlit as st


@st.cache_resource(show_spinner=False)
def _load_creative_studio_module(source_mtime: float) -> ModuleType:
    source_path = Path(__file__).with_name("4_Creative_Studio.py")
    spec = importlib.util.spec_from_file_location("creative_studio_shared", source_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load Creative Studio module from {source_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    st.set_page_config(
        page_title="Creative Images",
        page_icon="🖼️",
        layout="wide",
    )

    source_path = Path(__file__).with_name("4_Creative_Studio.py")
    cs = _load_creative_studio_module(source_path.stat().st_mtime)
    cs._init_session_state()

    st.title("🖼️ Creative Images")
    st.caption("Generate, upload, and persist the 7 Amazon listing image slots.")

    selected_launch = cs._render_launch_selector()
    if selected_launch is None:
        st.stop()

    cs._show_stage_readiness_notice(selected_launch)
    cs._render_launch_info(selected_launch)
    cs._hydrate_saved_creative_state(selected_launch)

    st.divider()
    cs._render_image_gallery(selected_launch)


if __name__ == "__main__":
    main()
