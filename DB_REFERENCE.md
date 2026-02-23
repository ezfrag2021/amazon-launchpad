# Launchpad Schema Reference (Migrations 001-007)

| Table | Primary Columns | Key Relationships |
|-------|-----------------|-------------------|
| **product_launches** | launch_id (PK), source_asin, source_marketplace, target_marketplaces[], product_category, pursuit_score, pursuit_category, current_stage | Central entity; referenced by all child tables |
| **niche_mapping** | mapping_id (PK), launch_id (FK), niche_id, marketplace, confidence | Links to market_intel.niche_definitions (cross-schema read) |
| **review_moat_analysis** | moat_id (PK), launch_id (FK), marketplace, competitor_count, avg_review_count, avg_rating, review_velocity_30d, moat_strength | Per-marketplace analysis; UNIQUE(launch_id, marketplace) |
| **compliance_rules** | rule_id (PK), regime, category_pattern, requirement_name, documentation_required[], is_2026_dpp_relevant | Reference data; seeded by seed_compliance_rules.py |
| **launch_compliance_checklist** | checklist_id (PK), launch_id (FK), rule_id (FK), status, evidence_url | Junction table; UNIQUE(launch_id, rule_id) |
| **ingredient_registry** | ingredient_id (PK), canonical_name, normalized_name, cas_number, synonyms[] | Canonical ingredient dictionary for concentration checks |
| **ingredient_compliance_rules** | ingredient_rule_id (PK), ingredient_id (FK), jurisdiction, product_category, rule_type, max_concentration | EU/UK ingredient-level thresholds/restrictions with citations |
| **pricing_analysis** | pricing_id (PK), launch_id (FK), marketplace, recommended_launch_price, price_floor, price_ceiling, margin_estimate_pct | UNIQUE(launch_id, marketplace) |
| **ppc_simulation** | sim_id (PK), launch_id (FK), marketplace, keyword, search_volume_exact, estimated_cpc, estimated_acos_pct | Per-keyword estimates; UNIQUE(launch_id, marketplace, keyword) |
| **risk_assessment** | risk_id (PK), launch_id (FK), risk_category, risk_description, severity, mitigation | 5 categories: safety, fragility, IP, compliance, market |
| **listing_drafts** | draft_id (PK), launch_id (FK), marketplace, version, title, bullets (JSONB), backend_keywords, rufus_optimized | Versioned; UNIQUE(launch_id, marketplace, version) |
| **image_gallery** | image_id (PK), launch_id (FK), slot_number (1-7), image_type, storage_path | 7 slots max; UNIQUE(launch_id, slot_number) |
| **api_call_ledger** | ledger_id (PK), called_at, script_name, endpoint, marketplace, billable_pages, launch_id (FK) | Indexed by month; tracks budget consumption |
| **budget_config** | id=1 (PK), monthly_hard_cap=500, allow_override, override_reason | Single-row config; launchpad_app: SELECT only |
| **jungle_scout_cache** | cache_id (PK), asin, marketplace, endpoint, response_data (JSONB), fetched_at, expires_at | UNIQUE(asin, marketplace, endpoint) |

## Functions & Views

| Name | Type | Purpose |
|------|------|---------|
| `get_js_cache(asin, marketplace, endpoint)` | Function | Returns cached JSONB if valid |
| `set_js_cache(...)` | Function | Upsert cache entry |
| `v_api_budget_status` | View | Monthly budget summary |
| `v_js_cache_summary` | View | Cache coverage per ASIN/marketplace |

## Security Model

| Role | Permissions |
|------|-------------|
| launchpad_admin | Schema owner, creates objects |
| launchpad_app | SELECT/INSERT/UPDATE on launchpad tables; SELECT on market_intel tables |
| launchpad_reader | SELECT on launchpad tables |

## Cross-Schema Access

- **market_intel.niche_definitions** (read)
- **market_intel.niche_competitors** (read)
- **market_intel.competitor_sales_weekly** (read)
- **market_intel.niche_keyword_bank** (read)
- **market_intel.v_war_room_competitor_detail** (read)
- **market_intel.v_niche_keyword_summary** (read)
- **market_intel.competitor_price_daily** (read)
- **market_intel.marketplace_lookup** (read)

---

> **CRITICAL: Connection Warning**  
> Do NOT double-encode DSN passwords. The `.env` file contains pre-encoded values (`LAUNCHPAD_DB_PASSWORD_ENC`). Calling `normalize_dsn()` on already-encoded DSNs breaks authentication (`%` → `%25`).  
> **Fixed in commits:** `2c017ac` (JS client), `663c004` (DB connection)
