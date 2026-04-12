# Jungle Scout API — Firm Project Rules

*Collated from: `skill.md`, `docs/junglescout_api_reference.md`, `docs/junglescout_support_knowledge_base.md`, `docs/ops_runbook.md`, `HANDOFF.md`, `IMPLEMENTATION_HANDOFF_BUDGET_OVERRIDE.md`, `services/api_budget_guard.py`, `scripts/ingest_junglescout.py`, `scripts/api_budget_planner.py`, and implementation scripts.*

---

## 1. What Constitutes a Single Billable API Call

**Core rule: Each page of an API response equals exactly one request against the monthly quota.**

This was confirmed directly with Jungle Scout support (2026-02-15) and is the foundational budget planning assumption across all project documentation.

| Endpoint | Paginated? | What = 1 Billable Call | Batch Support |
|---|---|---|---|
| **`sales_estimates`** | No — daily data is nested in `attributes.data[]` | **1 call per ASIN regardless of date range** (up to 366 days of daily data in one call) | None — 1 ASIN per call only |
| **`keywords_by_asin`** | Yes — keywords are top-level `data[]` items | 1 call per page of results | Up to **10 ASINs per single call** |
| **`keywords_by_keyword`** | Yes | 1 call per page of results | N/A |
| **`product_database`** | Yes — ASINs are top-level `data[]` items | 1 call per page of results | N/A |
| **`share_of_voice`** | No — single object response | 1 call per keyword | None — 1 keyword per call only |
| **`historical_search_volume`** | Yes | 1 call per page of results | N/A |

**Concrete examples from Jungle Scout:**
- Product Database query returning 2,000 results at `page[size]=100` → **20 billable calls**
- Sales Estimates for 1 ASIN spanning a full year → **1 billable call**
- Keywords by ASIN returning 500 keywords at `page[size]=100` → **5 billable calls**

---

## 2. Budget Caps and Monthly Accounting

| Rule | Detail | Source |
|---|---|---|
| **Default hard cap** | **1,000 API calls/month** | `api_budget_guard.py`: `DEFAULT_MONTHLY_API_HARD_CAP = 1000` |
| **Override mechanism** | Cap above 1,000 only with `ALLOW_API_BUDGET_OVERRIDE=true` AND non-empty `API_BUDGET_OVERRIDE_REASON` | `api_budget_guard.py`, `ops_runbook.md` |
| **Override audit** | Reason is mandatory, logged in `api_call_ledger`, not persisted across sessions | `IMPLEMENTATION_HANDOFF_BUDGET_OVERRIDE.md` |
| **Pre-call enforcement** | `enforce_hard_cap()` checks `used_calls + planned_new_calls ≤ monthly_hard_cap` before every outbound request | `api_budget_guard.py` line 119-132 |
| **Budget event recording** | Every outbound call is logged to `market_intel.api_call_budget_events` with `event_date`, `endpoint`, `job_name`, `calls` count, and `note` | `api_budget_guard.py`, `ingest_junglescout.py` |
| **Monthly call-plan formula** | `projected_monthly_calls = daily_core_calls × days_in_month + exploration_reserve_calls + retry_buffer_calls` → must be ≤ 1,000 | `ops_runbook.md` |
| **Per-run hard cap** | `JUNGLESCOUT_MAX_API_CALLS` env var — stops the script before the next API request would exceed the cap | All ingestion scripts |
| **Setup recommendation** | Set `JUNGLESCOUT_MAX_API_CALLS=100` during setup/smoke/backfill to prevent accidental over-consumption | `ops_runbook.md`, `skill.md` |
| **Preflight estimation** | Required before enabling any API mode via `scripts/api_budget_planner.py` | `ops_runbook.md` |

**Budget allocation example (1,000/month, 385 competitors, 15 niches, 71 our ASINs):**

| Activity | Frequency | Est. Requests | Notes |
|---|---|---|---|
| Competitor sales (30-day range) | Monthly | ~385 | 1 call/ASIN |
| Weekly sales top-up (7-day range) | Weekly | ~385 | New/changed only |
| Competitor discovery | Monthly | ~15-30 | 1-2 pages/niche at `page[size]=100` |
| Keyword harvest | Monthly | ~24-72 | 8 batches of 10 ASINs × 1-3 pages |
| Ad-hoc / Share of Voice | As needed | ~100-200 | Exploration reserve |
| **Total monthly** | | **~500-700** | Leaves 300-500 buffer |

