# Repository Atlas: Amazon Launchpad

## Project Responsibility
Amazon Launchpad is a comprehensive product launch management system for Amazon sellers expanding from US to UK/EU marketplaces. It provides a 4-stage pipeline (opportunity validation → compliance → pricing/risk → creative assets) with intelligent scoring, regulatory compliance checking, and AI-powered content generation. The application features cache-first Jungle Scout API integration to minimize API costs.

## System Entry Points
- `app.py` — Main Streamlit application entry point and home page
- `requirements.txt` — Python dependency manifest (Streamlit, psycopg, Jungle Scout SDK)
- `.env` — Environment configuration for database DSNs and API keys

## Repository Directory Map

| Directory | Responsibility Summary | Detailed Map |
|-----------|------------------------|--------------|
| `services/` | Core business logic layer implementing Service Layer pattern with stateless classes, cache-first API client, and budget metering. | [View Map](services/codemap.md) |
| `pages/` | Streamlit multi-page UI layer implementing the 4-stage launch pipeline with stage gating. | [View Map](pages/codemap.md) |
| `migrations/` | Sequential PostgreSQL migrations (001-008) building the complete `launchpad` schema with RBAC and cache evolution. | [View Map](migrations/codemap.md) |
| `scripts/` | Deployment utilities for database seeding (compliance rules) and permission validation. | [View Map](scripts/codemap.md) |

## Architecture Overview

### Data Flow
```
User (Streamlit Pages)
    ↓
Services Layer (Business Logic)
    ↓
PostgreSQL (launchpad schema) ← Cache-First API
    ↓                           ↓
Application State            Jungle Scout API
    ↓
market_intel schema (cross-schema reads)
```

### Key Design Patterns
1. **Service Layer** — All domain logic isolated from UI concerns
2. **Cache-First API** — Jungle Scout responses cached in PostgreSQL with parameterized keys
3. **Stage Gating** — Pipeline enforces sequential completion (Stage 1 → 2 → 3 → 4)
4. **RBAC** — Three database roles (admin/app/reader) with principle of least privilege
5. **Stateless Services** — No instance state; all persistence in database

### Technology Stack
- **Frontend**: Streamlit (Python-based UI framework)
- **Backend Services**: Python 3.13, psycopg3, dataclasses
- **Database**: PostgreSQL 15+ with custom schema (`launchpad`)
- **External APIs**: Jungle Scout Data API, Google Generative AI
- **Caching**: PostgreSQL table-based with TTL support

## Development & Deployment

### Environment Variables
- `LAUNCHPAD_DB_DSN` — Primary database connection
- `MARKET_INTEL_DSN` — Cross-schema read access
- `JS_API_KEY` — Jungle Scout API credentials
- `GOOGLE_APPLICATION_CREDENTIALS` — Generative AI credentials

### Key Commands
```bash
# Run Streamlit app
streamlit run app.py

# Run tests
python -m pytest tests/

# Apply migrations
psql "$PG_DSN_SUPER" -f migrations/008_cache_evolution.sql

# Seed compliance rules
python scripts/seed_compliance_rules.py

# Validate permissions
psql "$LAUNCHPAD_DB_DSN" -f scripts/validate_launchpad_access.sql
```

## Project Status
- **Migrations**: 8/8 applied (including cache evolution)
- **Tests**: 7/7 passing (cache-first strategy)
- **Cache-First**: Active (request_key support in Migration 008)

---

*Cartography initialized: 2026-02-19*
*Files tracked: 24 (Python + SQL)*
