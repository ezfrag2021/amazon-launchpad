# Amazon Launchpad — Project Structure & Architecture

> **Status**: Planning  
> **Relationship to amazon-mi**: Separate Streamlit application, shared infrastructure  
> **Last updated**: 2026-02-18

---

## 1. Executive Summary

### What is the Launchpad?

A 4-stage product launch workflow for Amazon UK/EU markets. Unlike amazon-mi (which provides **ongoing competitive intelligence** with weekly monitoring cadence), the Launchpad is a **one-shot project workflow** that guides a user through sequential stages to launch a new product:

| Stage | Name | Purpose |
|-------|------|---------|
| 1 | Opportunity Validator | Input a US ASIN → map to UK/EU niche → score the opportunity |
| 2 | Compliance Compass | Identify regulatory requirements (CE/UKCA/WEEE/RoHS/DPP) → generate checklist |
| 3 | Risk & Pricing Architect | Predict launch price, simulate PPC spend, assess product risks |
| 4 | Creative Studio | Generate AI-optimized listings, product images, and A+ Content |

### Why a Separate Project?

1. **Workflow paradigm mismatch**: amazon-mi is a continuous monitoring dashboard (weekly cadence, ongoing pipelines). Launchpad is a sequential per-product lifecycle tool.
2. **Marketplace scope conflict**: amazon-mi explicitly excludes US (`EXCLUDED_MARKETPLACES = ("US",)` in `marketplace_policy.py`). Launchpad Stage 1 starts with US ASIN input.
3. **~70% new territory**: Only ~30% of launchpad data needs (competitor analysis, keyword research, pricing) overlap with existing `market_intel` schema. Compliance, PPC simulation, AI image generation, and listing optimization are entirely new domains.
4. **Dependency blast radius**: New APIs (compliance data, image generation, Amazon Advertising API) would destabilize amazon-mi's production deployment if co-located.
5. **Cognitive load**: Two fundamentally different mental models (monitoring vs. project execution) in one sidebar creates UX confusion.

### What's Shared?

| Asset | Sharing Model |
|-------|---------------|
| PostgreSQL instance | Same DB (`amazon_dash`), separate schema (`launchpad`) |
| Market intel data | Launchpad reads `market_intel.*` views (READ-ONLY) |
| DSN handling pattern | Same URL-encoding + role injection pattern |
| Jungle Scout client | Same library + auth, **separate budget counters** |
| Google AI auth | Same service account key |
| Cloudflare Zero Trust | Same tunnel, different route |
| App host | Same machine (192.168.0.121), different port |

---

## 2. Infrastructure — Shared with amazon-mi

### 2.1 PostgreSQL

