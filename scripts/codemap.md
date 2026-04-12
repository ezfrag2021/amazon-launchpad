# scripts/

## Responsibility
Deployment and database administration utilities. Provides two standalone scripts: one for seeding reference data into the `launchpad` schema and one for validating that the `launchpad_app` role has correct permissions across all tables and cross-schema reads. These scripts are run manually during deployment setup and post-migration verification — they are not invoked by the application at runtime.

## Design

### seed_compliance_rules.py
CLI script (Python/psycopg) that populates `launchpad.compliance_rules` with a hardcoded dataset of 20 regulatory compliance rules. Key design decisions:

- **Idempotent inserts**: Uses `INSERT ... ON CONFLICT (regime, requirement_name) DO NOTHING`. Before inserting, `_ensure_unique_constraint()` checks for and creates the `uq_compliance_rules_regime_name` constraint if absent — making the script safe to re-run.
- **`--clear` flag**: Deletes all rows from `compliance_rules` before seeding. Cascades to `launch_compliance_checklist` via FK (requires `ON DELETE CASCADE` on that table).
- **`--dry-run` flag**: Prints the rule list with counts without touching the database.
- **DSN resolution**: Calls `services.db_connection.resolve_dsn()` with fallback env vars `LAUNCHPAD_DB_DSN → MARKET_INTEL_DSN → PG_DSN`. Connects with `role="launchpad_app"`.
- **Seed data structure**: Each rule is a dict with fields: `regime`, `category_pattern` (regex), `requirement_name`, `requirement_description`, `documentation_required` (list), `is_2026_dpp_relevant` (bool), `effective_date`, `source_url`.

**Regimes covered** (20 rules total):
| Regime | Rules | Notes |
|--------|-------|-------|
| CE | 7 | Electronics, Toys, Machinery, Medical Devices, PPE, REACH, GPSR |
| UKCA | 4 | Electronics, Toys, Machinery, Medical Devices |
| WEEE | 2 | EU producer registration, UK producer registration |
| RoHS | 2 | EU directive, UK regulations |
| ToyEN71 | 4 | EN 71-1 (physical), EN 71-2 (flammability), EN 71-3 (chemical), EN 62115 (electrical) |
| DPP | 4 | Battery Passport, Electronics Passport, Textiles Passport, Furniture Passport |

DPP (Digital Product Passport) rules are flagged `is_2026_dpp_relevant=True` and have effective dates of 2026–2027.

### validate_launchpad_access.sql
psql script that runs a structured test suite against the live database to verify `launchpad_app` role permissions. Uses temp table `_test_results` and helper functions `_pass()` / `_fail()` to record outcomes, then prints a summary. All DML test operations use `SAVEPOINT` / `ROLLBACK TO SAVEPOINT` — no permanent changes are made.

**Test sections:**
| Section | What it checks |
|---------|---------------|
| 1 — Schema existence | `launchpad` schema exists and is owned by `launchpad_admin` |
| 2 — Current role | Connected user is `launchpad_app` |
| 3 — Table access (11 tables) | SELECT/INSERT/UPDATE on `product_launches`, `niche_mapping`, `review_moat_analysis`, `compliance_rules` (SELECT-only), `launch_compliance_checklist`, `pricing_analysis`, `ppc_simulation`, `risk_assessment`, `listing_drafts`, `image_gallery`, `api_call_ledger`; SELECT+UPDATE-only on `budget_config` (INSERT/DELETE must be blocked) |
| 4 — Cross-schema reads | SELECT on 8 `market_intel` tables/views; INSERT into `market_intel` must be denied |
| 5 — Views/functions | `launchpad.v_api_budget_status` is queryable |
| 6 — Role privilege audit | Prints schema-level and table-level grants; verifies `launchpad_app` has no superuser or CREATEROLE |
| 7 — Summary | Counts passed/failed; lists failed test names with detail |

## Flow

### seed_compliance_rules.py
```
CLI args parsed → load_dotenv() → resolve_dsn() → connect(role="launchpad_app")
  → [--clear] clear_compliance_rules() → DELETE FROM compliance_rules
  → seed_compliance_rules()
      → _ensure_unique_constraint()  # idempotent DDL guard
      → INSERT ... ON CONFLICT DO NOTHING (per rule)
      → conn.commit()
  → print summary (inserted / skipped / by regime)
  → conn.close()
```

### validate_launchpad_access.sql
```
psql -U launchpad_app -d amazon_dash -f validate_launchpad_access.sql
  → CREATE TEMP TABLE _test_results
  → CREATE FUNCTION _pass(), _fail()
  → Section 1–6: DO $$ blocks with SAVEPOINT-guarded DML
      → each test calls _pass() or _fail() → INSERT into _test_results
  → Section 7: aggregate counts, RAISE NOTICE summary
  → SELECT failed tests
  → DROP FUNCTION _pass, _fail; DROP TABLE _test_results
```

## Integration

- **When to run `seed_compliance_rules.py`**: During initial deployment after the `launchpad` schema migrations have been applied. Re-run with `--clear` when the seed dataset is updated. Requires `launchpad_app` role with INSERT on `compliance_rules` (or `launchpad_admin` if INSERT is revoked from `launchpad_app`).
- **When to run `validate_launchpad_access.sql`**: After any migration that alters `launchpad` schema permissions, after role/grant changes, or as a smoke test in CI. Run as `launchpad_app` to test that role's actual permissions.
- **Depends on**: `services/db_connection.py` (`connect`, `resolve_dsn`), `.env` file with `LAUNCHPAD_DB_DSN` (or fallback vars), PostgreSQL `launchpad` schema with all tables present.
- **Consumed by**: Deployment runbooks and CI permission checks. Not imported or called by any application service.
- **Tables touched**: `launchpad.compliance_rules` (seed script writes), all `launchpad.*` tables and `market_intel.*` views (validation script reads via savepoint-rolled-back DML).
