# pages/

## Responsibility
Streamlit multi-page UI layer for the Amazon Launchpad application. Each file is a numbered Streamlit page implementing one stage of the 4-stage product launch pipeline: opportunity validation → compliance → pricing/risk → creative asset generation.

## Design

**Pattern: Streamlit Multi-Page App**
- Files prefixed `N_Name.py` are auto-discovered by Streamlit as sidebar pages
- Each page is self-contained: owns its own DB connection, session state keys, and service imports
- Pages enforce sequential stage gating — each page checks that the prior stage is complete before rendering its main content
- `st.cache_resource` used for DB connections to avoid reconnecting on every rerender
- Session state (`st.session_state`) used as in-page ephemeral store for form data, fetched results, and computed outputs

**DB Access Pattern**
- Pages 1 and 4 use a `_get_dsn()` / `_open_conn()` pattern (new connection per operation, context-managed)
- Pages 2 and 3 use a module-level `get_connection()` cached singleton via `@st.cache_resource`
- All DB writes use explicit `conn.commit()` / `conn.rollback()` — no autocommit
- DSN resolved via `services.db_connection.resolve_dsn("LAUNCHPAD_DB_DSN", "MARKET_INTEL_DSN", "PG_DSN")` with fallback chain

**Rendering Pattern**
- Each page decomposes into private `_render_*()` functions (header, selector, data sections, save)
- `main()` function orchestrates render order (pages 1 and 4 only; pages 2 and 3 use top-level script execution)
- Stage gate checks call `LaunchStateManager.can_advance_stage()` and `advance_stage()` for transitions

## Pages

### 1_Opportunity_Validator.py — Stage 1
**Purpose:** Input a US ASIN, fetch competitor data from Jungle Scout across UK/EU markets, compute a Pursuit Score, and persist results.

**Key flows:**
1. Launch selector: load existing launches from `launchpad.product_launches` via `LaunchStateManager.list_launches()`
2. Data gathering: ASIN input + target marketplace multiselect (UK/DE/FR/IT/ES)
3. Budget check: `JungleScoutClient.check_budget_available()` pre-flight before API calls
4. Competitor fetch: `JungleScoutClient.get_product_database()` per marketplace → `_parse_js_response()` → flat competitor list
5. Score calculation: `OpportunityScorer.calculate_pursuit_score()` with 6 inputs derived from competitor stats (competitor_count, avg_review_count, review_velocity_30d, avg_rating, sales_velocity_score, keyword_difficulty)
6. Score categories: `CATEGORY_SATURATED` / `CATEGORY_PROVEN` / `CATEGORY_GOLDMINE` with color-coded display
7. Save: `LaunchStateManager.create_launch()` or update existing, then upsert into `launchpad.review_moat_analysis` per marketplace

**Session state keys:** `launches`, `selected_launch_id`, `competitor_data`, `pursuit_score`, `pursuit_category`, `score_breakdown`

**Services consumed:** `JungleScoutClient`, `OpportunityScorer`, `LaunchStateManager`, `db_connection`

---

### 2_Compliance_Compass.py — Stage 2
**Purpose:** Match regulatory compliance rules (CE/UKCA/WEEE/RoHS/ToyEN71/DPP) to a product category, generate a per-launch checklist, and track completion status.

**Key flows:**
1. Launch selection: filtered to launches with `pursuit_score IS NOT NULL` (Stage 1 complete); warns if Stage 1 incomplete
2. Product category input: free-text field saved to `launchpad.product_launches.product_category`
3. DPP relevance check: `ComplianceEngine.is_dpp_relevant()` — shows 2026 DPP alert if applicable
4. Rule matching: `ComplianceEngine.match_rules_for_product()` against all rules in `launchpad.compliance_rules`
5. Checklist generation: `ComplianceEngine.generate_checklist()` → upsert into `launchpad.launch_compliance_checklist` (ON CONFLICT DO NOTHING preserves existing items)
6. Progress tracking: `ComplianceEngine.calculate_compliance_progress()` → completion %, counts by status
7. Checklist display: grouped by regime, each item has status dropdown (pending/in_progress/completed/not_applicable/blocked), evidence URL, and notes; auto-saves on widget change via `update_checklist_item()`
8. Stage advance: `LaunchStateManager.can_advance_stage()` → `advance_stage()` with confirmation dialog

