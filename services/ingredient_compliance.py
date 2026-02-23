"""
Ingredient-level compliance screening helpers.

Provides:
- DB-backed loading of ingredient concentration/prohibition rules
- Raw ingredient line parsing
- Rule evaluation into per-ingredient findings
"""

from __future__ import annotations

import re
from typing import Any

import psycopg
from psycopg.rows import dict_row


_FALLBACK_RULES: dict[str, dict[str, dict[str, Any]]] = {
    "EU": {
        "salicylic acid": {
            "status": "allowed_with_limit",
            "limit": 2.0,
            "unit": "% w/w",
            "condition": "Leave-on cosmetic concentration cap.",
            "source": "EU Cosmetics Annex (fallback sample)",
        },
        "phenoxyethanol": {
            "status": "allowed_with_limit",
            "limit": 1.0,
            "unit": "% w/w",
            "condition": "Preservative concentration limit.",
            "source": "EU Cosmetics Annex (fallback sample)",
        },
        "hydroquinone": {
            "status": "prohibited",
            "limit": None,
            "unit": "",
            "condition": "Prohibited in this category.",
            "source": "EU Cosmetics Annex (fallback sample)",
        },
    },
    "UK": {
        "salicylic acid": {
            "status": "allowed_with_limit",
            "limit": 2.0,
            "unit": "% w/w",
            "condition": "Leave-on cosmetic concentration cap.",
            "source": "UK Cosmetics Schedule (fallback sample)",
        },
        "phenoxyethanol": {
            "status": "allowed_with_limit",
            "limit": 1.0,
            "unit": "% w/w",
            "condition": "Preservative concentration limit.",
            "source": "UK Cosmetics Schedule (fallback sample)",
        },
        "hydroquinone": {
            "status": "restricted_conditionally",
            "limit": None,
            "unit": "",
            "condition": "Special restrictions apply by use type.",
            "source": "UK Cosmetics Schedule (fallback sample)",
        },
    },
}


def normalize_ingredient_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def parse_ingredient_line(raw_line: str) -> dict[str, Any] | None:
    cleaned = raw_line.strip()
    if not cleaned:
        return None

    pattern = (
        r"(?P<name>.*?)"
        r"(?P<value>\d+(?:\.\d+)?)"
        r"\s*(?P<unit>%\s*[wv]/[wv]|%|ppm|mg/kg)$"
    )
    match = re.search(pattern, cleaned, flags=re.IGNORECASE)
    if not match:
        return {
            "input": cleaned,
            "name": normalize_ingredient_name(cleaned),
            "submitted_value": None,
            "submitted_unit": None,
        }

    name = normalize_ingredient_name(match.group("name").strip(" :,-"))
    unit = normalize_ingredient_name(match.group("unit"))
    return {
        "input": cleaned,
        "name": name,
        "submitted_value": float(match.group("value")),
        "submitted_unit": unit,
    }


def load_rule_map(
    conn: psycopg.Connection,
    jurisdictions: list[str],
    product_category: str,
    product_subtype: str = "",
) -> tuple[dict[str, dict[str, dict[str, Any]]], bool]:
    normalized_jurisdictions = [j for j in jurisdictions if j in ("EU", "UK")]
    if not normalized_jurisdictions:
        return {}, False

    sql = """
        SELECT
            r.jurisdiction,
            g.canonical_name,
            g.normalized_name,
            g.synonyms,
            r.rule_type,
            r.max_concentration,
            r.max_unit,
            r.condition_text,
            r.source_title,
            r.source_url,
            r.source_clause,
            r.rule_version
        FROM launchpad.ingredient_compliance_rules r
        JOIN launchpad.ingredient_registry g
          ON g.ingredient_id = r.ingredient_id
        WHERE r.is_active = TRUE
          AND g.is_active = TRUE
          AND r.jurisdiction = ANY(%s)
          AND (r.product_category = 'all' OR r.product_category = %s)
          AND (r.product_subtype = '' OR LOWER(r.product_subtype) = LOWER(%s))
          AND r.effective_from <= CURRENT_DATE
          AND (r.effective_to IS NULL OR r.effective_to >= CURRENT_DATE)
        ORDER BY
          CASE WHEN r.product_category = %s THEN 0 ELSE 1 END,
          CASE WHEN r.product_subtype <> '' THEN 0 ELSE 1 END,
          r.effective_from DESC
    """

    by_jurisdiction: dict[str, dict[str, dict[str, Any]]] = {
        j: {} for j in normalized_jurisdictions
    }

    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                sql,
                (
                    normalized_jurisdictions,
                    product_category,
                    product_subtype,
                    product_category,
                ),
            )
            rows = list(cur.fetchall())
    except psycopg.Error as exc:
        sqlstate = getattr(exc, "sqlstate", "")
        if sqlstate in ("42P01", "3F000"):
            fallback = {
                j: dict(_FALLBACK_RULES.get(j, {})) for j in normalized_jurisdictions
            }
            return fallback, True
        raise

    for row in rows:
        jurisdiction = str(row.get("jurisdiction") or "")
        if jurisdiction not in by_jurisdiction:
            continue

        rule = {
            "status": _rule_type_to_status(str(row.get("rule_type") or "")),
            "limit": float(row["max_concentration"])
            if row.get("max_concentration") is not None
            else None,
            "unit": str(row.get("max_unit") or "").strip(),
            "condition": str(row.get("condition_text") or "").strip(),
            "source": str(row.get("source_title") or "").strip() or "Regulatory source",
            "source_url": str(row.get("source_url") or "").strip(),
            "source_clause": str(row.get("source_clause") or "").strip(),
            "rule_version": str(row.get("rule_version") or "").strip(),
            "canonical_name": str(row.get("canonical_name") or "").strip(),
        }

        names = [str(row.get("normalized_name") or "")]
        synonyms = row.get("synonyms") or []
        if isinstance(synonyms, list):
            names.extend(normalize_ingredient_name(str(s)) for s in synonyms if s)

        for name in names:
            normalized = normalize_ingredient_name(name)
            if not normalized:
                continue
            if normalized not in by_jurisdiction[jurisdiction]:
                by_jurisdiction[jurisdiction][normalized] = rule

    has_db_rules = any(by_jurisdiction.get(j) for j in by_jurisdiction)
    if has_db_rules:
        return by_jurisdiction, False

    fallback = {j: dict(_FALLBACK_RULES.get(j, {})) for j in normalized_jurisdictions}
    return fallback, True


