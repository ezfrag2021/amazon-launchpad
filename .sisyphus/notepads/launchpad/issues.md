- 2026-02-18: Streamlit dashboard on :8503 shows DB errors because app.py always normalizes pre-encoded DSN, causing `password authentication failed for user "launchpad_app"`.
- 2026-02-18: launchpad schema exists but has zero relations (`launchpad.product_launches` missing), so migrations 002-006 have not been applied in amazon_dash.
- 2026-02-19: Migration 008 execution failed with `permission denied to set role "launchpad_admin"`; running same SQL without role switch also failed (`must be owner of table jungle_scout_cache`).
# Migration 008 Execution Blocker

## 2026-02-19: Migration 008 Requires Superuser

Migration 008 requires superuser credentials to execute ALTER TABLE and SET ROLE commands.
The launchpad_app role does not have sufficient privileges.

**Resolution:** Set PG_DSN_SUPER environment variable with postgres superuser credentials:
```bash
export PG_DSN_SUPER="postgresql://postgres:PASSWORD@192.168.0.110:5433/amazon_dash?sslmode=disable"
psql "$PG_DSN_SUPER" -f migrations/008_cache_evolution.sql
```

Or run via OPS_MANIFEST.sh Step 9 after setting PG_DSN_SUPER.
Migration file is ready and correct.
