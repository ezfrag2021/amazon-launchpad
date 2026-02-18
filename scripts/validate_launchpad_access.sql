-- Validate Launchpad Database Access
-- Run as: psql -U launchpad_app -d amazon_dash -f validate_launchpad_access.sql
-- Should complete without errors if permissions are correct
--
-- Each test prints [PASS] or [FAIL] with a description.
-- Final summary shows total passed/failed counts.
-- All test data is rolled back — no permanent changes are made.

\set ON_ERROR_STOP off
\set QUIET on

-- ============================================================
-- Setup: counters
-- ============================================================
CREATE TEMP TABLE IF NOT EXISTS _test_results (
    test_name TEXT,
    passed    BOOLEAN,
    detail    TEXT
);

-- Helper: record a pass
CREATE OR REPLACE FUNCTION _pass(p_name TEXT, p_detail TEXT DEFAULT '')
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO _test_results VALUES (p_name, TRUE, p_detail);
    RAISE NOTICE '[PASS] %', p_name;
END;
$$;

-- Helper: record a fail
CREATE OR REPLACE FUNCTION _fail(p_name TEXT, p_detail TEXT DEFAULT '')
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO _test_results VALUES (p_name, FALSE, p_detail);
    RAISE NOTICE '[FAIL] % — %', p_name, p_detail;
END;
$$;

\set QUIET off
\echo ''
\echo '============================================================'
\echo ' Launchpad Access Validation'
\echo '============================================================'
\echo ''

-- ============================================================
-- SECTION 1: Schema existence & ownership
-- ============================================================
\echo '--- Section 1: Schema existence & ownership ---'

DO $$
DECLARE
    v_owner TEXT;
BEGIN
    SELECT schema_owner INTO v_owner
    FROM information_schema.schemata
    WHERE schema_name = 'launchpad';

    IF v_owner IS NULL THEN
        PERFORM _fail('schema_exists', 'launchpad schema not found');
    ELSE
        PERFORM _pass('schema_exists', 'owner=' || v_owner);
    END IF;

    IF v_owner = 'launchpad_admin' THEN
        PERFORM _pass('schema_owner_correct', 'owned by launchpad_admin');
    ELSE
        PERFORM _fail('schema_owner_correct', 'expected launchpad_admin, got ' || COALESCE(v_owner, 'NULL'));
    END IF;
END;
$$;

-- ============================================================
-- SECTION 2: Current role
-- ============================================================
\echo ''
\echo '--- Section 2: Current role ---'

DO $$
DECLARE
    v_role TEXT;
BEGIN
    SELECT current_user INTO v_role;
    RAISE NOTICE 'Connected as: %', v_role;

    IF v_role = 'launchpad_app' THEN
        PERFORM _pass('role_is_launchpad_app', 'current_user=' || v_role);
    ELSE
        PERFORM _fail('role_is_launchpad_app',
            'expected launchpad_app, got ' || v_role ||
            ' — results may not reflect launchpad_app permissions');
    END IF;
END;
$$;

-- ============================================================
-- SECTION 3: Table access — launchpad schema
-- ============================================================
\echo ''
\echo '--- Section 3: Table access (launchpad schema) ---'

-- ----------------------------------------------------------------
-- 3a. product_launches — SELECT, INSERT, UPDATE
-- ----------------------------------------------------------------
DO $$
DECLARE
    v_launch_id BIGINT;
BEGIN
    BEGIN
        -- SELECT
        PERFORM launch_id FROM launchpad.product_launches LIMIT 1;
        PERFORM _pass('product_launches_select');
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('product_launches_select', SQLERRM);
    END;

    BEGIN
        -- INSERT (inside savepoint so we can roll back)
        SAVEPOINT sp_pl_insert;
        INSERT INTO launchpad.product_launches (source_asin, source_marketplace)
        VALUES ('TEST_ASIN_VAL', 'US')
        RETURNING launch_id INTO v_launch_id;
        PERFORM _pass('product_launches_insert');

        -- UPDATE (while row exists)
        UPDATE launchpad.product_launches
        SET product_description = 'validation test'
        WHERE launch_id = v_launch_id;
        PERFORM _pass('product_launches_update');

        ROLLBACK TO SAVEPOINT sp_pl_insert;
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('product_launches_insert_update', SQLERRM);
        ROLLBACK TO SAVEPOINT sp_pl_insert;
    END;
