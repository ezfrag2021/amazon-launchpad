# migrations/

## Responsibility
Database Schema Versioning and Evolution — sequential PostgreSQL migrations that build the complete `launchpad` schema from security foundation through feature tables to cache optimization. Each file is an idempotent DDL unit applied in order to the `amazon_dash` database.

## Design

**Sequential Migration Pattern**: Files are numbered `001`–`008` and must be applied in order. Each migration executes `SET ROLE launchpad_admin` before DDL, ensuring all objects are owned by the admin role.

**PostgreSQL Schema Isolation**: All application tables live in the `launchpad` schema (not `public`), providing namespace isolation from the co-resident `market_intel` schema (owned by a separate service).

**Role-Based Access Control (RBAC)**:
- `launchpad_admin` — DDL owner, no login
- `launchpad_app` — runtime application role, SELECT/INSERT/UPDATE
- `launchpad_reader` — read-only analytics role, SELECT only

**Cross-Schema Read Access**: `launchpad_app` is granted SELECT on specific `market_intel` tables (`niche_definitions`, `niche_competitors`, `competitor_sales_weekly`, `niche_keyword_bank`, `competitor_price_daily`, `marketplace_lookup`, and two views) — logical FK relationships without physical constraints.

**Cache-First API Pattern** (migrations 007–008): Jungle Scout API responses are cached in PostgreSQL with upsert semantics to implement "fetch once, use many times", protecting against expensive API quota consumption.

## Flow

### Migration Sequence

| # | File | Purpose | Key Objects |
|---|------|---------|-------------|
| 001 | `001_launchpad_security.sql` | Security foundation | Schema `launchpad`, roles (`launchpad_admin`, `launchpad_app`, `launchpad_reader`), default privileges, cross-schema grants to `market_intel` |
| 002 | `002_launchpad_core_tables.sql` | Central launch entity | `product_launches` (central entity with pursuit_score/category and stage 1–4), `niche_mapping` (logical FK to `market_intel.niche_definitions`), `review_moat_analysis` |
| 003 | `003_launchpad_compliance.sql` | EU/UK regulatory compliance | `compliance_rules` (CE, UKCA, WEEE, RoHS, ToyEN71, DPP regimes with 2026 DPP flag), `launch_compliance_checklist` (per-launch status tracking) |
| 004 | `004_launchpad_pricing.sql` | Pricing and PPC analysis | `pricing_analysis` (price floor/ceiling/percentiles per marketplace), `ppc_simulation` (keyword-level CPC/ACoS/TACoS/days-to-page1 estimates), `risk_assessment` (safety/IP/compliance/market risks) |
| 005 | `005_launchpad_creative.sql` | AI-generated content | `listing_drafts` (versioned title/bullets/description/A+ content, tracks generating model), `image_gallery` (7-slot image management with type constraints) |
| 006 | `006_launchpad_api_budget.sql` | API cost governance | `api_call_ledger` (billable page tracking per script/endpoint), `budget_config` (single-row hard cap, default 500 pages/month, INSERT/DELETE revoked from `launchpad_app`), `v_api_budget_status` view |
| 007 | `007_cache_jungle_scout_data.sql` | Jungle Scout response cache | `jungle_scout_cache` table, `get_js_cache()` / `set_js_cache()` functions, `v_js_cache_summary` view |
| 008 | `008_cache_evolution.sql` | Cache parameterization | Adds `request_key VARCHAR(64)` to `jungle_scout_cache`, replaces 3-column unique constraint with 4-column, updates both cache functions with `p_request_key` and `p_ttl_hours` parameters |

### Migration 008 Cache Evolution Detail

Migration 007 used a `(asin, marketplace, endpoint)` unique key — one cached response per ASIN/endpoint combination. Migration 008 introduces `request_key` to support parameterized caching (e.g., different keyword sets for the same ASIN):

1. `ALTER TABLE` adds `request_key VARCHAR(64) NOT NULL DEFAULT 'default'` — backward compatible, existing rows get `'default'`
2. Drops old unique constraint `jungle_scout_cache_asin_marketplace_endpoint_key`
3. Adds new constraint `jungle_scout_cache_unique_lookup` on `(asin, marketplace, endpoint, request_key)`
4. Drops and recreates `idx_js_cache_lookup` index with the 4-column key
5. Replaces `get_js_cache(asin, marketplace, endpoint)` → `get_js_cache(asin, marketplace, endpoint, p_request_key DEFAULT 'default')`
6. Replaces `set_js_cache(asin, marketplace, endpoint, response_data, api_calls_used)` → adds `p_request_key VARCHAR(64) DEFAULT 'default'` and `p_ttl_hours INTEGER DEFAULT NULL`; TTL is computed as `now() + interval` when provided, enabling expiring cache entries

## Integration

**Consumed by**: All Python scripts in `scripts/` that interact with the database — launch analysis, compliance seeding, pricing simulation, creative generation, and Jungle Scout data fetching.

**Depends on**: `market_intel` schema (must exist before migration 001 runs, as cross-schema grants reference its tables and views).

**Schema supports**:
- Product launch lifecycle tracking (stages 1–4) via `product_launches`
- Multi-marketplace analysis (US source → UK/DE/FR/IT/ES targets by default)
- EU regulatory compliance workflows (2026 DPP readiness flag on `compliance_rules`)
- AI content generation audit trail (model name, version, timestamp on `listing_drafts` and `image_gallery`)
- API cost governance with hard cap enforcement via `budget_config` (INSERT/DELETE revoked from `launchpad_app`)
- Jungle Scout quota protection via parameterized response cache with optional TTL (`expires_at`)
