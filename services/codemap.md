# services/

## Responsibility
Core business logic layer for Amazon Launchpad. Implements the Service Layer pattern — stateless classes and pure functions that encapsulate all domain rules, scoring algorithms, compliance checks, pricing calculations, and external API access. Pages and scripts call into this layer; the layer never calls back into pages.

---

## Design

### Patterns
- **Service Layer** — All domain logic lives here, isolated from HTTP/UI concerns. Pages import service classes and call methods; services return plain dicts or primitives.
- **Stateless Classes** — `ComplianceEngine`, `OpportunityScorer`, `PricingEngine`, `LaunchStateManager` hold no instance state (except `JungleScoutClient` which wraps the SDK client). All state lives in the DB or is passed as arguments.
- **Repository-style DB Access** — `LaunchStateManager` owns all SQL for `launchpad.product_launches`, `launch_compliance_checklist`, `pricing_analysis`, and `listing_drafts`. No ORM; raw psycopg with `dict_row` for ergonomic result access.
- **Cache-First API Client** — `JungleScoutClient.get_cached_or_fetch()` checks `launchpad.js_api_cache` (via `launchpad.get_js_cache` / `launchpad.set_js_cache` DB functions) before hitting the live Jungle Scout API. Cache key is a SHA-256 hash of sorted request params; TTL defaults to 24 hours.
- **Budget Metering** — Every Jungle Scout API call is gated by `JungleScoutClient.reserve_budget()`, which reads `launchpad.v_api_budget_status` and inserts into `launchpad.api_call_ledger`. **Never touches `market_intel.api_call_ledger`.**
- **Pure Logic Layer** — `ComplianceEngine` and `OpportunityScorer` are pure Python with no DB access; they receive data as arguments and return computed results.

### Module Inventory

| Module | Class / Functions | Role |
|---|---|---|
| `auth_manager.py` | `resolve_service_account_key_path()`, `get_generative_client()` | Google Generative AI credential resolution for Stage 4 |
| `compliance_engine.py` | `ComplianceEngine` | CE/UKCA/WEEE/RoHS/ToyEN71/DPP rules matching and checklist generation |
| `db_connection.py` | `resolve_dsn()`, `normalize_dsn()`, `inject_role()`, `connect()` | PostgreSQL DSN resolution and connection factory |
| `js_client.py` | `JungleScoutClient`, `BudgetExhaustedError` | Jungle Scout API wrapper with cache-first access and budget metering |
| `launch_state.py` | `LaunchStateManager` | Product launch CRUD and stage-progression state machine |
| `marketplace_policy.py` | Module-level functions | Marketplace code normalisation and US→UK/EU mapping |
| `opportunity_scorer.py` | `OpportunityScorer`, `ScoreBreakdown` | Pursuit Score calculation (0–100) and category classification |
| `pricing_engine.py` | `PricingEngine` | Launch price envelope, PPC simulation, margin calculation |

---

## Flow

### Stage 1 — Opportunity Validator
```
Page (pages/opportunity_validator/)
  → marketplace_policy.validate_source_marketplace(code)   # enforce US-only input
  → marketplace_policy.get_target_marketplaces_for_launch("US")  # → ["UK","DE","FR","IT","ES"]
  → LaunchStateManager.create_launch(conn, source_asin, ...)  # INSERT product_launches
  → JungleScoutClient.get_product_database(conn, marketplace, ...)
      → get_cached_or_fetch()
          → launchpad.get_js_cache(asin, marketplace, endpoint, key)  # cache check
          → [MISS] reserve_budget() → INSERT launchpad.api_call_ledger
          → ClientSync.product_database(...)  # live API call
          → launchpad.set_js_cache(...)  # store result
  → OpportunityScorer.calculate_pursuit_score(competitor_count, avg_reviews, ...)
      → analyze_competitor_density(), analyze_review_moat(), analyze_market_stability()
      → weighted sum × price_stability multiplier → (score, category)
  → LaunchStateManager.update_launch(conn, launch_id, pursuit_score=..., pursuit_category=...)
  → LaunchStateManager.advance_stage(conn, launch_id)  # Stage 1 → 2 if pursuit_score set
```

### Stage 2 — Compliance Compass
```
Page (pages/compliance_compass/)
  → LaunchStateManager.get_launch(conn, launch_id)  # fetch product_category
  → ComplianceEngine.generate_checklist(launch_id, product_category, attributes, rules)
      → match_rules_for_product()  # regex/substring match on category_pattern
      → is_dpp_relevant()  # keyword scan for EU DPP 2026 applicability
      → returns list[dict] ready for INSERT into launch_compliance_checklist
  → [DB INSERT of checklist items]
  → ComplianceEngine.calculate_compliance_progress(checklist_items)  # status counts + pct
  → ComplianceEngine.get_next_action(checklist_items)  # human-readable recommendation
  → LaunchStateManager.can_advance_stage()  # checks no pending/blocked items remain
  → LaunchStateManager.advance_stage()  # Stage 2 → 3
```

