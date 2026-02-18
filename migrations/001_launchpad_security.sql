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

SET ROLE launchpad_admin;

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
