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

MODULE_PATH = SERVICES_DIR / "ingredient_compliance.py"
SPEC = importlib.util.spec_from_file_location(
    "services.ingredient_compliance", MODULE_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module at {MODULE_PATH}")
_module = importlib.util.module_from_spec(SPEC)
sys.modules["services.ingredient_compliance"] = _module
SPEC.loader.exec_module(_module)

evaluate_screening = _module.evaluate_screening
overall_status = _module.overall_status
parse_ingredient_line = _module.parse_ingredient_line


def test_parse_ingredient_line_with_concentration() -> None:
    parsed = parse_ingredient_line("Salicylic Acid 2% w/w")
    assert parsed is not None
    assert parsed["name"] == "salicylic acid"
    assert parsed["submitted_value"] == 2.0
    assert parsed["submitted_unit"] == "% w/w"


def test_evaluate_screening_detects_exceedance_and_unknown() -> None:
    rules = {
        "EU": {
            "salicylic acid": {
                "status": "allowed_with_limit",
                "limit": 2.0,
                "unit": "% w/w",
                "source": "EU Annex",
                "canonical_name": "Salicylic Acid",
            }
        }
    }
    findings, warnings = evaluate_screening(
        raw_text="Salicylic Acid 2.5% w/w\nUnknown Ingredient",
        jurisdictions=["EU"],
        rules=rules,
    )

    outcomes = {f["Outcome"] for f in findings}
    assert "exceeds_limit" in outcomes
    assert "unknown" in outcomes
    assert warnings


def test_overall_status_priority_fail_over_manual_review() -> None:
    findings = [
        {"Outcome": "unknown"},
        {"Outcome": "exceeds_limit"},
    ]
    assert overall_status(findings) == "fail"