### Stage 3 — Risk & Pricing Architect
```
Page (pages/pricing_architect/)
  → JungleScoutClient.get_keywords_by_asin(conn, asin, marketplace)  # cache-first
  → JungleScoutClient.get_sales_estimates(conn, asin, marketplace)   # cache-first
  → PricingEngine.analyze_competitor_pricing(competitor_prices)
      → percentiles (p25/p50/p75), CV, stability label
  → PricingEngine.calculate_launch_price_envelope(prices, target_margin_pct, cost_of_goods)
      → price_floor, recommended_launch_price, price_ceiling, margin_estimate_pct
  → PricingEngine.simulate_ppc_campaign(keywords, daily_budget, target_acos, marketplace)
      → per-keyword: estimated_cpc, acos_pct, tacos_pct, daily_spend, days_to_page1
  → PricingEngine.calculate_margin(price, cogs, amazon_fees_pct, fulfillment_cost)
  → PricingEngine.assess_price_viability(recommended, floor, ceiling, competitor_count)
  → [DB INSERT into launchpad.pricing_analysis + launchpad.ppc_simulation]
  → LaunchStateManager.advance_stage()  # Stage 3 → 4
```

### Stage 4 — Creative Studio
```
Page (pages/creative_studio/)
  → auth_manager.get_generative_client()
      → resolve_service_account_key_path()  # GOOGLE_SERVICE_ACCOUNT_JSON env var or repo root default
      → service_account.Credentials.from_service_account_file(key_path, scopes=[...])
      → genai.configure(credentials=...)
      → returns configured google.generativeai module
  → genai.GenerativeModel(...).generate_content(...)  # Gemini listing generation
  → [DB INSERT into launchpad.listing_drafts]
  → LaunchStateManager.advance_stage()  # Stage 4 → 5 (Launch Ready)
```

### DB Connection Setup (all stages)
```
db_connection.resolve_dsn("LAUNCHPAD_DB_DSN", "MARKET_INTEL_DSN", "PG_DSN")
  → normalize_dsn(dsn)   # URL-encode password special chars
  → inject_role(dsn, "launchpad_app")  # appends ?options=-c role=launchpad_app
  → connect(dsn, role="launchpad_app", read_only=False)
      → psycopg.connect(dsn + sslmode=disable)
      → SET ROLE launchpad_app
      → [optional] SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY
```

---

## Integration

### Consumers
- `pages/opportunity_validator/` — imports `LaunchStateManager`, `JungleScoutClient`, `OpportunityScorer`, `marketplace_policy`
- `pages/compliance_compass/` — imports `LaunchStateManager`, `ComplianceEngine`
- `pages/pricing_architect/` — imports `LaunchStateManager`, `JungleScoutClient`, `PricingEngine`
- `pages/creative_studio/` — imports `LaunchStateManager`, `auth_manager`
- All pages — import `db_connection` for connection setup

### External Dependencies
| Dependency | Used By | Purpose |
|---|---|---|
| `psycopg` | `db_connection`, `js_client`, `launch_state` | PostgreSQL driver |
| `google-auth` (`google.oauth2.service_account`) | `auth_manager` | Service account credential loading |
| `google-generativeai` | `auth_manager` | Gemini model client |
| `junglescout-client` (`junglescout.ClientSync`) | `js_client` | Jungle Scout API SDK |

### Database Tables / Views
| Object | Owner Module | Operation |
|---|---|---|
| `launchpad.product_launches` | `launch_state` | INSERT / SELECT / UPDATE |
| `launchpad.launch_compliance_checklist` | `launch_state` (reads), pages (writes) | SELECT (stage gate checks) |
| `launchpad.pricing_analysis` | `launch_state` (reads), pages (writes) | SELECT EXISTS (stage gate) |
| `launchpad.listing_drafts` | `launch_state` (reads), pages (writes) | SELECT EXISTS (stage gate) |
| `launchpad.api_call_ledger` | `js_client` | INSERT (budget reservation) |
| `launchpad.v_api_budget_status` | `js_client` | SELECT (budget check) |
| `launchpad.budget_config` | `js_client` (via view) | Source of monthly hard cap + override flag |
| `launchpad.js_api_cache` | `js_client` (via DB functions) | Cache store for Jungle Scout responses |
| `launchpad.get_js_cache(asin, marketplace, endpoint, key)` | `js_client` | Cache read function |
| `launchpad.set_js_cache(asin, marketplace, endpoint, data, calls, key, ttl_hours)` | `js_client` | Cache write function |

### Key Constants
| Constant | Module | Value |
|---|---|---|
| `DEFAULT_ROLE` | `db_connection` | `"launchpad_app"` |
| `ALLOWED_INPUT_MARKETPLACE` | `marketplace_policy` | `"US"` |
| `DEFAULT_TARGET_MARKETPLACES` | `marketplace_policy` | `["UK","DE","FR","IT","ES"]` |
| `SATURATED_THRESHOLD` | `opportunity_scorer` | `40.0` |
| `PROVEN_THRESHOLD` | `opportunity_scorer` | `70.0` |
| `DEFAULT_TARGET_MARGIN` | `pricing_engine` | `30.0` |
| `AMAZON_REFERRAL_FEE_PCT` | `pricing_engine` | `15.0` |
| `DEFAULT_TARGET_ACOS` | `pricing_engine` | `30.0` |

### Scoring Weights (`opportunity_scorer`)
| Factor | Weight |
|---|---|
| `review_moat` | 0.25 |
| `competitor_density` | 0.20 |
| `sales_velocity` | 0.20 |
| `market_stability` | 0.15 |
| `rating_gap` | 0.10 |
| `keyword_difficulty` | 0.10 |

Score is multiplied by `price_stability` (0–1 float) as a final adjustment. Result stored as `NUMERIC(5,2)` in `product_launches.pursuit_score`.
