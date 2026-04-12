from __future__ import annotations
# pyright: basic

import argparse
import importlib
import importlib.util
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_module_from_path(module_name: str, file_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


try:
    from services.db_connection import connect, resolve_dsn
except Exception:
    db_mod = _load_module_from_path("db_connection", ROOT / "services" / "db_connection.py")
    connect = db_mod.connect
    resolve_dsn = db_mod.resolve_dsn


scorer_mod = _load_module_from_path("opportunity_scorer", ROOT / "services" / "opportunity_scorer.py")
OpportunityScorer = scorer_mod.OpportunityScorer


def load_env_file() -> None:
    try:
        dotenv_mod = importlib.import_module("dotenv")
    except Exception:
        return
    load_dotenv = getattr(dotenv_mod, "load_dotenv", None)
    if callable(load_dotenv):
        load_dotenv()


DEFAULT_ASIN = "B07D95W1FF"
DEFAULT_MARKETPLACE = "GB"
DEFAULT_STAGE = 2


@dataclass
class SeedSnapshot:
    asin: str
    source_marketplace: str
    launch_marketplace: str
    niche_id: int
    niche_name: str
    title: str | None
    category: str | None
    competitor_count: int
    avg_review_count: float
    avg_rating: float
    review_velocity_30d: float
    focal_avg_weekly_units: float
    focal_weeks: int
    pursuit_score: float
    pursuit_category: str


def normalize_marketplace_for_launch(value: str) -> str:
    code = value.strip().upper()
    if code == "GB":
        return "UK"
    return code


def fetch_seed_snapshot(conn: Any, asin: str, marketplace: str) -> SeedSnapshot:
    source_marketplace = marketplace.strip().upper()
    launch_marketplace = normalize_marketplace_for_launch(source_marketplace)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT niche_id, asin, title, category
            FROM market_intel.niche_competitors
            WHERE asin = %s
            LIMIT 1
            """,
            (asin,),
        )
        base = cur.fetchone()

    if base is None:
        raise ValueError(
            f"ASIN {asin} was not found in market_intel.niche_competitors."
        )

    niche_id = int(base[0])
    title = base[2]
    category = base[3]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT niche_name
            FROM market_intel.niche_definitions
            WHERE niche_id = %s
            ORDER BY CASE WHEN marketplace = %s THEN 0 ELSE 1 END, niche_id
            LIMIT 1
            """,
            (niche_id, source_marketplace),
        )
        niche_row = cur.fetchone()
    niche_name = str(niche_row[0]) if niche_row else f"Niche {niche_id}"

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT asin, reviews, rating
            FROM market_intel.niche_competitors
            WHERE niche_id = %s
            """,
            (niche_id,),
        )
        competitor_rows = cur.fetchall()

    competitor_count = len(competitor_rows)
    review_values = [float(r[1]) for r in competitor_rows if r[1] is not None]
    rating_values = [float(r[2]) for r in competitor_rows if r[2] is not None]
    avg_review_count = statistics.mean(review_values) if review_values else 0.0
    avg_rating = statistics.mean(rating_values) if rating_values else 0.0

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT competitor_asin, AVG(estimated_weekly_units)::float, COUNT(*)::int
            FROM market_intel.competitor_sales_weekly
            WHERE niche_id = %s
              AND marketplace = %s
            GROUP BY competitor_asin
            """,
            (niche_id, source_marketplace),
        )
        sales_rows = cur.fetchall()

    if not sales_rows:
        raise ValueError(
            f"No competitor_sales_weekly data found for niche_id={niche_id}, "
            f"marketplace={source_marketplace}."
        )

    weekly_units = [float(r[1]) for r in sales_rows if r[1] is not None]
    avg_weekly_units = statistics.mean(weekly_units) if weekly_units else 0.0

    focal = [r for r in sales_rows if str(r[0]).upper() == asin.upper()]
    if not focal:
        raise ValueError(
            f"No competitor_sales_weekly rows found for ASIN {asin} in {source_marketplace}."
        )
    focal_avg_weekly_units = float(focal[0][1] or 0.0)
    focal_weeks = int(focal[0][2])

    review_velocity_30d = avg_weekly_units * 4.0
    sales_velocity_score = min(100.0, (focal_avg_weekly_units * 4.0 / 10_000.0) * 100.0)
    keyword_difficulty = min(100.0, (competitor_count / 50.0) * 100.0)

    scorer = OpportunityScorer()
    pursuit_score, pursuit_category = scorer.calculate_pursuit_score(
        competitor_count=competitor_count,
        avg_review_count=avg_review_count,
        review_velocity_30d=review_velocity_30d,
        avg_rating=avg_rating,
        sales_velocity_score=sales_velocity_score,
        keyword_difficulty=keyword_difficulty,
    )

    return SeedSnapshot(
        asin=asin,
        source_marketplace=source_marketplace,
        launch_marketplace=launch_marketplace,
        niche_id=niche_id,
        niche_name=niche_name,
        title=title,
        category=category,
        competitor_count=competitor_count,
        avg_review_count=avg_review_count,
        avg_rating=avg_rating,
        review_velocity_30d=review_velocity_30d,
        focal_avg_weekly_units=focal_avg_weekly_units,
        focal_weeks=focal_weeks,
        pursuit_score=float(pursuit_score),
        pursuit_category=str(pursuit_category),
    )


