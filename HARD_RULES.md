# Amazon Launchpad - Hard Architectural Rules

## Port Allocation (CRITICAL)

### Reserved Ports (NEVER TOUCH)
- **Port 8501**: RESERVED for Amazon BI Dashboard
  - Status: PRODUCTION
  - Owner: Amazon BI Team
  - NEVER serve Launchpad on this port
  
- **Port 8502**: RESERVED for amazon-mi (Market Intelligence)
  - Status: PRODUCTION  
  - Owner: Market Intelligence Team
  - NEVER serve Launchpad on this port

### Launchpad Port
- **Port 8503**: DEDICATED to Amazon Launchpad
  - Status: DEDICATED
  - Owner: Launchpad Application
  - ONLY port for Launchpad Streamlit server

## Enforcement

These rules are enforced by:
1. `.streamlit/config.toml` - Explicit port 8503
2. This document - Hard rule declaration
3. Cloudflare tunnel configuration - Routes to localhost:8503
4. Systemd service files - Bind to port 8503 only

## Violation Consequences

Using ports 8501 or 8502 for Launchpad will:
- Conflict with existing production services
- Cause port binding errors
- Potentially disrupt BI and MI dashboards
- Violate architectural isolation principles

## Verification Command

Before starting Launchpad, verify port availability:
```bash
# Check if 8503 is free
lsof -i :8503

# Check that 8501 and 8502 are occupied (by other services)
lsof -i :8501
lsof -i :8502
```

## Hard Rule Summary

> **PORT 8501 and 8502 are FORBIDDEN for Launchpad.**
> 
> **ONLY use Port 8503.**

This rule is non-negotiable and has no exceptions.

## Operational Safety (Port Ownership)

- Launchpad operators and automation must **never** stop, restart, or modify services bound to ports 8501 or 8502.
- If 8501 or 8502 is in use, treat that as expected production ownership by BI/MI teams, not an error to fix from Launchpad.
- Launchpad actions are limited to port 8503 and Launchpad-owned processes only.
- During troubleshooting, report conflicts on 8501/8502 for visibility, but do not suggest or perform shutdown actions on those ports.

## Jungle Scout API Cost-Control Rules (CRITICAL)

Because Jungle Scout calls are billable, all API usage in Launchpad must be cost-aware, cache-first, and persistence-first.

### Billing and budget model

1. **Billable unit = response page**
   - For paginated endpoints, each page consumed is one billable API call.
   - Non-paginated endpoints are one call per request.

2. **Enforce budget before outbound calls**
   - Every live call must pass budget checks first.
   - Launchpad metering and audit must stay in `launchpad.api_call_ledger` and `launchpad.v_api_budget_status`.
   - Launchpad code must never meter calls against `market_intel.*` tables.

3. **Per-run guardrails are mandatory**
   - Use `JUNGLESCOUT_MAX_API_CALLS` for smoke tests and setup runs.
   - Default to conservative test budgets to avoid accidental burn.

### Efficiency rules (required)

4. **Every call must be intentional**
   - No exploratory/redundant calls if cache or stored payload can answer the question.
   - Prefer high-value pulls that support multiple downstream uses.

5. **Maximize payload per paid call**
   - Use maximum supported page sizes where valid for each endpoint.
   - Avoid narrow windows when broader windows have the same call cost.
   - For `sales_estimates`, request the maximum useful date range per call.

6. **Use endpoint batching where supported**
   - For endpoints that allow multi-ASIN requests, batch to endpoint limits before making additional calls.

7. **Pagination discipline**
   - Follow `links.next` rather than constructing next-page URLs manually.
   - Reuse the same request body for paginated POST collection requests.

### Persistence and data reuse rules

8. **Persist all useful returned data**
   - Do not discard useful fields from paid responses.
   - Store full response payloads (or a lossless normalized representation) so paid data can be reused.

9. **Cache-first is non-optional**
   - Check cache before live calls where supported.
   - Use stable request keys based on canonicalized params.
   - Choose TTLs by endpoint volatility; avoid unnecessary refresh churn.

10. **No duplicate spend for the same question**
    - Before issuing a call, verify whether existing cached/stored data already satisfies the use case.
    - If yes, reuse existing data.

### Reliability and safety rules

11. **Retry only transient failures**
    - Retry on `429`, `5xx`, and transport timeout/network failures using exponential backoff with jitter.
    - Fail fast on non-429 `4xx` errors (configuration/request issue).

12. **Treat API outputs correctly**
    - Present JS sales values as estimates (directional, not exact accounting).
    - Preserve parent/variant semantics; do not misrepresent parent-level estimates as variant-specific actuals.

13. **Secrets and logs**
    - Never commit or print API secrets.
    - Never log authorization headers or full sensitive request objects.

### Launchpad scope adaptation

14. **Apply rules to Launchpad architecture**
    - These rules are adapted from cross-project guidance; Launchpad implementations must follow Launchpad schema/contracts.
    - Where upstream guidance references other project tables (for example `market_intel_raw.*`), use Launchpad equivalents and keep behaviorally equivalent guarantees (idempotency, reuse, auditability).

### Enforcement guidance

- PRs touching Jungle Scout integration must state:
  - why each live call is necessary,
  - page-size/date-range/batching choices,
  - what persistence layer receives the payload,
  - how cache keying and TTL avoid duplicate spend,
  - how budget checks and failure modes are enforced.
- Any code path that pays for data and discards reusable output is a rules violation.