| Property | Value |
|----------|-------|
| Host | `192.168.0.110` |
| Port | `5433` |
| Database | `amazon_dash` |
| SSL | `sslmode=disable` (server doesn't support SSL on internal network) |
| App host | `192.168.0.121` (where code runs) |

**Existing schemas** (amazon-mi owned):
- `public` — Amazon BI tables (asin_marketplace, performance_daily, economics_daily, catalog_items)
- `market_intel` — MI curated tables and views (niche_definitions, niche_competitors, competitor_sales_weekly, etc.)
- `market_intel_raw` — Raw Jungle Scout API ingestion landing

**Existing roles** (amazon-mi):
- `market_intel_admin` (NOLOGIN, schema owner)
- `market_intel_ingest` (LOGIN, writes to raw)
- `market_intel_transform` (LOGIN, raw→curated ETL)
- `market_intel_app` (LOGIN, read-only dashboard access)
- `market_intel_writer` (LOGIN, used by dashboard for admin-role pages)

**New for Launchpad:**
- Schema: `launchpad`
- Roles:
  - `launchpad_admin` (NOLOGIN, schema owner)
  - `launchpad_app` (LOGIN, dashboard read/write for launch workflows)
  - `launchpad_reader` (LOGIN, read-only for reporting)

**Cross-schema access:**
```sql
-- Launchpad reads market_intel views (never writes)
GRANT USAGE ON SCHEMA market_intel TO launchpad_app;
GRANT SELECT ON TABLE market_intel.niche_definitions TO launchpad_app;
GRANT SELECT ON TABLE market_intel.niche_competitors TO launchpad_app;
GRANT SELECT ON TABLE market_intel.competitor_sales_weekly TO launchpad_app;
GRANT SELECT ON TABLE market_intel.niche_keyword_bank TO launchpad_app;
GRANT SELECT ON TABLE market_intel.v_war_room_competitor_detail TO launchpad_app;
GRANT SELECT ON TABLE market_intel.v_niche_keyword_summary TO launchpad_app;
GRANT SELECT ON TABLE market_intel.competitor_price_daily TO launchpad_app;
GRANT SELECT ON TABLE market_intel.marketplace_lookup TO launchpad_app;
```

**DSN pattern** (identical to amazon-mi):
```python
# Three-tier env var fallback
dsn = os.getenv("LAUNCHPAD_DB_DSN") or os.getenv("MARKET_INTEL_DSN") or os.getenv("PG_DSN")

# URL-encode credentials (handles special chars in passwords)
from urllib.parse import urlparse, quote_plus, urlunparse
parsed = urlparse(dsn)
safe_password = quote_plus(parsed.password)
normalized = parsed._replace(netloc=f"{parsed.username}:{safe_password}@{parsed.hostname}:{parsed.port}")

# Role injection
if "options=" not in dsn:
    dsn += "?options=-c role=launchpad_admin"
```

### 2.2 Cloudflare Zero Trust

| Property | amazon-mi | amazon-launchpad |
|----------|-----------|------------------|
| Route | `mi.yourdomain.com` → `localhost:8502` | `launchpad.yourdomain.com` → `localhost:8503` |
| Auth | Google SSO allowlist | Same Google SSO allowlist |
| Origin | Private behind tunnel | Private behind tunnel |

Same `cloudflared` service, additional route in tunnel config.

### 2.3 Port Allocation

| Port | Service | Status |
|------|---------|--------|
| 8501 | Amazon BI Dashboard | **PERMANENTLY RESERVED — NEVER USE** |
| 8502 | Market Intelligence (amazon-mi) | Active, systemd `streamlit-mi-dashboard.service` |
| 8503 | Product Launchpad (amazon-launchpad) | **NEW** |

### 2.4 Google Generative AI Auth

Shared service account for Gemini models:

| Property | Value |
|----------|-------|
| Key file | `gen-lang-client-0422857398-6a11b7435ae6.json` |
| Env override | `GOOGLE_SERVICE_ACCOUNT_JSON` |
| Scope | `https://www.googleapis.com/auth/generative-language` |
| Pattern | `google.oauth2.service_account.Credentials` → `genai.configure(credentials=...)` |

Launchpad uses Gemini more heavily (listing generation, image generation) than amazon-mi (optional summaries only).

### 2.5 Jungle Scout API

| Property | Value |
|----------|-------|
| Client library | `junglescout-client>=0.6,<1` |
| Auth env vars | `JUNGLESCOUT_API_KEY_NAME`, `JUNGLESCOUT_API_KEY` |
| Rate limit | 300 req/min, 15 req/sec |
| Pagination | Cursor-based via `links.next` |

**CRITICAL — Separate budget tracking:**
- amazon-mi: budget tracked in `market_intel.api_call_ledger` (1000 calls/month, enforced by `services/api_budget_guard.py`)
- Launchpad: MUST have its own budget table in `launchpad.api_call_ledger` with its own monthly cap
- The two budgets are independent — a Launchpad session must NEVER consume amazon-mi's allocation

**Endpoints relevant to Launchpad:**

| Endpoint | Use in Launchpad |
|----------|------------------|
| `product_database` | Stage 1: Find UK/EU competitors for a mapped niche |
| `keywords_by_asin` | Stage 1 & 3: Keyword research for opportunity scoring and PPC estimation |
| `sales_estimates` | Stage 1 & 3: Sales velocity for Pursuit Score and pricing analysis |
| `share_of_voice` | Stage 1: Competitive landscape assessment |

---

## 3. Shared Python Package — amazon-tools-core

> **Status**: To be extracted. Until then, Launchpad copies these utilities directly (matching the existing DRY violation in amazon-mi where `_get_admin_dsn()` is copy-pasted across 4 pages).

### Planned modules:

**`db.py`** — Database connection utilities
```python
# DSN resolution with three-tier env fallback
def resolve_dsn(primary_var: str, *fallback_vars: str) -> str: ...

# URL-encoding normalization for special chars in passwords
def normalize_dsn(dsn: str) -> str: ...

# Role injection via ?options=-c role=X
def inject_role(dsn: str, role: str) -> str: ...

# psycopg connection helper with sslmode=disable default
def connect(dsn: str, role: str, read_only: bool = False) -> psycopg.Connection: ...
```

**`auth.py`** — Google Generative AI authentication
```python
# Resolve service account key path (env override or repo default)
def resolve_service_account_key_path() -> Path: ...

# Return configured google.generativeai client
def get_generative_client() -> Any: ...
```

**`marketplace.py`** — Marketplace normalization
```python
MARKETPLACE_ALIASES = {"GB": "UK"}

def normalize_marketplace_code(value: str) -> str: ...
def get_marketplace_variants(code: str) -> list[str]: ...  # e.g., "UK" → ["UK", "GB"]
def filter_allowed_marketplaces(values: list[str]) -> list[str]: ...
```

**`js_budget.py`** — Jungle Scout API budget metering
```python
# Abstract budget tracker — each app provides its own schema/table
class ApiBudget:
    def __init__(self, schema: str, max_calls: int): ...
    def reserve_call(self, conn, script_name: str, endpoint: str) -> bool: ...
    def get_remaining(self, conn) -> int: ...
```

---

## 4. Launchpad Project Structure

```
amazon-launchpad/
├── .streamlit/
│   └── config.toml                    # port=8503, address=0.0.0.0, headless=true
├── .env                               # All env vars (see Section 7)
├── .env.app                           # App-mode overrides (LAUNCHPAD_DATA_MODE=db)
├── .gitignore                         # Same patterns as amazon-mi
├── app.py                             # Streamlit entrypoint with stage navigation
├── requirements.txt                   # Dependencies (see Section 9)
├── AGENTS.md                          # Agent guidance (same delegation model as amazon-mi)
│
├── pages/
│   ├── 1_Opportunity_Validator.py     # Stage 1: US ASIN → UK/EU niche mapping + Pursuit Score
│   ├── 2_Compliance_Compass.py        # Stage 2: Regulatory requirements + checklist generator
│   ├── 3_Risk_Pricing_Architect.py    # Stage 3: Launch price + PPC simulation + risk assessment
│   └── 4_Creative_Studio.py           # Stage 4: AI listings + images + A+ Content
│
├── services/
│   ├── __init__.py
│   ├── db_connection.py               # DSN resolution + psycopg helpers (shared core pattern)
│   ├── auth_manager.py                # Google AI auth (shared core pattern)
│   ├── marketplace_policy.py          # Extended: US→UK/EU mapping + existing normalization
│   ├── js_client.py                   # Jungle Scout client wrapper + per-app budget metering
│   ├── opportunity_scorer.py          # Pursuit Score engine (Saturated/Proven/Goldmine)
│   ├── compliance_engine.py           # CE/UKCA/WEEE/RoHS/DPP rules engine
│   ├── pricing_engine.py              # Launch price predictor + PPC simulator
│   ├── listing_generator.py           # RUFUS-optimized listing writer via Gemini
│   ├── image_generator.py             # AI product image generation via Gemini/Imagen 3
│   └── launch_state.py                # Per-launch state machine (stage progression tracking)
│
├── migrations/
│   ├── 001_launchpad_security.sql     # Schema + roles + cross-schema grants
│   ├── 002_launchpad_core_tables.sql  # Product launches, stages, niche mappings
│   ├── 003_launchpad_compliance.sql   # Compliance rules + checklists
│   ├── 004_launchpad_pricing.sql      # Pricing analysis + PPC simulation tables
│   ├── 005_launchpad_creative.sql     # Listing drafts + image gallery
│   └── 006_launchpad_api_budget.sql   # Separate budget tracking (own ledger)
│
├── scripts/
│   ├── seed_compliance_rules.py       # Seed CE/UKCA/WEEE/RoHS/DPP rule database
│   ├── validate_launchpad_access.sql  # Access verification (same pattern as amazon-mi)
│   └── api_budget_planner.py          # Offline budget modeling for launchpad operations
│
├── tests/
│   ├── test_opportunity_scorer.py
│   ├── test_compliance_engine.py
│   ├── test_pricing_engine.py
│   ├── test_listing_generator.py
│   └── test_db_connection.py
│
└── docs/
    ├── architecture.md
    └── compliance_rules_reference.md
```

---

## 5. Database Schema Design (launchpad schema)

### 5.1 Migration 001 — Security Foundation

```sql
-- launchpad security foundation (mirrors amazon-mi pattern from migrations/001_security_foundation.sql)

-- Schema
CREATE SCHEMA IF NOT EXISTS launchpad;

-- Roles
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'launchpad_admin') THEN
    CREATE ROLE launchpad_admin NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'launchpad_app') THEN
    CREATE ROLE launchpad_app LOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'launchpad_reader') THEN
    CREATE ROLE launchpad_reader LOGIN;
  END IF;
END $$;

-- Schema ownership and access
ALTER SCHEMA launchpad OWNER TO launchpad_admin;
GRANT USAGE ON SCHEMA launchpad TO launchpad_app, launchpad_reader;
GRANT CONNECT ON DATABASE amazon_dash TO launchpad_app, launchpad_reader;

-- Default privileges
ALTER DEFAULT PRIVILEGES FOR ROLE launchpad_admin IN SCHEMA launchpad
  GRANT SELECT, INSERT, UPDATE ON TABLES TO launchpad_app;
ALTER DEFAULT PRIVILEGES FOR ROLE launchpad_admin IN SCHEMA launchpad
  GRANT SELECT ON TABLES TO launchpad_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE launchpad_admin IN SCHEMA launchpad
  GRANT USAGE, SELECT ON SEQUENCES TO launchpad_app;

-- Cross-schema read access to market_intel
GRANT USAGE ON SCHEMA market_intel TO launchpad_app;
GRANT SELECT ON TABLE market_intel.niche_definitions TO launchpad_app;
GRANT SELECT ON TABLE market_intel.niche_competitors TO launchpad_app;
GRANT SELECT ON TABLE market_intel.competitor_sales_weekly TO launchpad_app;
GRANT SELECT ON TABLE market_intel.niche_keyword_bank TO launchpad_app;
GRANT SELECT ON TABLE market_intel.v_war_room_competitor_detail TO launchpad_app;
GRANT SELECT ON TABLE market_intel.v_niche_keyword_summary TO launchpad_app;
GRANT SELECT ON TABLE market_intel.competitor_price_daily TO launchpad_app;
GRANT SELECT ON TABLE market_intel.marketplace_lookup TO launchpad_app;
```

### 5.2 Migration 002 — Core Tables (Stage 1)

```sql
SET ROLE launchpad_admin;

-- Central launch entity
CREATE TABLE launchpad.product_launches (
    launch_id       BIGSERIAL PRIMARY KEY,
    source_asin     VARCHAR(20) NOT NULL,
    source_marketplace VARCHAR(5) NOT NULL DEFAULT 'US',
    target_marketplaces TEXT[] NOT NULL DEFAULT ARRAY['UK','DE','FR','IT','ES'],
    product_description TEXT,
    product_category    TEXT,
    pursuit_score       NUMERIC(5,2),
    pursuit_category    VARCHAR(20) CHECK (pursuit_category IN ('Saturated','Proven','Goldmine')),
    current_stage       SMALLINT NOT NULL DEFAULT 1 CHECK (current_stage BETWEEN 1 AND 4),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_launches_source_asin ON launchpad.product_launches(source_asin);
CREATE INDEX idx_launches_created ON launchpad.product_launches(created_at DESC);

-- Niche mapping (links to market_intel.niche_definitions via read)
CREATE TABLE launchpad.niche_mapping (
    mapping_id  BIGSERIAL PRIMARY KEY,
    launch_id   BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    niche_id    BIGINT NOT NULL,  -- FK target is market_intel.niche_definitions (cross-schema, logical only)
    marketplace VARCHAR(5) NOT NULL,
    confidence  NUMERIC(4,3),     -- 0.000–1.000 mapping confidence
    mapped_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (launch_id, niche_id, marketplace)
);

-- Review moat analysis
CREATE TABLE launchpad.review_moat_analysis (
    moat_id             BIGSERIAL PRIMARY KEY,
    launch_id           BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    marketplace         VARCHAR(5) NOT NULL,
    competitor_count    INTEGER,
    avg_review_count    NUMERIC(10,1),
    avg_rating          NUMERIC(3,2),
    review_velocity_30d NUMERIC(10,1),   -- new reviews/30d for top 10 competitors
    moat_strength       VARCHAR(10) CHECK (moat_strength IN ('Weak','Medium','Strong')),
    analyzed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (launch_id, marketplace)
);
```

### 5.3 Migration 003 — Compliance (Stage 2)

```sql
SET ROLE launchpad_admin;

-- Compliance rules reference (seeded by scripts/seed_compliance_rules.py)
CREATE TABLE launchpad.compliance_rules (
    rule_id                 BIGSERIAL PRIMARY KEY,
    regime                  VARCHAR(20) NOT NULL,  -- CE, UKCA, WEEE, RoHS, ToyEN71, DPP
    category_pattern        TEXT NOT NULL,          -- regex or keyword match against product category
    requirement_name        TEXT NOT NULL,
    requirement_description TEXT,
    documentation_required  TEXT[],                 -- e.g., ARRAY['Safety Data Sheet','Lab Test Report']
    is_2026_dpp_relevant    BOOLEAN NOT NULL DEFAULT FALSE,
    effective_date          DATE,
    source_url              TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_compliance_regime ON launchpad.compliance_rules(regime);

-- Per-launch compliance checklist
CREATE TABLE launchpad.launch_compliance_checklist (
    checklist_id    BIGSERIAL PRIMARY KEY,
    launch_id       BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    rule_id         BIGINT NOT NULL REFERENCES launchpad.compliance_rules(rule_id),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','in_progress','completed','not_applicable','blocked')),
    evidence_url    TEXT,
    notes           TEXT,
    completed_at    TIMESTAMPTZ,
    UNIQUE (launch_id, rule_id)
);
```

### 5.4 Migration 004 — Pricing & PPC (Stage 3)

```sql
SET ROLE launchpad_admin;

-- Pricing analysis
CREATE TABLE launchpad.pricing_analysis (
    pricing_id              BIGSERIAL PRIMARY KEY,
    launch_id               BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    marketplace             VARCHAR(5) NOT NULL,
    recommended_launch_price NUMERIC(10,2),
    price_floor             NUMERIC(10,2),
    price_ceiling           NUMERIC(10,2),
    margin_estimate_pct     NUMERIC(5,2),
    competitor_price_p25    NUMERIC(10,2),
    competitor_price_p50    NUMERIC(10,2),
    competitor_price_p75    NUMERIC(10,2),
    competitor_count        INTEGER,
    data_freshness_date     DATE,
    analyzed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (launch_id, marketplace)
);

-- PPC simulation per keyword
CREATE TABLE launchpad.ppc_simulation (
    sim_id                  BIGSERIAL PRIMARY KEY,
    launch_id               BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    marketplace             VARCHAR(5) NOT NULL,
    keyword                 TEXT NOT NULL,
    search_volume_exact     INTEGER,
    estimated_cpc           NUMERIC(8,2),
    estimated_acos_pct      NUMERIC(5,2),
    estimated_tacos_pct     NUMERIC(5,2),
    organic_rank_target     INTEGER,
    estimated_daily_spend   NUMERIC(10,2),
    estimated_days_to_page1 INTEGER,
    source_field            VARCHAR(30),  -- ppc_bid_exact or ppc_bid_broad from keyword_bank
    simulated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (launch_id, marketplace, keyword)
);

-- Risk assessment
CREATE TABLE launchpad.risk_assessment (
    risk_id         BIGSERIAL PRIMARY KEY,
    launch_id       BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    risk_category   VARCHAR(30) NOT NULL,  -- 'safety','fragility','IP','compliance','market'
    risk_description TEXT NOT NULL,
    severity        VARCHAR(10) CHECK (severity IN ('Low','Medium','High','Critical')),
    mitigation      TEXT,
    assessed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 5.5 Migration 005 — Creative (Stage 4)

```sql
SET ROLE launchpad_admin;

-- Listing drafts (versioned)
CREATE TABLE launchpad.listing_drafts (
    draft_id            BIGSERIAL PRIMARY KEY,
    launch_id           BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    marketplace         VARCHAR(5) NOT NULL,
    version             INTEGER NOT NULL DEFAULT 1,
    title               TEXT,
    bullets             JSONB,          -- array of 5 bullet strings
    description         TEXT,
    backend_keywords    TEXT,           -- 250 byte limit for Amazon
    rufus_optimized     BOOLEAN NOT NULL DEFAULT FALSE,
    a_plus_content      JSONB,          -- structured A+ Content modules
    generated_by        VARCHAR(50),    -- model name (e.g., 'gemini-2.0-flash')
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (launch_id, marketplace, version)
);

-- Image gallery
CREATE TABLE launchpad.image_gallery (
    image_id        BIGSERIAL PRIMARY KEY,
    launch_id       BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    slot_number     SMALLINT NOT NULL CHECK (slot_number BETWEEN 1 AND 7),
    image_type      VARCHAR(20) NOT NULL
                    CHECK (image_type IN ('main_white_bg','lifestyle','infographic','comparison','dimensions','packaging','in_use')),
    prompt_used     TEXT,
    storage_path    TEXT,               -- local or S3 path
    model_used      VARCHAR(50),        -- e.g., 'imagen-3'
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (launch_id, slot_number)
);
```

### 5.6 Migration 006 — API Budget (Separate from amazon-mi)

```sql
SET ROLE launchpad_admin;

-- Launchpad's own API call ledger (mirrors market_intel.api_call_ledger design)
CREATE TABLE launchpad.api_call_ledger (
    ledger_id       BIGSERIAL PRIMARY KEY,
    called_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    script_name     TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    marketplace     VARCHAR(5),
    billable_pages  INTEGER NOT NULL DEFAULT 1,
    launch_id       BIGINT REFERENCES launchpad.product_launches(launch_id),
    metadata        JSONB
);

CREATE INDEX idx_ledger_month ON launchpad.api_call_ledger (date_trunc('month', called_at));

-- Budget config (single-row, same pattern as market_intel.budget_config)
CREATE TABLE launchpad.budget_config (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    monthly_hard_cap    INTEGER NOT NULL DEFAULT 500,
    allow_override      BOOLEAN NOT NULL DEFAULT FALSE,
    override_reason     TEXT
);

INSERT INTO launchpad.budget_config (id, monthly_hard_cap) VALUES (1, 500);

-- Revoke destructive ops on budget_config
REVOKE INSERT, DELETE ON launchpad.budget_config FROM launchpad_app;

-- Budget status view
CREATE VIEW launchpad.v_api_budget_status AS
SELECT
    date_trunc('month', CURRENT_DATE)::DATE AS month_start,
    COALESCE(SUM(l.billable_pages), 0)      AS total_billable_pages,
    bc.monthly_hard_cap,
    bc.monthly_hard_cap - COALESCE(SUM(l.billable_pages), 0) AS remaining_budget,
    bc.allow_override,
    bc.override_reason
FROM launchpad.budget_config bc
LEFT JOIN launchpad.api_call_ledger l
    ON l.called_at >= date_trunc('month', CURRENT_DATE)
    AND l.called_at < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
WHERE bc.id = 1
GROUP BY bc.monthly_hard_cap, bc.allow_override, bc.override_reason;
```

---

## 6. Stage Implementation Details

### Stage 1 — Opportunity Validator

| Aspect | Detail |
|--------|--------|
| **Feasibility** | ✅ HIGH |
| **Input** | US ASIN or free-text product description |
| **Data sources** | Jungle Scout `product_database` + `sales_estimates` + `keywords_by_asin`; `market_intel.niche_definitions`, `market_intel.niche_competitors`, `market_intel.niche_keyword_bank` |
| **Processing** | (1) Look up US ASIN via JS product_database to get category + attributes. (2) Map to UK/EU niche using category matching against existing `niche_definitions`. (3) Pull competitor data for matched niche. (4) Calculate Pursuit Score. |
| **Pursuit Score formula** | Weighted composite of: competitor count (fewer = better), avg review count (lower moat = better), sales velocity (higher = better), keyword difficulty (lower = better). Thresholds: <40 = Saturated, 40-70 = Proven, >70 = Goldmine. |
| **Output** | `product_launches` row, `niche_mapping` rows, `review_moat_analysis` rows, Pursuit Score + category |
| **External deps** | Jungle Scout API (existing), optionally Amazon Product Advertising API for US→UK ASIN cross-reference |

### Stage 2 — Compliance Compass

| Aspect | Detail |
|--------|--------|
| **Feasibility** | ⚠️ MEDIUM |
| **Input** | Product category + attributes from Stage 1 |
| **Data sources** | `launchpad.compliance_rules` (seeded rules engine), product category from Stage 1 |
| **Processing** | (1) Match product category against `compliance_rules.category_pattern`. (2) Identify applicable regimes (CE/UKCA/WEEE/RoHS/Toy Safety/DPP). (3) Generate checklist with required documentation. (4) Flag 2026 DPP requirements. |
| **Output** | `launch_compliance_checklist` rows with status tracking |
| **External deps** | None (rules engine is local). No public API exists for Amazon MYC or CE/UKCA databases — compliance rules are curated manually from official regulation texts and seeded via `scripts/seed_compliance_rules.py`. |
| **Key risk** | Rules require manual maintenance as regulations change. DPP (EU Digital Product Passport) requirements are still evolving for 2026. |

### Stage 3 — Risk & Pricing Architect

| Aspect | Detail |
|--------|--------|
| **Feasibility** | ✅ HIGH |
| **Input** | Niche mapping + competitor data from Stage 1 |
| **Data sources** | `market_intel.competitor_price_daily`, `market_intel.competitor_sales_weekly`, `market_intel.niche_keyword_bank` (ppc_bid_exact, ppc_bid_broad fields), Jungle Scout `sales_estimates` |
| **Processing** | (1) Aggregate competitor prices → compute percentiles (p25/p50/p75). (2) Model launch price as a function of positioning strategy. (3) Pull PPC bid data for top keywords → estimate ACOS/TACoS and daily spend to reach Page 1. (4) LLM-powered risk assessment from product description + category. |
| **Output** | `pricing_analysis` rows, `ppc_simulation` rows per keyword, `risk_assessment` rows |
| **External deps** | Jungle Scout API (existing), Gemini for risk narrative generation (existing auth) |

### Stage 4 — Creative Studio

| Aspect | Detail |
|--------|--------|
| **Feasibility** | ⚠️ MEDIUM-HIGH |
| **Input** | All prior stage data + user refinements |
| **Data sources** | Gemini text generation (listing copy), Gemini/Imagen 3 (product images), competitor listings for A+ Content patterns |
| **Processing** | (1) Generate Title + 5 Bullets + Description optimized for Amazon RUFUS (AI-native search). RUFUS optimization means: natural language over keyword stuffing, question-answer patterns, explicit attribute mentions, comparison-friendly language. (2) Generate 7-image gallery (main white-bg, lifestyle, infographic, comparison, dimensions, packaging, in-use). (3) Design A+ Content blueprint with comparison table + Brand Story layout. |
| **Output** | `listing_drafts` rows (versioned), `image_gallery` rows with storage paths |
| **External deps** | Google Generative AI / Imagen 3 API for images (existing auth, new endpoint), Gemini for text (existing) |
| **Key risk** | AI-generated product images may not meet Amazon's image quality requirements for main photos (white background, 1000px minimum, no text). Lifestyle images are more forgiving. Recommend human review before upload. |

---

## 7. Environment Variables (.env contract)

```bash
# ===== Database Connection =====
DB_HOST=192.168.0.110
DB_PORT=5433
DB_NAME=amazon_dash
DB_SSLMODE=disable

# Primary DSN (URL-encoded password required for special chars)
LAUNCHPAD_DB_DSN=postgresql://launchpad_app:<PASSWORD>@192.168.0.110:5433/amazon_dash?sslmode=disable

# Fallback DSNs (same pattern as amazon-mi)
MARKET_INTEL_DSN=postgresql://market_intel_writer:<PASSWORD>@192.168.0.110:5433/amazon_dash?sslmode=disable
PG_DSN=postgresql://amazon_dash_user:<PASSWORD>@192.168.0.110:5433/amazon_dash?sslmode=disable

# Permission note: amazon_dash_user is the elevated DB account for this project.
# Use PG_DSN (amazon_dash_user) for migrations/DDL and permission-sensitive operations.
# launchpad_app remains the standard runtime app role for everyday Launchpad reads/writes.

# ===== Jungle Scout =====
JUNGLESCOUT_API_KEY_NAME=<YOUR_KEY_NAME>
JUNGLESCOUT_API_KEY=<YOUR_API_KEY>
JUNGLESCOUT_MAX_RETRIES=5
JUNGLESCOUT_MAX_API_CALLS=50  # per-run safety cap

# ===== Google Generative AI =====
GOOGLE_SERVICE_ACCOUNT_JSON=gen-lang-client-0422857398-6a11b7435ae6.json

# ===== Launchpad-Specific =====
LAUNCHPAD_DATA_MODE=db            # db or mock
LAUNCHPAD_MONTHLY_API_BUDGET=500  # separate from amazon-mi's 1000
LAUNCHPAD_LOG_DIR=/root/amazon-launchpad/logs

# ===== Marketplace Policy =====
# US is INPUT ONLY (for source ASIN lookup)
# All persisted/analyzed data targets UK/EU
MARKETPLACE_TARGETS=UK,DE,FR,IT,ES
```

---

## 8. Migration Strategy

All migrations follow amazon-mi's established patterns:

| Pattern | amazon-mi Reference | Launchpad Equivalent |
|---------|---------------------|---------------------|
| Security foundation | `migrations/001_security_foundation.sql` | `migrations/001_launchpad_security.sql` |
| Role creation | `CREATE ROLE IF NOT EXISTS` via DO $$ block | Same |
| Schema ownership | `ALTER SCHEMA ... OWNER TO ...` | Same |
| Least-privilege grants | `ALTER DEFAULT PRIVILEGES` | Same |
| Access verification | `scripts/verify_access.sql` | `scripts/validate_launchpad_access.sql` |
| Budget tracking | `market_intel.api_call_ledger` + `budget_config` | `launchpad.api_call_ledger` + `launchpad.budget_config` |
| Single-row config | `CHECK (id = 1)` pattern | Same |
| View rebuilds | `DROP VIEW IF EXISTS ... CREATE VIEW ...` | Same |

**Migration execution** (from app host 192.168.0.121):
```bash
# Load env
set -a; source .env; set +a

# Apply migrations in order
psql "$LAUNCHPAD_DB_DSN" -v ON_ERROR_STOP=1 -f migrations/001_launchpad_security.sql
psql "$LAUNCHPAD_DB_DSN" -v ON_ERROR_STOP=1 -f migrations/002_launchpad_core_tables.sql
# ... etc
```

---

## 9. Requirements.txt

```
# === Shared with amazon-mi (match versions for compatibility) ===
streamlit>=1.38,<2              # Dashboard framework
plotly>=5.24,<6                 # Interactive visualizations
psycopg[binary]>=3.2,<4        # PostgreSQL driver (psycopg3)
junglescout-client>=0.6,<1     # Jungle Scout API client
python-amazon-sp-api>=1.8,<2   # Amazon SP-API (product lookup)
google-generativeai>=0.8,<1    # Gemini text generation + Imagen image generation
google-auth>=2.35,<3           # Service account authentication
python-dotenv>=1.0.0           # .env file loading

# === Launchpad-specific ===
# (Add as needed during implementation — keep minimal)
# pillow>=10.0                  # Image processing if needed for gallery management
# boto3>=1.34                   # S3 upload for generated images (if using S3 storage)
```

---

## 10. Systemd Service

File: `/etc/systemd/system/streamlit-launchpad.service`

```ini
[Unit]
Description=Amazon Product Launchpad (Streamlit)
After=network-online.target
Wants=network-online.target
StartLimitBurst=5
StartLimitIntervalSec=300

[Service]
Type=simple
User=root
WorkingDirectory=/root/amazon-launchpad
EnvironmentFile=/root/amazon-launchpad/.env
EnvironmentFile=-/root/amazon-launchpad/.env.app
Environment=LAUNCHPAD_DATA_MODE=db
Environment=PYTHONPATH=/root/amazon-launchpad

ExecStart=/usr/local/bin/streamlit run app.py

Restart=on-failure
RestartSec=15

StandardOutput=append:/var/log/streamlit-launchpad.log
StandardError=append:/var/log/streamlit-launchpad.log

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/root/amazon-launchpad/logs /var/log
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

**Activation:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable streamlit-launchpad.service
sudo systemctl start streamlit-launchpad.service
sudo systemctl status streamlit-launchpad.service
```

**Streamlit config** (`.streamlit/config.toml`):
```toml
[server]
# Port 8501 = BI Dashboard (RESERVED)
# Port 8502 = Market Intelligence (amazon-mi)
# Port 8503 = Product Launchpad (this app)
port = 8503
address = "0.0.0.0"
headless = true
enableCORS = true
enableXsrfProtection = true
```

---

## 11. Key Architectural Rules

### Hard Rules (NEVER violate)

1. **Zero-write to market_intel**: Launchpad NEVER writes to `market_intel` schema. Read-only cross-schema access via explicit GRANT SELECT on named objects.

2. **Separate API budgets**: Launchpad tracks its own JS API usage in `launchpad.api_call_ledger`. It MUST NOT consume amazon-mi's 1000 calls/month allocation.

3. **Port discipline**: 8501 = BI (reserved), 8502 = MI, 8503 = Launchpad. Never change these.

4. **US is input-only**: US marketplace data is accepted as INPUT (source ASIN lookup in Stage 1). All persisted entities, analysis, and outputs target UK/EU marketplaces only.

5. **Separate dependencies**: Own `requirements.txt`, own lockfiles, own release cadence. A Launchpad dependency upgrade must never break amazon-mi.

6. **Separate systemd service**: Own unit file, own logs, own restart policy. The two apps have independent lifecycles.

### Soft Rules (follow unless there's good reason not to)

7. **Shared package extraction**: When amazon-tools-core is extracted, both apps should depend on it. Until then, copy utilities and accept the DRY violation.

8. **Deep-link, don't merge**: If unified UX is needed, add hyperlinks between the two apps (e.g., "View in Market Intelligence →"). Do NOT merge pages into one Streamlit app.

9. **Same auth perimeter**: Both apps share Cloudflare Zero Trust with Google SSO. Same user allowlist.

10. **Same DB conventions**: URL-encoded DSNs, role injection, `sslmode=disable`, three-tier fallback, `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` for read-only services.

---

## Appendix A — Cross-Reference to amazon-mi

| amazon-mi Component | Launchpad Equivalent | Sharing Model |
|---------------------|---------------------|---------------|
| `services/marketplace_policy.py` | `services/marketplace_policy.py` | Copy + extend (add US→UK/EU mapping) |
| `services/auth_manager.py` | `services/auth_manager.py` | Copy verbatim (same pattern) |
| `services/api_budget_guard.py` | `services/js_client.py` | Adapted (own schema, own ledger) |
| `services/db_market_intel_service.py` | `services/db_connection.py` | New (lighter, launch-focused) |
| `migrations/001_security_foundation.sql` | `migrations/001_launchpad_security.sql` | Same pattern, different roles/schema |
| `market_intel.api_call_ledger` | `launchpad.api_call_ledger` | Same table design, different schema |
| `market_intel.budget_config` | `launchpad.budget_config` | Same pattern (CHECK id=1) |
| `.streamlit/config.toml` (port 8502) | `.streamlit/config.toml` (port 8503) | Same structure, different port |
| `streamlit-mi-dashboard.service` | `streamlit-launchpad.service` | Same template, different paths/port |

## Appendix B — Feasibility Summary

| Stage | Feasibility | Data Overlap with amazon-mi | New APIs/Services | Estimated Effort |
|-------|-------------|---------------------------|-------------------|-----------------|
| 1. Opportunity Validator | ✅ HIGH | ~80% (competitors, keywords, sales) | Minimal (US ASIN lookup) | 3-5 days |
| 2. Compliance Compass | ⚠️ MEDIUM | ~5% (product category only) | Rules engine (built, not API) | 5-8 days |
| 3. Risk & Pricing | ✅ HIGH | ~70% (pricing, keywords, PPC bids) | Gemini for risk narrative | 3-5 days |
| 4. Creative Studio | ⚠️ MEDIUM-HIGH | ~10% (keyword data for SEO) | Gemini text + Imagen 3 images | 5-8 days |
| **Total** | | | | **16-26 days** |