def moat_strength_from_reviews(avg_review_count: float) -> str:
    if avg_review_count < 100.0:
        return "Weak"
    if avg_review_count < 1000.0:
        return "Medium"
    return "Strong"


def find_existing_launch_id(conn: Any, asin: str, source_marketplace: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT launch_id
            FROM launchpad.product_launches
            WHERE source_asin = %s
              AND source_marketplace = %s
            ORDER BY launch_id DESC
            LIMIT 1
            """,
            (asin, source_marketplace),
        )
        row = cur.fetchone()
    return int(row[0]) if row else None


def _count_rows_for_launch_ids(conn: Any, table: str, launch_ids: list[int]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM {table} WHERE launch_id = ANY(%s)",
            (launch_ids,),
        )
        return int(cur.fetchone()[0])


def clear_existing_seed_data(
    conn: Any,
    asin: str,
    source_marketplace: str,
    dry_run: bool,
) -> tuple[dict[str, int], bool, str | None]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT launch_id
            FROM launchpad.product_launches
            WHERE source_asin = %s
              AND source_marketplace = %s
            """,
            (asin, source_marketplace),
        )
        launch_ids = [int(r[0]) for r in cur.fetchall()]

    if not launch_ids:
        empty_counts = {
            "launches": 0,
            "niche_mapping": 0,
            "review_moat_analysis": 0,
            "launch_compliance_checklist": 0,
            "pricing_analysis": 0,
            "ppc_simulation": 0,
            "risk_assessment": 0,
            "listing_drafts": 0,
            "image_gallery": 0,
            "api_call_ledger": 0,
        }
        return empty_counts, True, None

    child_tables = [
        "launchpad.niche_mapping",
        "launchpad.review_moat_analysis",
        "launchpad.launch_compliance_checklist",
        "launchpad.pricing_analysis",
        "launchpad.ppc_simulation",
        "launchpad.risk_assessment",
        "launchpad.listing_drafts",
        "launchpad.image_gallery",
        "launchpad.api_call_ledger",
    ]

    counts: dict[str, int] = {}
    for table in child_tables:
        key = table.split(".", 1)[1]
        counts[key] = _count_rows_for_launch_ids(conn, table, launch_ids)

    counts["launches"] = len(launch_ids)

    if dry_run:
        return counts, True, None

    try:
        with conn.cursor() as cur:
            for table in child_tables:
                cur.execute(f"DELETE FROM {table} WHERE launch_id = ANY(%s)", (launch_ids,))
            cur.execute(
                "DELETE FROM launchpad.product_launches WHERE launch_id = ANY(%s)",
                (launch_ids,),
            )
    except Exception as exc:
        conn.rollback()
        return counts, False, str(exc)

    return counts, True, None


def upsert_launch_scenario(conn: Any, snapshot: SeedSnapshot) -> tuple[int, str]:
    launch_id = find_existing_launch_id(conn, snapshot.asin, snapshot.source_marketplace)
    action = "updated" if launch_id is not None else "created"

    if launch_id is None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO launchpad.product_launches (
                    source_asin,
                    source_marketplace,
                    target_marketplaces,
                    product_description,
                    product_category,
                    pursuit_score,
                    pursuit_category,
                    current_stage
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING launch_id
                """,
                (
                    snapshot.asin,
                    snapshot.source_marketplace,
                    [snapshot.launch_marketplace],
                    snapshot.title,
                    snapshot.category or snapshot.niche_name,
                    snapshot.pursuit_score,
                    snapshot.pursuit_category,
                    DEFAULT_STAGE,
                ),
            )
            launch_id = int(cur.fetchone()[0])
    else:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE launchpad.product_launches
                SET target_marketplaces = %s,
                    product_description = %s,
                    product_category = %s,
                    pursuit_score = %s,
                    pursuit_category = %s,
                    current_stage = %s,
                    updated_at = now()
                WHERE launch_id = %s
                """,
                (
                    [snapshot.launch_marketplace],
                    snapshot.title,
                    snapshot.category or snapshot.niche_name,
                    snapshot.pursuit_score,
                    snapshot.pursuit_category,
                    DEFAULT_STAGE,
                    launch_id,
                ),
            )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO launchpad.niche_mapping (launch_id, niche_id, marketplace, confidence)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (launch_id, niche_id, marketplace) DO UPDATE SET
                confidence = EXCLUDED.confidence,
                mapped_at = now()
            """,
            (launch_id, snapshot.niche_id, snapshot.launch_marketplace, 0.98),
        )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO launchpad.review_moat_analysis (
                launch_id,
                marketplace,
                competitor_count,
                avg_review_count,
                avg_rating,
                review_velocity_30d,
                moat_strength
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (launch_id, marketplace) DO UPDATE SET
                competitor_count = EXCLUDED.competitor_count,
                avg_review_count = EXCLUDED.avg_review_count,
                avg_rating = EXCLUDED.avg_rating,
                review_velocity_30d = EXCLUDED.review_velocity_30d,
                moat_strength = EXCLUDED.moat_strength,
                analyzed_at = now()
            """,
            (
                launch_id,
                snapshot.launch_marketplace,
                snapshot.competitor_count,
                snapshot.avg_review_count,
                snapshot.avg_rating,
                snapshot.review_velocity_30d,
                moat_strength_from_reviews(snapshot.avg_review_count),
            ),
        )

    return launch_id, action


