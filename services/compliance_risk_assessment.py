"""
AI-powered compliance risk assessment using Google Gemini.

Analyses product attributes against selected regulatory regimes and provides
risk identification with mitigation recommendations referencing specific regimes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.0-flash"


def assess_compliance_risks(
    product_category: str,
    intended_use: str,
    materials: str,
    selected_regimes: list[str],
    key_requirements: list[str],
    ingredient_context: dict[str, Any] | None = None,
    gmp_assured: bool = False,
) -> dict[str, Any] | None:
    """Generate an AI-powered compliance risk assessment.

    Uses Google Gemini to analyse product attributes against selected regulatory
    regimes and produce structured risk/mitigation output.

    Args:
        product_category: Product category string.
        intended_use: How the product is intended to be used.
        materials: Key materials and components.
        selected_regimes: Regime codes (CE, UKCA, etc.).
        key_requirements: Requirement names from selected regimes.
        ingredient_context: Ingredient screening summary/context.
        gmp_assured: Whether manufacturing is assumed GMP-controlled.

    Returns:
        Dict with structure::

            summary: str
            overall_risk_level: "low"|"medium"|"high"|"critical"
            risks: list of {risk_name, severity, description, regime_references, mitigations}
            recommended_priority_actions: list[str]

        Returns None on failure.
    """
    try:
        from services.auth_manager import get_generative_client
    except ImportError:
        logger.warning(
            "auth_manager not available for risk assessment; using heuristic fallback"
        )
        return _build_fallback_assessment(
            product_category=product_category,
            intended_use=intended_use,
            materials=materials,
            selected_regimes=selected_regimes,
            key_requirements=key_requirements,
            ingredient_context=ingredient_context,
            gmp_assured=gmp_assured,
            reason="Gemini auth module unavailable",
        )

    prompt = _build_risk_prompt(
        product_category,
        intended_use,
        materials,
        selected_regimes,
        key_requirements,
        ingredient_context,
        gmp_assured,
    )

    raw = ""
    try:
        genai = get_generative_client()
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        raw = response.text.strip()

        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise json.JSONDecodeError("Top-level payload is not an object", raw, 0)
        return _apply_contextual_adjustments(parsed, ingredient_context, gmp_assured)
    except json.JSONDecodeError as exc:
        logger.error("Risk assessment JSON parse error: %s\nRaw: %s", exc, raw[:500])
        return _build_fallback_assessment(
            product_category=product_category,
            intended_use=intended_use,
            materials=materials,
            selected_regimes=selected_regimes,
            key_requirements=key_requirements,
            ingredient_context=ingredient_context,
            gmp_assured=gmp_assured,
            reason="Gemini returned non-JSON payload",
        )
    except FileNotFoundError as exc:
        logger.warning("Google credentials not found: %s", exc)
        return _build_fallback_assessment(
            product_category=product_category,
            intended_use=intended_use,
            materials=materials,
            selected_regimes=selected_regimes,
            key_requirements=key_requirements,
            ingredient_context=ingredient_context,
            gmp_assured=gmp_assured,
            reason="Gemini credentials missing",
        )
    except ImportError as exc:
        logger.warning("Gemini dependency unavailable: %s", exc)
        return _build_fallback_assessment(
            product_category=product_category,
            intended_use=intended_use,
            materials=materials,
            selected_regimes=selected_regimes,
            key_requirements=key_requirements,
            ingredient_context=ingredient_context,
            gmp_assured=gmp_assured,
            reason="Gemini dependency missing",
        )
    except Exception as exc:
        logger.error("Risk assessment generation error: %s", exc)
        return _build_fallback_assessment(
            product_category=product_category,
            intended_use=intended_use,
            materials=materials,
            selected_regimes=selected_regimes,
            key_requirements=key_requirements,
            ingredient_context=ingredient_context,
            gmp_assured=gmp_assured,
            reason="Gemini request failed",
        )


def _build_fallback_assessment(
    product_category: str,
    intended_use: str,
    materials: str,
    selected_regimes: list[str],
    key_requirements: list[str],
    ingredient_context: dict[str, Any] | None,
    gmp_assured: bool,
    reason: str,
) -> dict[str, Any]:
    """Return deterministic, non-AI risk assessment for offline testing."""
    regimes = [r for r in selected_regimes if r]
    regime_set = set(regimes)
    material_text = (materials or "").lower()
    use_text = (intended_use or "").lower()
    category_text = (product_category or "").lower()

    risks: list[dict[str, Any]] = []

    if "CE" in regime_set:
        risks.append(
            {
                "risk_name": "Incomplete CE technical file",
                "severity": "high",
                "description": "Missing conformity evidence (test reports, risk assessment, DoC) can block EU listing readiness.",
                "regime_references": ["CE"],
                "mitigations": [
                    "Compile a technical file with applicable harmonized standards and test evidence.",
                    "Issue and version-control an EU Declaration of Conformity before launch.",
                ],
            }
        )

    if "UKCA" in regime_set:
        risks.append(
            {
                "risk_name": "UK Responsible Person and UKCA marking gaps",
                "severity": "medium",
                "description": "Products sold in Great Britain require UKCA alignment and accountable UK market contacts.",
                "regime_references": ["UKCA"],
                "mitigations": [
                    "Confirm UK Responsible Person details and include GB postal address on packaging.",
                    "Prepare UK Declaration of Conformity aligned to applicable product regulations.",
                ],
            }
        )

    if "WEEE" in regime_set:
        risks.append(
            {
                "risk_name": "WEEE producer obligations not operationalized",
                "severity": "medium",
                "description": "Electrical/electronic products require compliant take-back and producer registration workflows.",
                "regime_references": ["WEEE"],
                "mitigations": [
                    "Register as producer (or via authorized representative) in target markets.",
                    "Add crossed-out wheelie bin symbol and disposal instructions to packaging.",
                ],
            }
        )

    if "RoHS" in regime_set:
        risks.append(
            {
                "risk_name": "Restricted substance evidence insufficient",
                "severity": "high",
                "description": "Supplier declarations alone may be inadequate without material-level compliance evidence.",
                "regime_references": ["RoHS"],
                "mitigations": [
                    "Collect declarations and test evidence for homogeneous materials from key suppliers.",
                    "Implement incoming QC checks for high-risk components (solder, cables, plastics).",
                ],
            }
        )

    if "ToyEN71" in regime_set:
        risks.append(
            {
                "risk_name": "Toy safety labeling and testing mismatch",
                "severity": "high",
                "description": "Age grading, warning text, and EN 71 test scope can be inconsistent across listing and packaging.",
                "regime_references": [
                    "ToyEN71",
                    "CE" if "CE" in regime_set else "ToyEN71",
                ],
                "mitigations": [
                    "Map hazards to EN 71 parts and validate warnings for the intended age group.",
                    "Align product detail page claims with tested use-case and packaging warnings.",
                ],
            }
        )

    if "DPP" in regime_set:
        risks.append(
            {
                "risk_name": "DPP data readiness risk",
                "severity": "medium",
                "description": "Digital Product Passport requirements can expose traceability and data quality gaps before 2026 enforcement.",
                "regime_references": ["DPP"],
                "mitigations": [
                    "Define product data schema for materials, recyclability, and lifecycle attributes.",
                    "Assign ownership for maintaining passport data and QR-linked records.",
                ],
            }
        )

    if any(token in material_text for token in ("lithium", "battery", "rechargeable")):
        risks.append(
            {
                "risk_name": "Battery transport and safety controls",
                "severity": "critical"
                if "toy" in category_text or "children" in use_text
                else "high",
                "description": "Battery-powered products have elevated safety and logistics risks if test and handling controls are incomplete.",
                "regime_references": [
                    r for r in ("CE", "RoHS", "WEEE", "ToyEN71") if r in regime_set
                ],
                "mitigations": [
                    "Validate battery safety documentation and transport classification before shipment.",
                    "Implement supplier lot traceability and incoming inspection for battery assemblies.",
                ],
            }
        )

    if not risks:
        risks.append(
            {
                "risk_name": "Regime scope ambiguity",
                "severity": "medium",
                "description": "Selected regimes or product attributes are too sparse to derive precise compliance risks.",
                "regime_references": regimes,
                "mitigations": [
                    "Provide clearer intended use, user age, and component details.",
                    "Confirm applicable regimes with legal/compliance counsel before launch.",
                ],
            }
        )

    ingredient_context = ingredient_context or {}
    ingredient_overall = str(ingredient_context.get("overall") or "").lower()
    if gmp_assured and ingredient_overall in ("pass", "conditional"):
        risks.append(
            {
                "risk_name": "Ingredient controls require periodic refresh",
                "severity": "low",
                "description": "Ingredient screen passed under current GMP assumption; maintain periodic evidence updates as formulas or suppliers change.",
                "regime_references": [r for r in ("EU", "UK") if r in regime_set]
                or regimes,
                "mitigations": [
                    "Re-run ingredient check on formulation changes and keep supplier CoA/SDS records.",
                    "Schedule recurring compliance review of concentration-limited substances.",
                ],
            }
        )

    overall = _derive_overall_risk(risks)

    requirements_sample = (
        ", ".join(key_requirements[:3])
        if key_requirements
        else "selected regime requirements"
    )
    summary = (
        f"Heuristic risk assessment generated because {reason}. "
        f"For category '{product_category or 'Unspecified'}', primary risk concentration is '{overall}'. "
        f"Assessment references {requirements_sample} and should be treated as pre-check guidance until Gemini-backed review is available."
    )

    actions = [
        "Prioritize mitigation tasks for all HIGH/CRITICAL risks before moving to Stage 3.",
        "Collect objective evidence for each selected regime (test reports, declarations, labeling proofs).",
        "Run Gemini assessment later to validate this heuristic result against richer context.",
    ]

    return {
        "summary": summary,
        "overall_risk_level": overall,
        "risks": risks,
        "recommended_priority_actions": actions,
    }


def _build_risk_prompt(
    product_category: str,
    intended_use: str,
    materials: str,
    selected_regimes: list[str],
    key_requirements: list[str],
    ingredient_context: dict[str, Any] | None,
    gmp_assured: bool,
) -> str:

    regimes_str = ", ".join(selected_regimes) if selected_regimes else "None specified"
    reqs_str = (
        "\n".join(f"- {r}" for r in key_requirements[:20])
        if key_requirements
        else "None specified"
    )
    ingredient_context = ingredient_context or {}
    ingredient_overall = str(ingredient_context.get("overall") or "not_run")
    ingredient_counts = ingredient_context.get("counts") or {}
    ingredient_lines = [
        f"- overall: {ingredient_overall}",
        f"- prohibited_or_exceeds: {int(ingredient_counts.get('prohibited_or_exceeds', 0) or 0)}",
        f"- missing_concentration: {int(ingredient_counts.get('missing_concentration', 0) or 0)}",
        f"- restricted_conditionally: {int(ingredient_counts.get('restricted_conditionally', 0) or 0)}",
        f"- no_specific_rule_or_unknown: {int(ingredient_counts.get('no_specific_or_unknown', 0) or 0)}",
    ]
    ingredient_examples = ingredient_context.get("examples") or []
    if isinstance(ingredient_examples, list):
        for item in ingredient_examples[:8]:
            ingredient_lines.append(f"- {str(item)}")
    ingredient_block = "\n".join(ingredient_lines)
    gmp_text = "Yes" if gmp_assured else "No / Unknown"

    return f"""You are an expert EU/UK product compliance consultant. Analyse the following product \