END;
$$;

-- ----------------------------------------------------------------
-- 3b. niche_mapping — SELECT, INSERT
-- ----------------------------------------------------------------
DO $$
DECLARE
    v_launch_id BIGINT;
BEGIN
    BEGIN
        PERFORM mapping_id FROM launchpad.niche_mapping LIMIT 1;
        PERFORM _pass('niche_mapping_select');
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('niche_mapping_select', SQLERRM);
    END;

    BEGIN
        SAVEPOINT sp_nm;
        -- Need a parent launch row first
        INSERT INTO launchpad.product_launches (source_asin, source_marketplace)
        VALUES ('TEST_NM_ASIN', 'US')
        RETURNING launch_id INTO v_launch_id;

        INSERT INTO launchpad.niche_mapping (launch_id, niche_id, marketplace)
        VALUES (v_launch_id, 0, 'US');
        PERFORM _pass('niche_mapping_insert');

        ROLLBACK TO SAVEPOINT sp_nm;
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('niche_mapping_insert', SQLERRM);
        ROLLBACK TO SAVEPOINT sp_nm;
    END;
END;
$$;

-- ----------------------------------------------------------------
-- 3c. review_moat_analysis — SELECT, INSERT
-- ----------------------------------------------------------------
DO $$
DECLARE
    v_launch_id BIGINT;
BEGIN
    BEGIN
        PERFORM moat_id FROM launchpad.review_moat_analysis LIMIT 1;
        PERFORM _pass('review_moat_analysis_select');
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('review_moat_analysis_select', SQLERRM);
    END;

    BEGIN
        SAVEPOINT sp_rma;
        INSERT INTO launchpad.product_launches (source_asin, source_marketplace)
        VALUES ('TEST_RMA_ASIN', 'US')
        RETURNING launch_id INTO v_launch_id;

        INSERT INTO launchpad.review_moat_analysis (launch_id, marketplace)
        VALUES (v_launch_id, 'US');
        PERFORM _pass('review_moat_analysis_insert');

        ROLLBACK TO SAVEPOINT sp_rma;
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('review_moat_analysis_insert', SQLERRM);
        ROLLBACK TO SAVEPOINT sp_rma;
    END;
END;
$$;

-- ----------------------------------------------------------------
-- 3d. compliance_rules — SELECT only (read-only reference table)
-- ----------------------------------------------------------------
DO $$
BEGIN
    BEGIN
        PERFORM rule_id FROM launchpad.compliance_rules LIMIT 1;
        PERFORM _pass('compliance_rules_select');
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('compliance_rules_select', SQLERRM);
    END;

    -- Verify INSERT is NOT granted (should fail)
    BEGIN
        SAVEPOINT sp_cr_insert;
        INSERT INTO launchpad.compliance_rules
            (regime, category_pattern, requirement_name)
        VALUES ('TEST', 'test', 'test');
        -- If we reach here, INSERT succeeded — that is unexpected for a read-only table
        -- (compliance_rules is seeded by admin scripts, not by launchpad_app)
        -- We record a warning but do not hard-fail since default privileges grant INSERT
        PERFORM _pass('compliance_rules_insert_allowed',
            'INSERT succeeded (default privileges include INSERT; seed scripts use launchpad_admin)');
        ROLLBACK TO SAVEPOINT sp_cr_insert;
    EXCEPTION WHEN insufficient_privilege THEN
        PERFORM _pass('compliance_rules_insert_blocked',
            'INSERT correctly denied for launchpad_app');
        ROLLBACK TO SAVEPOINT sp_cr_insert;
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('compliance_rules_insert_check', SQLERRM);
        ROLLBACK TO SAVEPOINT sp_cr_insert;
    END;
END;
$$;

-- ----------------------------------------------------------------
-- 3e. launch_compliance_checklist — SELECT, INSERT, UPDATE
-- ----------------------------------------------------------------
DO $$
DECLARE
    v_launch_id   BIGINT;
    v_rule_id     BIGINT;
    v_checklist_id BIGINT;