**Regime config:** `_REGIME_CONFIG` dict maps regime codes to display labels, colors, icons, descriptions
**Status config:** `_STATUS_CONFIG` maps 5 statuses to display properties

**Services consumed:** `ComplianceEngine`, `LaunchStateManager`, `db_connection`

---

### 3_Risk_Pricing_Architect.py — Stage 3
**Purpose:** Analyse competitor pricing, calculate a price envelope (floor/recommended/ceiling), simulate PPC campaigns per keyword, and capture structured risk assessments.

**Stage gate:** Requires `current_stage >= STAGE_COMPLIANCE` (Stage 2 complete).

**Key flows:**

**Section 1 — Competitor Pricing Analysis:**
- Manual price entry (text area, one price per line) or fetch from `JungleScoutClient.get_product_database()`
- `PricingEngine.analyze_competitor_pricing()` → min/max/mean/p25/p50/p75, price stability classification
- Altair histogram of price distribution; top-10 competitor table

**Section 2 — Price Envelope Calculator:**
- Inputs: COGS, target margin %, Amazon referral fee %, fulfillment cost
- `PricingEngine.calculate_launch_price_envelope()` → price_floor, recommended_launch_price, price_ceiling
- `PricingEngine.calculate_margin()` at each price point → net margin %
- `PricingEngine.assess_price_viability()` → viability_score (0–100) + recommendations
- Altair bar chart of price positioning

**Section 3 — PPC Campaign Simulator:**
- Keyword list input; optionally enriched via `JungleScoutClient.get_share_of_voice()` per keyword
- `PricingEngine.simulate_ppc_campaign()` → per-keyword: estimated_cpc, estimated_acos_pct, estimated_daily_spend, estimated_days_to_page1
- Campaign totals: total daily spend, blended ACoS

**Section 4 — Risk Assessment:**
- 5 risk categories: safety, fragility, IP, compliance, market
- Per-category: severity (Low/Medium/High/Critical), description, mitigation strategy
- Overall risk rating computed as weighted average of severity scores

**Section 5 — Save & Complete:**
- Saves to `launchpad.pricing_analysis` (upsert by launch_id + marketplace)
- Saves to `launchpad.ppc_simulation` (upsert by launch_id + marketplace + keyword)
- Saves to `launchpad.risk_assessment` (insert per risk_category)
- Stage advance via `LaunchStateManager.advance_stage(validate=True)`

**Session state keys:** `fetched_competitor_prices`, `competitor_analysis`, `competitor_prices`, `price_envelope`, `cogs`, `amazon_fee_pct`, `fulfillment_cost`, `ppc_simulation`, `ppc_marketplace`

**Services consumed:** `PricingEngine`, `JungleScoutClient`, `LaunchStateManager`, `db_connection`

---

### 4_Creative_Studio.py — Stage 4
**Purpose:** AI-powered Amazon listing generation (Google Gemini) and product image generation (Google Imagen 3) with 7-slot gallery management, version history, and export.

**Stage gate:** Requires `current_stage >= 3` (Stage 3 complete); only shows launches at Stage 3+.

**Key flows:**

**Listing Generation:**
- Inputs: product name, key features, target keywords (auto-populated from `launchpad.ppc_simulation`), brand voice (Professional/Friendly/Technical/Luxury), A+ Content toggle, RUFUS AI optimization toggle
- `_build_listing_prompt()` constructs structured JSON-output prompt for Gemini
- `_generate_listing()` calls `get_generative_client().GenerativeModel("gemini-2.0-flash").generate_content()` → parses JSON response
- Output fields: title (max 200 chars), 5 bullets (max 500 chars each), description (HTML, max 2000 chars), backend_keywords (max 250 bytes), quality_score (0–100), quality_notes, optimization_suggestions, optional a_plus_content
- Editable display with per-field character/byte counters and Amazon limit enforcement
- Draft save: `_save_listing_draft()` → `launchpad.listing_drafts` with auto-incremented version

