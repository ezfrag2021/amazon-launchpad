# Amazon Launchpad

Amazon Launchpad is a Streamlit app for taking a product from idea validation to launch-ready assets across UK/EU marketplaces.

It is organized as a staged workflow:

1. Opportunity Validator
2. Compliance Compass
3. Risk & Pricing Architect
4. Creative Studio
5. Launch Ready (state completion)

The app runs on PostgreSQL schema `launchpad` and integrates with Jungle Scout, Amazon SP-API helpers, and Google Gemini/Imagen for AI-assisted analysis and creative generation.

## What this project does

- Evaluates a US source ASIN for UK/EU opportunity fit
- Scores market opportunity with a Pursuit Score (`Saturated`, `Proven`, `Goldmine`)
- Builds compliance checklists for CE/UKCA/WEEE/RoHS/ToyEN71/DPP-style requirements
- Models competitor pricing and PPC scenarios
- Generates listing copy and image concepts with policy guardrails
- Tracks each launch through a persisted stage state machine

## Tech stack

- Python 3.11+
- Streamlit
- PostgreSQL (`launchpad` schema)
- `psycopg` (psycopg3)
- Jungle Scout API client
- Google Generative AI (Gemini + Imagen)

## Project layout

- `app.py` - main dashboard entrypoint
- `pages/` - Streamlit module pages (`1_` through `6_`)
- `services/` - domain services (DB, launch state, scoring, compliance, pricing, creative)
- `migrations/` - SQL migrations for schema, tables, cache, and policy terms
- `scripts/` - helper scripts (compliance seed, DB validation)
- `tests/` - pytest test suite for key services

## Quick start

### 1) Clone and create venv

```bash
git clone https://github.com/ezfrag2021/amazon-launchpad.git
cd amazon-launchpad
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2) Configure environment

```bash
cp .env.example .env
```

Fill in at least:

- `LAUNCHPAD_DB_DSN` (or fallback `MARKET_INTEL_DSN` / `PG_DSN`)
- `JUNGLESCOUT_API_KEY_NAME`
- `JUNGLESCOUT_API_KEY`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

Note: `services/db_connection.py` supports `${VAR}` expansion inside DSN values.

### 3) Apply database migrations

Run all SQL files in `migrations/` in order:

```bash
for f in migrations/*.sql; do
  echo "Applying $f"
  psql "$LAUNCHPAD_DB_DSN" -v ON_ERROR_STOP=1 -f "$f"
done
```

### 4) Seed compliance rules

```bash
python scripts/seed_compliance_rules.py
```

### 5) Start the app

```bash
streamlit run app.py
```

Default Streamlit config is in `.streamlit/config.toml` (port `8503`).

## Running tests

```bash
pytest tests
```

## Important operational notes

- Launchpad API budget is tracked in `launchpad.api_call_ledger` and `launchpad.budget_config`.
- This app is designed to use `launchpad` schema objects and read-only access to selected `market_intel` data.
- Stage progression is managed by `services/launch_state.py`.
- Listing guardrails and blocked terms are enforced in Creative Studio and can be persisted to `launchpad.listing_policy_terms`.

## Additional docs

- `SETUP.md` - environment and deployment setup guide
- `DB_REFERENCE.md` - database and schema reference
- `LAUNCHPAD_PROJECT_STRUCTURE.md` - architecture and design notes
- `HARD_RULES.md` - non-negotiable project constraints

## Security

- Do not commit `.env` or credential files.
- Prefer scoped DB roles and least-privilege grants from migrations.
- Validate all generated listing/content before publishing to Amazon.