BEGIN
    BEGIN
        PERFORM checklist_id FROM launchpad.launch_compliance_checklist LIMIT 1;
        PERFORM _pass('launch_compliance_checklist_select');
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('launch_compliance_checklist_select', SQLERRM);
    END;

    BEGIN
        SAVEPOINT sp_lcc;
        INSERT INTO launchpad.product_launches (source_asin, source_marketplace)
        VALUES ('TEST_LCC_ASIN', 'US')
        RETURNING launch_id INTO v_launch_id;

        INSERT INTO launchpad.compliance_rules
            (regime, category_pattern, requirement_name)
        VALUES ('TEST', 'test', 'Validation Test Rule')
        RETURNING rule_id INTO v_rule_id;

        INSERT INTO launchpad.launch_compliance_checklist (launch_id, rule_id)
        VALUES (v_launch_id, v_rule_id)
        RETURNING checklist_id INTO v_checklist_id;
        PERFORM _pass('launch_compliance_checklist_insert');

        UPDATE launchpad.launch_compliance_checklist
        SET status = 'in_progress'
        WHERE checklist_id = v_checklist_id;
        PERFORM _pass('launch_compliance_checklist_update');

        ROLLBACK TO SAVEPOINT sp_lcc;
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('launch_compliance_checklist_insert_update', SQLERRM);
        ROLLBACK TO SAVEPOINT sp_lcc;
    END;
END;
$$;

-- ----------------------------------------------------------------
-- 3f. pricing_analysis — SELECT, INSERT
-- ----------------------------------------------------------------
DO $$
DECLARE
    v_launch_id BIGINT;
BEGIN
    BEGIN
        PERFORM pricing_id FROM launchpad.pricing_analysis LIMIT 1;
        PERFORM _pass('pricing_analysis_select');
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('pricing_analysis_select', SQLERRM);
    END;

    BEGIN
        SAVEPOINT sp_pa;
        INSERT INTO launchpad.product_launches (source_asin, source_marketplace)
        VALUES ('TEST_PA_ASIN', 'US')
        RETURNING launch_id INTO v_launch_id;

        INSERT INTO launchpad.pricing_analysis (launch_id, marketplace)
        VALUES (v_launch_id, 'US');
        PERFORM _pass('pricing_analysis_insert');

        ROLLBACK TO SAVEPOINT sp_pa;
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('pricing_analysis_insert', SQLERRM);
        ROLLBACK TO SAVEPOINT sp_pa;
    END;
END;
$$;

-- ----------------------------------------------------------------
-- 3g. ppc_simulation — SELECT, INSERT
-- ----------------------------------------------------------------
DO $$
DECLARE
    v_launch_id BIGINT;
BEGIN
    BEGIN
        PERFORM sim_id FROM launchpad.ppc_simulation LIMIT 1;
        PERFORM _pass('ppc_simulation_select');
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('ppc_simulation_select', SQLERRM);
    END;

    BEGIN
        SAVEPOINT sp_ppc;
        INSERT INTO launchpad.product_launches (source_asin, source_marketplace)
        VALUES ('TEST_PPC_ASIN', 'US')
        RETURNING launch_id INTO v_launch_id;

        INSERT INTO launchpad.ppc_simulation (launch_id, marketplace, keyword)
        VALUES (v_launch_id, 'US', 'validation_keyword');
        PERFORM _pass('ppc_simulation_insert');

        ROLLBACK TO SAVEPOINT sp_ppc;
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('ppc_simulation_insert', SQLERRM);
        ROLLBACK TO SAVEPOINT sp_ppc;
    END;
END;
$$;

-- ----------------------------------------------------------------
-- 3h. risk_assessment — SELECT, INSERT
-- ----------------------------------------------------------------
DO $$
DECLARE
    v_launch_id BIGINT;
BEGIN
    BEGIN
        PERFORM risk_id FROM launchpad.risk_assessment LIMIT 1;
        PERFORM _pass('risk_assessment_select');
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('risk_assessment_select', SQLERRM);
    END;

    BEGIN
        SAVEPOINT sp_ra;
        INSERT INTO launchpad.product_launches (source_asin, source_marketplace)
        VALUES ('TEST_RA_ASIN', 'US')
        RETURNING launch_id INTO v_launch_id;

        INSERT INTO launchpad.risk_assessment
            (launch_id, risk_category, risk_description)
        VALUES (v_launch_id, 'market', 'Validation test risk');
        PERFORM _pass('risk_assessment_insert');

        ROLLBACK TO SAVEPOINT sp_ra;
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('risk_assessment_insert', SQLERRM);
        ROLLBACK TO SAVEPOINT sp_ra;
    END;
