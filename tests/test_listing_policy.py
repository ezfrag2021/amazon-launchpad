from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = ROOT / "services"

services_pkg = types.ModuleType("services")
services_pkg.__path__ = [str(SERVICES_DIR)]
sys.modules.setdefault("services", services_pkg)

MODULE_PATH = SERVICES_DIR / "listing_policy.py"
SPEC = importlib.util.spec_from_file_location("services.listing_policy", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module at {MODULE_PATH}")
_module = importlib.util.module_from_spec(SPEC)
sys.modules["services.listing_policy"] = _module
SPEC.loader.exec_module(_module)

DEFAULT_GLOBAL_PROHIBITED_LISTING_TERMS = (
    _module.DEFAULT_GLOBAL_PROHIBITED_LISTING_TERMS
)
effective_blocked_phrases = _module.effective_blocked_phrases
normalize_listing_with_policy = _module.normalize_listing_with_policy


def test_normalize_listing_enforces_limits_and_strips_terms() -> None:
    listing = {
        "title": "A" * 260 + " best seller",
        "bullets": ["FDA approved and clinically proven"] * 6,
        "description": "doctor recommended " + ("x" * 2100),
        "backend_keywords": "nike apple bottle bottle insulated stainless",
    }
    limits = {"title": 200, "bullet": 500, "description": 2000, "backend_keywords": 250}

    normalized, report = normalize_listing_with_policy(
        listing=listing,
        marketplace="UK",
        amazon_limits=limits,
        enforce_policy=True,
        blocked_phrases=DEFAULT_GLOBAL_PROHIBITED_LISTING_TERMS
        + [
            "doctor recommended",
            "clinically proven",
        ],
    )

    assert len(normalized["title"]) <= 200
    assert len(normalized["bullets"]) == 5
    assert all(len(b) <= 500 for b in normalized["bullets"])
    assert len(normalized["description"]) <= 2000
    assert "best seller" not in normalized["title"].lower()
    assert "doctor recommended" not in normalized["description"].lower()
    assert "nike" not in normalized["backend_keywords"].lower()
    assert report["truncated_fields"]
    assert report["removed_phrases"]


def test_effective_blocked_phrases_merges_dedupes() -> None:
    phrases = effective_blocked_phrases(
        marketplace="UK",
        policy_terms={"global": ["alpha", "beta"], "eu_uk": ["beta", "gamma"]},
        additional_terms_raw="Gamma\nDelta\n",
    )
    lowered = [p.lower() for p in phrases]
    assert "alpha" in lowered
    assert "beta" in lowered
    assert "gamma" in lowered
    assert "delta" in lowered
    assert lowered.count("gamma") == 1