and identify compliance risks with specific mitigations referencing the applicable regulatory regimes.

PRODUCT CATEGORY: {product_category}
INTENDED USE: {intended_use or "Not specified"}
KEY MATERIALS/COMPONENTS: {materials or "Not specified"}
APPLICABLE REGIMES: {regimes_str}
KEY REQUIREMENTS:
{reqs_str}

ASSUMED GMP-CONTROLLED MANUFACTURING FACILITY: {gmp_text}

INGREDIENT SCREENING CONTEXT (from internal checker):
{ingredient_block}

OUTPUT FORMAT (respond with valid JSON only, no markdown code blocks):
{{
  "summary": "One-paragraph overall risk assessment summary",
  "overall_risk_level": "low|medium|high|critical",
  "risks": [
    {{
      "risk_name": "Short name of the risk",
      "severity": "low|medium|high|critical",
      "description": "Detailed description of the compliance risk",
      "regime_references": ["CE", "RoHS"],
      "mitigations": [
        "Specific actionable mitigation step referencing the relevant regime"
      ]
    }}
  ],
  "recommended_priority_actions": [
    "Top 3-5 priority actions the seller should take immediately"
  ]
}}

RULES:
- Identify 3-8 specific compliance risks based on the product type and materials.
- Each risk MUST reference at least one applicable regime from the list.
- Mitigations must be actionable and reference specific regulatory requirements.
- Severity levels: low (minor documentation gap), medium (testing/certification needed), \
high (potential market access risk), critical (safety/legal liability).
- Focus on real-world risks: chemical restrictions, safety testing, marking requirements, \
documentation gaps, DPP readiness, and supply chain compliance.
- Use ingredient screening context as a primary signal for ingredient/formulation risk.
- If ingredient screening has no prohibited/exceeds findings and GMP assumption is Yes, do NOT assign high/critical risk solely for generic GMP or generic ingredient uncertainty.
- If missing concentration exists for concentration-limited ingredients, include that as at least medium risk.
- Do NOT include markdown code blocks in response, return raw JSON only.
"""


def _derive_overall_risk(risks: list[dict[str, Any]]) -> str:
    severity_rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    return (
        max(
            risks,
            key=lambda r: severity_rank.get(str(r.get("severity") or "low"), 1),
        ).get("severity", "medium")
        if risks
        else "medium"
    )


def _cap_severity(current: str, cap: str) -> str:
    order = ["low", "medium", "high", "critical"]
    current_norm = current if current in order else "medium"
    cap_norm = cap if cap in order else "critical"
    return order[min(order.index(current_norm), order.index(cap_norm))]


def _apply_contextual_adjustments(
    assessment: dict[str, Any],
    ingredient_context: dict[str, Any] | None,
    gmp_assured: bool,
) -> dict[str, Any]:
    context = ingredient_context or {}
    counts = context.get("counts") or {}
    has_hard_ingredient_fail = int(counts.get("prohibited_or_exceeds", 0) or 0) > 0
    has_missing_conc = int(counts.get("missing_concentration", 0) or 0) > 0

    risks = assessment.get("risks")
    if not isinstance(risks, list):
        risks = []

    for risk in risks:
        if not isinstance(risk, dict):
            continue
        name_desc = (
            f"{str(risk.get('risk_name') or '')} {str(risk.get('description') or '')}"
        ).lower()
        severity = str(risk.get("severity") or "medium").lower()

        is_gmp_theme = ("gmp" in name_desc) or ("good manufacturing" in name_desc)
        is_ingredient_theme = any(
            token in name_desc
            for token in (
                "ingredient",
                "formulation",
                "concentration",
                "prohibited",
                "annex",
                "inci",
            )
        )

        if gmp_assured and is_gmp_theme:
            risk["severity"] = _cap_severity(severity, "low")

        if is_ingredient_theme and gmp_assured and not has_hard_ingredient_fail:
            cap = "medium" if has_missing_conc else "low"
            risk["severity"] = _cap_severity(
                str(risk.get("severity") or "medium").lower(), cap
            )

    assessment["risks"] = risks
    assessment["overall_risk_level"] = _derive_overall_risk(risks)
    return assessment
