# AGENTS.md

Project-specific operating guide for OpenCode agents working in `amazon-launchpad`.

## 1) Scope and intent

- Optimize for the **current codebase** in this repository, not speculative/planned architecture.
- Primary app shape: Streamlit multi-page workflow for Amazon launch execution.
- Make safe, minimal, reversible changes unless the user asks for a broader refactor.

## 2) Project snapshot (current state)

- Entrypoint: `app.py`
- UI pages: `pages/0_ASIN_Importer.py`, `pages/1_Opportunity_Validator.py`, `pages/2_Compliance_Compass.py`, `pages/3_Risk_Pricing_Architect.py`, `pages/4_Creative_Studio.py`, `pages/5_Creative_Images.py`, `pages/6_Aplus_Studio.py`
- Service layer: `services/*.py` (business logic, DB helpers, external API wrappers)
- DB migrations: `migrations/*.sql`
- Tests: `tests/*.py`
- Runtime config: `.streamlit/config.toml`, `.env` (gitignored), `opencode.json`

## 3) Non-negotiable guardrails

### Ports and runtime isolation

- `8501` and `8502` are reserved for other production dashboards.
- Launchpad must run on `8503` by default.
- Never stop/restart/modify processes that own ports `8501` or `8502`.

### Database boundaries

- Launchpad writes belong in `launchpad.*` objects.
- Launchpad must not meter API usage in `market_intel.api_call_ledger`.
- Treat `market_intel.*` as read-only integration data unless explicitly told otherwise.

### API cost control

- Follow cache-first behavior for Jungle Scout paths.
- Reuse cached/persisted data before making new live calls.
- Keep budget checks in place before billable API calls.

### Secrets and sensitive data

- Never commit `.env`, service account keys, credentials, or raw secrets.
- Never print secrets in terminal output, logs, or diffs.

## 4) Editing policy

- Prefer focused patches over broad rewrites.
- Preserve existing module boundaries (pages call services; services do not depend on pages).
- Keep naming and style consistent with nearby code.
- Add comments only when logic is non-obvious.
- Do not introduce new dependencies unless clearly justified by the task.

## 5) Verification policy (default)

Default expectation is **targeted tests first**.

- If editing cache/client behavior: run `pytest tests/test_js_client_cache.py`
- If editing compliance profile/rules matching: run `pytest tests/test_compliance_scope.py tests/test_ingredient_compliance.py`
- If editing pricing/economics logic: run `pytest tests/test_opportunity_economics.py`
- If editing creative/listing/image logic: run `pytest tests/test_creative_gallery.py tests/test_listing_policy.py tests/test_imagen_quota.py`
- If edits span multiple domains or shared infra: run `pytest tests`

If execution is blocked (missing env vars, DB/API credentials, unavailable services), report exactly what was blocked and why.

## 6) DB and migration safety defaults

- **Do not run migrations or seed scripts unless the user explicitly asks.**
- **Do not modify production DB data by default.**
- For DB-related code changes, prefer static/code-level validation and relevant unit tests first.
- When DB execution is requested, state the target DSN/role assumptions clearly before running commands.

## 7) Git behavior defaults

- Never commit unless the user explicitly asks for a commit.
- Never amend commits unless explicitly requested.
- Never use destructive git operations (`reset --hard`, force-push, checkout discard) unless explicitly requested.
- Ignore unrelated dirty-worktree files; do not revert user changes outside the task.

## 8) When to ask the user questions

Ask only when materially blocked or when choices are high-impact:

- Ambiguous requirement with multiple valid implementations that change behavior significantly.
- Destructive/irreversible actions (schema/data deletion, production-impacting operations).
- Missing required secrets/credentials.

When asking, provide one recommended default and continue all non-blocked work first.

## 9) Useful commands

- Start app: `streamlit run app.py`
- Run all tests: `pytest tests`
- Run targeted test file: `pytest tests/test_js_client_cache.py`
- Seed compliance rules (only if explicitly requested): `python scripts/seed_compliance_rules.py`
- Import ingredient compliance data (only if explicitly requested): `python scripts/import_ingredient_compliance_data.py --ingredients-csv <path> --rules-csv <path>`

## 10) Preferred execution style for this repo

- Primary agent should delegate as much work as practical to specialized sub-agents/tools to minimize expensive token usage.
- Be concise and action-oriented.
- Explain what changed, where, and why.
- Include file paths in responses.
- Suggest next logical validation step when relevant.