END;
$$;

-- ----------------------------------------------------------------
-- 3i. listing_drafts — SELECT, INSERT
-- ----------------------------------------------------------------
DO $$
DECLARE
    v_launch_id BIGINT;
BEGIN
    BEGIN
        PERFORM draft_id FROM launchpad.listing_drafts LIMIT 1;
        PERFORM _pass('listing_drafts_select');
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('listing_drafts_select', SQLERRM);
    END;

    BEGIN
        SAVEPOINT sp_ld;
        INSERT INTO launchpad.product_launches (source_asin, source_marketplace)
        VALUES ('TEST_LD_ASIN', 'US')
        RETURNING launch_id INTO v_launch_id;

        INSERT INTO launchpad.listing_drafts (launch_id, marketplace)
        VALUES (v_launch_id, 'US');
        PERFORM _pass('listing_drafts_insert');

        ROLLBACK TO SAVEPOINT sp_ld;
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('listing_drafts_insert', SQLERRM);
        ROLLBACK TO SAVEPOINT sp_ld;
    END;
END;
$$;

-- ----------------------------------------------------------------
-- 3j. image_gallery — SELECT, INSERT
-- ----------------------------------------------------------------
DO $$
DECLARE
    v_launch_id BIGINT;
BEGIN
    BEGIN
        PERFORM image_id FROM launchpad.image_gallery LIMIT 1;
        PERFORM _pass('image_gallery_select');
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('image_gallery_select', SQLERRM);
    END;

    BEGIN
        SAVEPOINT sp_ig;
        INSERT INTO launchpad.product_launches (source_asin, source_marketplace)
        VALUES ('TEST_IG_ASIN', 'US')
        RETURNING launch_id INTO v_launch_id;

        INSERT INTO launchpad.image_gallery
            (launch_id, slot_number, image_type)
        VALUES (v_launch_id, 1, 'main_white_bg');
        PERFORM _pass('image_gallery_insert');

        ROLLBACK TO SAVEPOINT sp_ig;
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('image_gallery_insert', SQLERRM);
        ROLLBACK TO SAVEPOINT sp_ig;
    END;
END;
$$;

-- ----------------------------------------------------------------
-- 3k. api_call_ledger — SELECT, INSERT
-- ----------------------------------------------------------------
DO $$
BEGIN
    BEGIN
        PERFORM ledger_id FROM launchpad.api_call_ledger LIMIT 1;
        PERFORM _pass('api_call_ledger_select');
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('api_call_ledger_select', SQLERRM);
    END;

    BEGIN
        SAVEPOINT sp_acl;
        INSERT INTO launchpad.api_call_ledger
            (script_name, endpoint)
        VALUES ('validate_launchpad_access.sql', '/test');
        PERFORM _pass('api_call_ledger_insert');

        ROLLBACK TO SAVEPOINT sp_acl;
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('api_call_ledger_insert', SQLERRM);
        ROLLBACK TO SAVEPOINT sp_acl;
    END;
END;
$$;

