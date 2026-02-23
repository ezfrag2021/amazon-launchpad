"""
Import ingredient registry and jurisdiction rules from CSV files.

Usage:
    python scripts/import_ingredient_compliance_data.py \
      --ingredients-csv data/ingredients.csv \
      --rules-csv data/compliance_rules.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from services.db_connection import connect, resolve_dsn
from services.ingredient_compliance import normalize_ingredient_name


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {k.strip(): (v or "").strip() for k, v in row.items()} for row in reader
        ]


def _parse_synonyms(value: str) -> list[str]:
    if not value:
        return []
    text = value.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in text.split("|") if part.strip()]


def _to_optional_float(value: str) -> float | None:
    if not value:
        return None
    return float(value)


def _to_optional_date(value: str) -> str | None:
    return value or None


def upsert_ingredients(
    conn: Any, rows: list[dict[str, str]], dry_run: bool
) -> dict[str, int]:
    upsert_sql = """
        INSERT INTO launchpad.ingredient_registry (
            canonical_name,
            normalized_name,
            cas_number,
            ec_number,
            synonyms,
            is_active,
            updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (normalized_name)
        DO UPDATE SET
            canonical_name = EXCLUDED.canonical_name,
            cas_number = EXCLUDED.cas_number,
            ec_number = EXCLUDED.ec_number,
            synonyms = EXCLUDED.synonyms,
            is_active = EXCLUDED.is_active,
            updated_at = now()
    """

    inserted = 0
    for row in rows:
        canonical_name = row.get("canonical_name", "")
        if not canonical_name:
            raise ValueError("ingredients.csv row missing canonical_name")

        normalized_name = normalize_ingredient_name(canonical_name)
        cas_number = row.get("cas_number") or None
        ec_number = row.get("ec_number") or None
        synonyms = _parse_synonyms(
            row.get("synonyms_json") or row.get("synonyms") or ""
        )
        is_active = (row.get("status") or "active").lower() != "inactive"

        if dry_run:
            inserted += 1
            continue

        with conn.cursor() as cur:
            cur.execute(
                upsert_sql,
                (
                    canonical_name,
                    normalized_name,
                    cas_number,
                    ec_number,
                    synonyms,
                    is_active,
                ),
            )
            inserted += 1

    if not dry_run:
        conn.commit()
    return {"processed": len(rows), "upserted": inserted}


def _resolve_ingredient_id(
    conn: Any, lookup_kind: str, lookup_value: str
) -> int | None:
    if lookup_kind == "cas_number":
        sql = "SELECT ingredient_id FROM launchpad.ingredient_registry WHERE cas_number = %s"
        params = (lookup_value,)
    elif lookup_kind == "normalized_name":
        sql = "SELECT ingredient_id FROM launchpad.ingredient_registry WHERE normalized_name = %s"
        params = (normalize_ingredient_name(lookup_value),)
    else:
        sql = "SELECT ingredient_id FROM launchpad.ingredient_registry WHERE normalized_name = %s"
        params = (normalize_ingredient_name(lookup_value),)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return int(row[0]) if row else None


def upsert_rules(
    conn: Any, rows: list[dict[str, str]], dry_run: bool
) -> dict[str, int]:
    upsert_sql = """
        INSERT INTO launchpad.ingredient_compliance_rules (
            ingredient_id,
            jurisdiction,
            product_category,
            product_subtype,
            rule_type,
            max_concentration,
            max_unit,
            basis,
            condition_text,
            exceptions_text,
            source_title,
            source_url,
            source_clause,
            effective_from,
            effective_to,
            rule_version,
            last_reviewed_at,
            is_active,
            updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (
            ingredient_id,
            jurisdiction,
            product_category,
            product_subtype,
            rule_type,
            effective_from
        )
        DO UPDATE SET
            max_concentration = EXCLUDED.max_concentration,
            max_unit = EXCLUDED.max_unit,
            basis = EXCLUDED.basis,
            condition_text = EXCLUDED.condition_text,
            exceptions_text = EXCLUDED.exceptions_text,
            source_title = EXCLUDED.source_title,
            source_url = EXCLUDED.source_url,
            source_clause = EXCLUDED.source_clause,
            effective_to = EXCLUDED.effective_to,
            rule_version = EXCLUDED.rule_version,
            last_reviewed_at = EXCLUDED.last_reviewed_at,
            is_active = EXCLUDED.is_active,
            updated_at = now()
    """

    processed = 0
    upserted = 0
    unresolved = 0

    for row in rows:
        processed += 1
        lookup_kind = (row.get("ingredient_lookup_kind") or "canonical_name").strip()
        lookup_value = (row.get("ingredient_lookup_value") or "").strip()
        if not lookup_value:
            raise ValueError("compliance_rules.csv row missing ingredient_lookup_value")

        ingredient_id = _resolve_ingredient_id(conn, lookup_kind, lookup_value)
        if ingredient_id is None:
            unresolved += 1
            continue

        jurisdiction = (row.get("jurisdiction") or "").upper().strip()
        product_category = row.get("product_category") or "all"
        product_subtype = row.get("product_subtype") or ""
        rule_type = row.get("rule_type") or ""
        max_concentration = _to_optional_float(row.get("max_value") or "")
        max_unit = row.get("max_unit") or None
        basis = row.get("basis") or None
        condition_text = row.get("condition_text") or None
        exceptions_text = row.get("exceptions_text") or None
        source_title = row.get("source_title") or "Regulatory source"
        source_url = row.get("source_url") or ""
        source_clause = row.get("source_clause") or None
        effective_from = _to_optional_date(row.get("effective_from") or "")
        effective_to = _to_optional_date(row.get("effective_to") or "")
        rule_version = row.get("rule_version") or "1.0"
        last_reviewed_at = _to_optional_date(row.get("last_reviewed_at") or "")
        is_active = (row.get("status") or "active").lower() != "inactive"

        if not effective_from:
            raise ValueError("compliance_rules.csv row missing effective_from")
        if not jurisdiction:
            raise ValueError("compliance_rules.csv row missing jurisdiction")
        if not rule_type:
            raise ValueError("compliance_rules.csv row missing rule_type")

        if dry_run:
            upserted += 1
            continue

        with conn.cursor() as cur:
            cur.execute(
                upsert_sql,
                (
                    ingredient_id,
                    jurisdiction,
                    product_category,
                    product_subtype,
                    rule_type,
                    max_concentration,
                    max_unit,
                    basis,
                    condition_text,
                    exceptions_text,
                    source_title,
                    source_url,
                    source_clause,
                    effective_from,
                    effective_to,
                    rule_version,
                    last_reviewed_at,
                    is_active,
                ),
            )
            upserted += 1

    if not dry_run:
        conn.commit()
    return {"processed": processed, "upserted": upserted, "unresolved": unresolved}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import ingredient compliance CSV data"
    )
    parser.add_argument("--ingredients-csv", required=True)
    parser.add_argument("--rules-csv", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ingredients_path = Path(args.ingredients_csv)
    rules_path = Path(args.rules_csv)
    if not ingredients_path.exists():
        raise FileNotFoundError(f"Missing file: {ingredients_path}")
    if not rules_path.exists():
        raise FileNotFoundError(f"Missing file: {rules_path}")

    load_dotenv()
    dsn = resolve_dsn("LAUNCHPAD_DB_DSN", "MARKET_INTEL_DSN", "PG_DSN")

    ingredients_rows = _read_csv(ingredients_path)
    rules_rows = _read_csv(rules_path)

    conn = connect(dsn, role="launchpad_app")
    try:
        ingredient_stats = upsert_ingredients(conn, ingredients_rows, args.dry_run)
        rule_stats = upsert_rules(conn, rules_rows, args.dry_run)

        mode = "DRY RUN" if args.dry_run else "IMPORT"
        print(f"[{mode}] Ingredient rows processed: {ingredient_stats['processed']}")
        print(f"[{mode}] Ingredient rows upserted: {ingredient_stats['upserted']}")
        print(f"[{mode}] Rule rows processed: {rule_stats['processed']}")
        print(f"[{mode}] Rule rows upserted: {rule_stats['upserted']}")
        print(
            f"[{mode}] Rule rows unresolved ingredient lookup: {rule_stats['unresolved']}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