---

## 3. Maximising Data Per Call (Efficiency Rules)

### 3.1 Page Size Optimisation
- **`product_database`**: Default is 10 (per API), but the API supports max **100**. Project rule: **always set `page[size]=100`** (implemented as `DEFAULT_PAGE_SIZE = 100` in `discover_competitors.py`). This gives 10× more data per billable call.
- **`keywords_by_asin` and `keywords_by_keyword`**: API default is 50, max is 50. Set `page[size]=50`.
- **`ingest_junglescout.py`**: Default page size is **100** (`JUNGLESCOUT_PAGE_SIZE=100`).

### 3.2 ASIN Batching
- **`keywords_by_asin` accepts up to 10 ASINs per single call** — always batch to minimise calls. The project's `harvest_niche_keywords.py` batches ASINs in groups of 10 for keyword harvesting.
- **`sales_estimates` is 1 ASIN per call** — no batching available; this is the most budget-expensive endpoint.

### 3.3 Date Range Maximisation
- **`sales_estimates`**: 1 call per ASIN regardless of date range (up to 366 days). Project rule: **always request the maximum useful date range** (default changed from 30 days to **90 days** — same API cost, 3× more data). The `fetch_competitor_sales.py` default is 90 days.
- **`historical_search_volume`**: Batch date ranges to minimise calls.
- End date must be before current date (yesterday or earlier) — the script auto-caps `week_end` to `TODAY - 1 day`.

### 3.4 Free Data Extraction
- **All fields in an API response are free** — the cost is per page, not per field. Project rule: capture every available field from each response:
  - `product_database`: 11 additional fields (category, seller_type, variants, image_url, weight, subcategory_ranks, fee_breakdown, listing_quality_score, date_first_available, buy_box_owner, number_of_sellers)
  - `sales_estimates`: Extract daily prices from `last_known_price` (free) + structural fields (is_parent, variants, parent_asin)
  - `keywords_by_asin`: 8 additional fields (competitor_ranks_json, avg_competitor_organic_rank, avg_competitor_sponsored_rank, sponsored_product_count, sp_brand_ad_bid, overall_rank, relative_organic/sponsored_position)

### 3.5 Call Discipline
- **Calls must be deliberate and dense**: avoid duplicate/redundant endpoint requests and maximise payload per call.
- **Avoid unnecessary pagination**: maximize `page[size]` to reduce total pages.
- **Do not manually build the next URL**: follow `links.next` from the response.
- **For POST collection endpoints, reuse the same request body** when paginating.

---

## 4. Pagination Model

| Aspect | Rule |
|---|---|
| **Type** | Cursor-based pagination |
| **Request params** | `page[size]` (records per page) + `page[cursor]` (cursor for next page) |
| **Response hints** | `meta.total_items` + `links.next` (full URL for next request) |
| **Cursor extraction** | Parse `page[cursor]` query parameter from `links.next` URL |
| **Following pages** | Do **not** manually construct the next URL; follow `links.next` exactly |
| **POST pagination** | Reuse the identical request body when following pagination on POST endpoints |
| **Cursor persistence** | Checkpoint stored in `market_intel_raw.js_ingest_cursor` for resumable ingestion |
| **Non-paginated endpoints** | `sales_estimates` and `share_of_voice` return all data in a single response |

---

## 5. Rate Limits, Retry, and Backoff

| Rule | Detail |
|---|---|
| **API throttling** | 300 requests/minute or 15 requests/second per account |
| **429 handling** | HTTP 429 = rate-limited; retry with backoff |
| **Retry scope** | Only retry on transient failures: `429`, `5xx`, transport/timeout errors |
| **Non-retriable** | `4xx` errors (except 429) fail fast with no retry loop — fix config/secrets first |
| **Backoff algorithm** | Exponential backoff with jitter: `(base × 2^attempt) + random(0, 0.5)` |
| **Backoff base** | `JUNGLESCOUT_BACKOFF_BASE_SECONDS` default = **1.0s**, doubling per attempt |
| **Max retries** | `JUNGLESCOUT_MAX_RETRIES` default = **5** |
| **Error response format** | Root `errors` array; each error has `title`, `detail`, `code`, `status` |
| **422 MISSING_RANK_DATA** | Expected for newly listed or delisted ASINs — not retriable, logged and skipped |