def print_dry_run(snapshot: SeedSnapshot, clear_counts: dict[str, int] | None) -> None:
    print("[DRY RUN] Launchpad test ASIN seed preview")
    print(f"  ASIN                    : {snapshot.asin}")
    print(f"  Source marketplace      : {snapshot.source_marketplace}")
    print(f"  Launch marketplace      : {snapshot.launch_marketplace}")
    print(f"  Niche                   : {snapshot.niche_id} ({snapshot.niche_name})")
    print(f"  Competitors in niche    : {snapshot.competitor_count}")
    print(f"  Avg review count        : {snapshot.avg_review_count:.2f}")
    print(f"  Avg rating              : {snapshot.avg_rating:.2f}")
    print(f"  Review velocity (30d)   : {snapshot.review_velocity_30d:.2f}")
    print(f"  Focal ASIN weekly units : {snapshot.focal_avg_weekly_units:.2f}")
    print(f"  Focal ASIN weeks        : {snapshot.focal_weeks}")
    print(f"  Pursuit score/category  : {snapshot.pursuit_score:.2f} / {snapshot.pursuit_category}")
    print(f"  Launch stage            : {DEFAULT_STAGE} (Stage 1 complete)")

    if clear_counts is not None:
        print()
        print("[DRY RUN] --clear would delete existing launch rows:")
        for key in sorted(clear_counts.keys()):
            print(f"  {key:<26} {clear_counts[key]}")

    print()
    print("[DRY RUN] No database changes made.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Seed a Stage-1-complete launch scenario in launchpad tables "
            "using existing market_intel data."
        )
    )
    parser.add_argument("--asin", default=DEFAULT_ASIN, help="ASIN to seed (default: B07D95W1FF)")
    parser.add_argument(
        "--marketplace",
        default=DEFAULT_MARKETPLACE,
        help="market_intel marketplace code to use (default: GB)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete existing launchpad rows for this ASIN/source marketplace before seeding.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions and computed values without writing to launchpad tables.",
    )
    args = parser.parse_args()

    asin = args.asin.strip().upper()
    marketplace = args.marketplace.strip().upper()

    load_env_file()

    try:
        dsn = resolve_dsn("LAUNCHPAD_DB_DSN", "MARKET_INTEL_DSN", "PG_DSN")
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        conn = connect(dsn, role="launchpad_app")
    except Exception as exc:
        print(f"ERROR: Failed to connect to database: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        snapshot = fetch_seed_snapshot(conn, asin=asin, marketplace=marketplace)

        clear_counts = None
        clear_ok = True
        clear_error = None
        if args.clear:
            clear_counts, clear_ok, clear_error = clear_existing_seed_data(
                conn,
                asin=asin,
                source_marketplace=marketplace,
                dry_run=args.dry_run,
            )

        if args.dry_run:
            print_dry_run(snapshot, clear_counts)
            conn.rollback()
            return

        if args.clear:
            print("Cleared existing rows for ASIN/source marketplace:")
            safe_counts = clear_counts or {}
            for key in sorted(safe_counts):
                print(f"  {key:<26} {safe_counts[key]}")
            if not clear_ok and clear_error:
                print(f"  clear_status               skipped ({clear_error})")
            print()

        launch_id, action = upsert_launch_scenario(conn, snapshot)
        conn.commit()

        print("Seed complete.")
        print(f"  Launch ID               : {launch_id} ({action})")
        print(f"  ASIN / source market    : {snapshot.asin} / {snapshot.source_marketplace}")
        print(f"  Niche                   : {snapshot.niche_id} ({snapshot.niche_name})")
        print(f"  Launch marketplace      : {snapshot.launch_marketplace}")
        print(f"  Pursuit score/category  : {snapshot.pursuit_score:.2f} / {snapshot.pursuit_category}")
        print(f"  Current stage           : {DEFAULT_STAGE} (ready for Stage 2)")
        print(
            "  Updated tables          : launchpad.product_launches, "
            "launchpad.niche_mapping, launchpad.review_moat_analysis"
        )

    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
