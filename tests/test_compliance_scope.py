from __future__ import annotations

# pyright: basic, reportUnknownVariableType=false, reportUnusedCallResult=false

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = ROOT / "services"

services_pkg = types.ModuleType("services")
services_pkg.__path__ = [str(SERVICES_DIR)]
sys.modules.setdefault("services", services_pkg)

PROFILE_PATH = SERVICES_DIR / "compliance_profile.py"
PROFILE_SPEC = importlib.util.spec_from_file_location(
    "services.compliance_profile", PROFILE_PATH
)
if PROFILE_SPEC is None or PROFILE_SPEC.loader is None:
    raise RuntimeError(f"Unable to load module at {PROFILE_PATH}")
_profile_module = importlib.util.module_from_spec(PROFILE_SPEC)
sys.modules["services.compliance_profile"] = _profile_module
PROFILE_SPEC.loader.exec_module(_profile_module)
ProductProfile = _profile_module.ProductProfile

ENGINE_PATH = SERVICES_DIR / "compliance_engine.py"
ENGINE_SPEC = importlib.util.spec_from_file_location("services.compliance_engine", ENGINE_PATH)
if ENGINE_SPEC is None or ENGINE_SPEC.loader is None:
    raise RuntimeError(f"Unable to load module at {ENGINE_PATH}")
_engine_module = importlib.util.module_from_spec(ENGINE_SPEC)
sys.modules["services.compliance_engine"] = _engine_module
ENGINE_SPEC.loader.exec_module(_engine_module)
ComplianceEngine = _engine_module.ComplianceEngine

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "seed_compliance_rules.py"
SPEC = importlib.util.spec_from_file_location("seed_compliance_rules_under_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module at {MODULE_PATH}")
_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(_module)
COMPLIANCE_RULES: list[dict[str, Any]] = list(_module.COMPLIANCE_RULES)


def _matched_requirement_names(matched_rules: list[dict[str, Any]]) -> set[str]:
    return {str(rule["requirement_name"]) for rule in matched_rules}


def _matched_regimes(matched_rules: list[dict[str, Any]]) -> set[str]:
    return {str(rule["regime"]) for rule in matched_rules}


def test_scope_matching_electronic_toy_includes_expected_and_excludes_irrelevant() -> None:
    engine = ComplianceEngine()

    profile = ProductProfile(
        product_category="Electronic Toy",
        product_description="Bluetooth remote-control toy with rechargeable battery",
        is_electrical=True,
        is_electronic=True,
        contains_batteries=True,
        is_radio_equipment=True,
        is_toy=True,
        is_childcare=True,
        is_dpp_category=True,
    )

    matched = engine.match_rules_for_product(
        product=profile,
        product_attributes={"product_description": profile.product_description},
        rules=COMPLIANCE_RULES,
    )
    matched_names = _matched_requirement_names(matched)
    matched_regimes = _matched_regimes(matched)

    assert {"CE", "UKCA", "WEEE", "RoHS", "ToyEN71", "DPP"}.issubset(matched_regimes)
    assert {
        "CE Marking — Toys",
        "UKCA Marking — Toys",
        "WEEE Producer Registration",
        "RoHS Compliance — Restriction of Hazardous Substances",
        "EN 71-1: Physical and Mechanical Safety",
        "EN 71-8 / EN 62115: Electrical Toy Safety",
        "DPP — Battery Passport (EU Battery Regulation 2023/1542)",
    }.issubset(matched_names)

    assert "CE Marking — Medical Devices" not in matched_names
    assert "UKCA Marking — Medical Devices" not in matched_names
    assert "CE Marking — Personal Protective Equipment (PPE)" not in matched_names
    assert "CE Marking — Machinery" not in matched_names


def test_scope_matching_manual_kitchen_tool_excludes_electronics_and_toy_regimes() -> None:
    engine = ComplianceEngine()

    profile = ProductProfile(
        product_category="Kitchen Utensil",
        product_description="Stainless-steel hand whisk for manual food prep",
        is_food_contact=True,
    )

    matched = engine.match_rules_for_product(
        product=profile,
        product_attributes={"product_description": profile.product_description},
        rules=COMPLIANCE_RULES,
    )
    matched_names = _matched_requirement_names(matched)
    matched_regimes = _matched_regimes(matched)

    assert "General Product Safety Regulation (GPSR) 2023/988" in matched_names
    assert "WEEE" not in matched_regimes
    assert "RoHS" not in matched_regimes
    assert "ToyEN71" not in matched_regimes
    assert "DPP" not in matched_regimes

    assert "CE Marking — Electronics" not in matched_names
    assert "UKCA Marking — Electronics" not in matched_names
    assert "EN 71-1: Physical and Mechanical Safety" not in matched_names
    assert "DPP — Electronics Product Passport (Ecodesign for Sustainable Products)" not in matched_names


def test_scope_matching_cosmetic_product_includes_cosmetics_rules() -> None:
    engine = ComplianceEngine()

    profile = ProductProfile(
        product_category="Hydration Gel",
        product_description="A topical application for skin hydration and moisture retention",
        is_cosmetic=True,
    )

    matched = engine.match_rules_for_product(
        product=profile,
        product_attributes={"product_description": profile.product_description},
        rules=COMPLIANCE_RULES,
    )
    matched_names = _matched_requirement_names(matched)

    assert "Cosmetics Regulation (EC) 1223/2009" in matched_names

    assert "CE Marking — Electronics" not in matched_names
    assert "UKCA Marking — Electronics" not in matched_names
    assert "EN 71-1: Physical and Mechanical Safety" not in matched_names
    assert "CE Marking — Toys" not in matched_names