---

## 6. Request and Response Structure (JSON:API)

| Aspect | Rule |
|---|---|
| **Auth headers** | `Authorization: KEY_NAME:API_KEY`, `X-API-Type: junglescout`, `Accept: application/vnd.junglescout.v1+json`, `Content-Type: application/vnd.api+json` |
| **Marketplace param** | Required `marketplace` query parameter on every request |
| **POST request body** | Wrapped with root `data` object containing `attributes` |
| **Success response** | Root `data` (single object or array), with `type`, `id`, `attributes` per resource |
| **Collection extras** | `meta` (e.g. `total_items`), `links` (`self`, `next`) |
| **Error response** | Root `errors` array |

---

## 7. Data Storage and Idempotency

| Rule | Detail |
|---|---|
| **Raw landing** | All API responses land in `market_intel_raw.js_api_records_raw` with `source`, `api_endpoint`, `record_identity`, `record_hash`, `raw_payload` (JSONB) |
| **Record identity** | Computed as `{api_endpoint}:{data.type}:{data.id}` (preferred) or hash-based fallback |
| **Record hash** | SHA-256 of canonical JSON (sorted keys, compact separators) |
| **Upsert logic** | `ON CONFLICT (source, api_endpoint, record_identity) DO UPDATE ... WHERE record_hash IS DISTINCT FROM EXCLUDED.record_hash` — skip write if payload unchanged |
| **Idempotent ingestion** | Stable record identity + hash + cursor persistence ensures reruns are safe |
| **Curated layer** | `scripts/transform_raw_to_curated.py` transforms raw → curated tables with upsert keys `(source, api_endpoint, source_record_identity)` |
| **Dead letter queue** | Failed records go to `market_intel_raw.js_dead_letter` with `error_stage`, `error_message`, `retryable` flag |
| **Batch tracking** | Every ingestion run creates a `js_ingest_batch` row with `batch_id`, `status`, `pages_fetched`, `records_received`, `records_loaded` |

---

## 8. Marketplace Scope Lock

- **Allowed**: UK + EU only (UK, DE, FR, IT, ES, NL, SE, PL)
- **Excluded**: US is **explicitly excluded by default**
- **Guard**: `JUNGLESCOUT_SETUP_MARKETPLACE_ONLY` fails fast if configured marketplace doesn't match guard value
- **UK → GB mapping**: Automatically handled when the SDK enum requires `GB` instead of `UK`

---

## 9. No-Backfill Operating Policy

- **No historical backfills** in normal operating mode.
- Organic daily fill only — one cycle per day.
- `JUNGLESCOUT_START_CURSOR`, `JUNGLESCOUT_START_DATE`, and `JUNGLESCOUT_END_DATE` are **blocked** in the standard ingestion script (`enforce_no_backfill_policy()`).
- Exception: `COMPETITOR_SNAPSHOT_DATE` may only be used with `scripts/load_competitor_snapshots.py --source-mode seed_from_existing` for internal seed loads.
- Reserve monthly headroom for niche exploration and incident retries.

---

## 10. Safety and Security Rules

1. **Never** commit or print API secrets (`JUNGLESCOUT_API_KEY_NAME`, `JUNGLESCOUT_API_KEY`, DSNs with credentials).
2. **Never** log authorization headers or full raw request objects that could include sensitive values.
3. Use `.env` files locally; keep secrets out of tracked docs, examples, and commits.
4. Respect call budgets during setup and testing by setting `JUNGLESCOUT_MAX_API_CALLS`.
5. Preserve idempotent ingestion behavior (stable record identity/hash and cursor persistence) when changing ingestion logic.
6. Present Jungle Scout sales data as **estimates** ("Est. Weekly Units"), not exact figures — JS explicitly states these are "directional insights rather than precise accounting".
7. Variant ASINs return **parent ASIN aggregated sales**, not individual variant sales — flag parent ASINs so users understand that variant ASIN estimates represent parent totals.