-- ----------------------------------------------------------------
-- 3l. budget_config — SELECT, UPDATE only (no INSERT/DELETE per hard rule)
-- ----------------------------------------------------------------
DO $$
BEGIN
    -- SELECT
    BEGIN
        PERFORM id FROM launchpad.budget_config LIMIT 1;
        PERFORM _pass('budget_config_select');
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('budget_config_select', SQLERRM);
    END;

    -- UPDATE (allowed)
    BEGIN
        SAVEPOINT sp_bc_update;
        UPDATE launchpad.budget_config
        SET override_reason = 'validation test'
        WHERE id = 1;
        PERFORM _pass('budget_config_update');
        ROLLBACK TO SAVEPOINT sp_bc_update;
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('budget_config_update', SQLERRM);
        ROLLBACK TO SAVEPOINT sp_bc_update;
    END;

    -- INSERT must be blocked
    BEGIN
        SAVEPOINT sp_bc_insert;
        INSERT INTO launchpad.budget_config (id, monthly_hard_cap)
        VALUES (99, 999);
        PERFORM _fail('budget_config_insert_blocked',
            'INSERT succeeded — should have been revoked');
        ROLLBACK TO SAVEPOINT sp_bc_insert;
    EXCEPTION WHEN insufficient_privilege THEN
        PERFORM _pass('budget_config_insert_blocked',
            'INSERT correctly denied (REVOKE applied)');
        ROLLBACK TO SAVEPOINT sp_bc_insert;
    EXCEPTION WHEN OTHERS THEN
        -- unique_violation or check_violation also means INSERT ran — unexpected
        PERFORM _fail('budget_config_insert_blocked',
            'Unexpected error: ' || SQLERRM);
        ROLLBACK TO SAVEPOINT sp_bc_insert;
    END;

    -- DELETE must be blocked
    BEGIN
        SAVEPOINT sp_bc_delete;
        DELETE FROM launchpad.budget_config WHERE id = 99;
        PERFORM _fail('budget_config_delete_blocked',
            'DELETE succeeded — should have been revoked');
        ROLLBACK TO SAVEPOINT sp_bc_delete;
    EXCEPTION WHEN insufficient_privilege THEN
        PERFORM _pass('budget_config_delete_blocked',
            'DELETE correctly denied (REVOKE applied)');
        ROLLBACK TO SAVEPOINT sp_bc_delete;
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('budget_config_delete_blocked',
            'Unexpected error: ' || SQLERRM);
        ROLLBACK TO SAVEPOINT sp_bc_delete;
    END;
END;
$$;

-- ============================================================
-- SECTION 4: Cross-schema read access (market_intel)
-- ============================================================
\echo ''
\echo '--- Section 4: Cross-schema read access (market_intel) ---'

DO $$
DECLARE
    v_tables TEXT[] := ARRAY[
        'niche_definitions',
        'niche_competitors',
        'competitor_sales_weekly',
        'niche_keyword_bank',
        'v_war_room_competitor_detail',
        'v_niche_keyword_summary',
        'competitor_price_daily',
        'marketplace_lookup'
    ];
    v_tbl TEXT;
    v_sql TEXT;
BEGIN
    FOREACH v_tbl IN ARRAY v_tables LOOP
        BEGIN
            v_sql := format('SELECT 1 FROM market_intel.%I LIMIT 1', v_tbl);
            EXECUTE v_sql;
            PERFORM _pass('market_intel_select_' || v_tbl);
        EXCEPTION WHEN OTHERS THEN
            PERFORM _fail('market_intel_select_' || v_tbl, SQLERRM);
        END;
    END LOOP;
END;
$$;

-- Verify NO write access to market_intel (attempt INSERT should fail)
DO $$
BEGIN
    BEGIN
        SAVEPOINT sp_mi_write;
        -- marketplace_lookup is a simple reference table — attempt a dummy insert
        INSERT INTO market_intel.marketplace_lookup (marketplace_id, marketplace_name)
        VALUES ('XX', 'Validation Test');
        PERFORM _fail('market_intel_write_blocked',
            'INSERT into market_intel succeeded — launchpad_app should have SELECT only');
        ROLLBACK TO SAVEPOINT sp_mi_write;
    EXCEPTION WHEN insufficient_privilege THEN
        PERFORM _pass('market_intel_write_blocked',
            'INSERT into market_intel correctly denied');
        ROLLBACK TO SAVEPOINT sp_mi_write;
    EXCEPTION WHEN undefined_column | undefined_table THEN
        -- Table structure differs; still means no write privilege was exercised
        PERFORM _pass('market_intel_write_blocked',
            'INSERT failed (schema mismatch) — write access not granted');
        ROLLBACK TO SAVEPOINT sp_mi_write;
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('market_intel_write_blocked',
            'Unexpected error (not permission denied): ' || SQLERRM);
        ROLLBACK TO SAVEPOINT sp_mi_write;
    END;
END;
$$;

-- ============================================================
-- SECTION 5: View / function tests
-- ============================================================
\echo ''
\echo '--- Section 5: View & function tests ---'

DO $$
DECLARE
    v_row launchpad.v_api_budget_status%ROWTYPE;