def evaluate_screening(
    raw_text: str,
    jurisdictions: list[str],
    rules: dict[str, dict[str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    lines = [line for line in raw_text.splitlines() if line.strip()]
    parsed = [parse_ingredient_line(line) for line in lines]

    findings: list[dict[str, Any]] = []
    warnings: list[str] = []

    for parsed_item in parsed:
        if not parsed_item:
            continue

        ingredient_name = str(parsed_item.get("name") or "")
        for jurisdiction in jurisdictions:
            rule = rules.get(jurisdiction, {}).get(ingredient_name)
            if not rule:
                findings.append(
                    {
                        "Jurisdiction": jurisdiction,
                        "Input": parsed_item["input"],
                        "Matched": ingredient_name.title(),
                        "Submitted": "N/A",
                        "Outcome": "no_specific_rule",
                        "Limit": "N/A",
                        "Action": "No specific EU/UK restriction rule matched; verify with CAS/INCI if needed",
                        "Source": "No matching rule",
                    }
                )
                continue

            submitted_value = parsed_item.get("submitted_value")
            submitted_unit = parsed_item.get("submitted_unit")
            submitted_text = (
                f"{submitted_value:g} {submitted_unit}".strip()
                if submitted_value is not None
                else "N/A"
            )

            outcome = str(rule.get("status") or "unknown")
            limit_value = rule.get("limit")
            limit_unit = str(rule.get("unit") or "")
            limit_text = (
                f"<= {limit_value:g} {limit_unit}" if limit_value is not None else "N/A"
            )
            action = "No action required"

            if limit_value is not None:
                if submitted_value is None:
                    outcome = "missing_concentration"
                    action = "Add concentration to evaluate threshold"
                elif float(submitted_value) > float(limit_value):
                    outcome = "exceeds_limit"
                    action = f"Reduce to <= {float(limit_value):g} {limit_unit}".strip()

            if outcome == "prohibited":
                action = "Remove ingredient or reformulate"
            elif outcome == "restricted_conditionally":
                action = "Specialist review required"

            findings.append(
                {
                    "Jurisdiction": jurisdiction,
                    "Input": parsed_item["input"],
                    "Matched": str(
                        rule.get("canonical_name") or ingredient_name
                    ).title(),
                    "Submitted": submitted_text,
                    "Outcome": outcome,
                    "Limit": limit_text,
                    "Action": action,
                    "Source": str(rule.get("source") or "N/A"),
                }
            )

    return findings, sorted(set(warnings))


def overall_status(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "manual_review"

    has_fail = any(
        item.get("Outcome") in ("prohibited", "exceeds_limit") for item in findings
    )
    if has_fail:
        return "fail"

    has_manual = any(
        item.get("Outcome") in ("unknown", "missing_concentration") for item in findings
    )
    if has_manual:
        return "manual_review"

    has_no_specific_rule = any(
        item.get("Outcome") == "no_specific_rule" for item in findings
    )
    if has_no_specific_rule:
        return "conditional"

    has_conditional = any(
        item.get("Outcome") == "restricted_conditionally" for item in findings
    )
    if has_conditional:
        return "conditional"

    return "pass"


def _rule_type_to_status(rule_type: str) -> str:
    mapping = {
        "max_concentration": "allowed_with_limit",
        "prohibited": "prohibited",
        "restricted_conditionally": "restricted_conditionally",
        "allowed": "allowed",
    }
    return mapping.get(rule_type, "unknown")