**Image Gallery (7 slots):**
- Fixed slot definitions in `IMAGE_SLOTS` dict: main_white_bg, lifestyle, infographic, comparison, dimensions, packaging, in_use
- Per-slot: generate via `_generate_image_with_imagen()` (Imagen 3 `imagen-3.0-generate-001`) or manual upload
- `_build_image_prompt()` generates slot-type-specific prompts
- Gallery persisted to `launchpad.image_gallery` (upsert by launch_id + slot_number)
- Per-slot requirements checklist (Amazon compliance rules per slot type)

**Version Management:**
- `_load_draft_versions()` loads all versions from `launchpad.listing_drafts` ordered by version DESC
- Restore: overwrites `cs_generated_listing` / `cs_edited_listing` session state
- Delete: hard-delete from `launchpad.listing_drafts`
- Side-by-side comparison of any two versions

**Marketplace Tabs:**
- Primary tab (UK) for main listing generation
- Additional tabs (DE/FR/IT/ES) for localized listing generation via separate `_generate_listing()` calls with marketplace context
- Per-marketplace drafts stored independently in `launchpad.listing_drafts`

**Final Review & Export:**
- Launch readiness checklist: listing generated, 7/7 images, title ≤200 chars, 5 bullets present
- Finalize: `LaunchStateManager.advance_stage(validate=False)` → Stage 5 (Launch Ready)
- Export: CSV download (listing fields), ZIP download (image bytes), Markdown launch report

**Session state keys (prefixed `cs_`):** `cs_launches`, `cs_selected_launch_id`, `cs_launch_data`, `cs_product_name`, `cs_key_features`, `cs_target_keywords`, `cs_brand_voice`, `cs_include_aplus`, `cs_generated_listing`, `cs_edited_listing`, `cs_image_gallery`, `cs_draft_versions`, `cs_rufus_optimize`, `cs_active_marketplace`

**Services consumed:** `get_generative_client` (auth_manager), `LaunchStateManager`, `db_connection`

---

## Flow

```
User opens page
    │
    ├─ DB connection established (cached or per-call)
    ├─ Launch selector → loads launchpad.product_launches
    ├─ Stage gate check → stops if prior stage incomplete
    │
    ├─ [Stage 1] ASIN input → JS API fetch → competitor table → score calc → save
    ├─ [Stage 2] Category input → rule match → checklist generate → item status updates → advance
    ├─ [Stage 3] Price input → envelope calc → PPC simulate → risk assess → save → advance
    └─ [Stage 4] Listing inputs → Gemini generate → edit → save draft
                 Image slots → Imagen generate / upload → gallery save
                 Final review → finalize (Stage 5) / export
```

## Integration

| Page | Reads From | Writes To |
|------|-----------|-----------|
| 1_Opportunity_Validator | `launchpad.product_launches` | `launchpad.product_launches`, `launchpad.review_moat_analysis` |
| 2_Compliance_Compass | `launchpad.product_launches`, `launchpad.compliance_rules`, `launchpad.launch_compliance_checklist` | `launchpad.product_launches` (category), `launchpad.launch_compliance_checklist` |
| 3_Risk_Pricing_Architect | `launchpad.product_launches`, `launchpad.pricing_analysis` | `launchpad.pricing_analysis`, `launchpad.ppc_simulation`, `launchpad.risk_assessment` |
| 4_Creative_Studio | `launchpad.product_launches`, `launchpad.pricing_analysis`, `launchpad.ppc_simulation`, `launchpad.listing_drafts`, `launchpad.image_gallery` | `launchpad.listing_drafts`, `launchpad.image_gallery` |

**External APIs:**
- Jungle Scout API: pages 1 and 3 via `services.js_client.JungleScoutClient`
- Google Gemini (`gemini-2.0-flash`): page 4 via `services.auth_manager.get_generative_client()`
- Google Imagen 3 (`imagen-3.0-generate-001`): page 4 via same generative client

**Shared services:**
- `services.db_connection` — DSN resolution and connection factory
- `services.launch_state.LaunchStateManager` — all 4 pages use for launch CRUD and stage transitions
- `services.opportunity_scorer.OpportunityScorer` — page 1 only
- `services.compliance_engine.ComplianceEngine` — page 2 only
- `services.pricing_engine.PricingEngine` — page 3 only