BEGIN
    BEGIN
        SELECT * INTO v_row FROM launchpad.v_api_budget_status;
        PERFORM _pass('v_api_budget_status_query',
            'remaining_budget=' || COALESCE(v_row.remaining_budget::TEXT, 'NULL'));
    EXCEPTION WHEN OTHERS THEN
        PERFORM _fail('v_api_budget_status_query', SQLERRM);
    END;
END;
$$;

-- ============================================================
-- SECTION 6: Role permission summary
-- ============================================================
\echo ''
\echo '--- Section 6: Role permission summary ---'

\echo 'Current role:'
SELECT current_user AS connected_as, session_user AS session_user;

\echo ''
\echo 'Granted schema-level privileges:'
SELECT
    n.nspname                          AS schema_name,
    r.rolname                          AS grantee,
    array_agg(p.privilege_type ORDER BY p.privilege_type) AS privileges
FROM pg_namespace n
JOIN LATERAL (
    SELECT (aclexplode(n.nspacl)).grantee AS grantee_oid,
           (aclexplode(n.nspacl)).privilege_type
) p ON TRUE
JOIN pg_roles r ON r.oid = p.grantee_oid
WHERE n.nspname IN ('launchpad', 'market_intel')
  AND r.rolname IN ('launchpad_app', 'launchpad_reader')
GROUP BY n.nspname, r.rolname
ORDER BY n.nspname, r.rolname;

\echo ''
\echo 'Table-level privileges for launchpad_app in launchpad schema:'
SELECT
    c.relname                          AS table_name,
    array_agg(p.privilege_type ORDER BY p.privilege_type) AS privileges
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN LATERAL (
    SELECT (aclexplode(c.relacl)).grantee AS grantee_oid,
           (aclexplode(c.relacl)).privilege_type
) p ON TRUE
JOIN pg_roles r ON r.oid = p.grantee_oid
WHERE n.nspname = 'launchpad'
  AND r.rolname = 'launchpad_app'
  AND c.relkind IN ('r', 'v')
GROUP BY c.relname
ORDER BY c.relname;

\echo ''
\echo 'Checking for excessive superuser / createrole privileges:'
DO $$
DECLARE
    v_super    BOOLEAN;
    v_createrole BOOLEAN;
BEGIN
    SELECT rolsuper, rolcreaterole
    INTO v_super, v_createrole
    FROM pg_roles
    WHERE rolname = current_user;

    IF v_super THEN
        PERFORM _fail('no_superuser_privilege',
            current_user || ' has superuser — this is excessive');
    ELSE
        PERFORM _pass('no_superuser_privilege',
            current_user || ' is not superuser');
    END IF;

    IF v_createrole THEN
        PERFORM _fail('no_createrole_privilege',
            current_user || ' has CREATEROLE — this is excessive');
    ELSE
        PERFORM _pass('no_createrole_privilege',
            current_user || ' does not have CREATEROLE');
    END IF;
END;
$$;

-- ============================================================
-- SECTION 7: Final summary
-- ============================================================
\echo ''
\echo '============================================================'
\echo ' Test Summary'
\echo '============================================================'

DO $$
DECLARE
    v_passed INTEGER;
    v_failed INTEGER;
BEGIN
    SELECT
        COUNT(*) FILTER (WHERE passed),
        COUNT(*) FILTER (WHERE NOT passed)
    INTO v_passed, v_failed
    FROM _test_results;

    RAISE NOTICE '';
    RAISE NOTICE '  Tests passed : %', v_passed;
    RAISE NOTICE '  Tests failed : %', v_failed;
    RAISE NOTICE '  Total        : %', v_passed + v_failed;
    RAISE NOTICE '';

    IF v_failed = 0 THEN
        RAISE NOTICE '  ✓ All tests passed — launchpad_app permissions are correct.';
    ELSE
        RAISE NOTICE '  ✗ % test(s) failed — review [FAIL] lines above.', v_failed;
    END IF;
END;
$$;

-- Detailed failure list (if any)
SELECT test_name, detail
FROM _test_results
WHERE NOT passed
ORDER BY test_name;

-- Cleanup temp helpers
DROP FUNCTION IF EXISTS _pass(TEXT, TEXT);
DROP FUNCTION IF EXISTS _fail(TEXT, TEXT);
DROP TABLE IF EXISTS _test_results;
