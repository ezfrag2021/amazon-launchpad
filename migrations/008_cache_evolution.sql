SET ROLE launchpad_admin;

-- Evolution: Add request_key support to jungle_scout_cache for parameterized caching
-- Allows caching different responses for the same ASIN/marketplace/endpoint based on request parameters

-- Add request_key column with default value for backward compatibility
ALTER TABLE launchpad.jungle_scout_cache
ADD COLUMN request_key VARCHAR(64) NOT NULL DEFAULT 'default';

-- Drop old unique constraint
ALTER TABLE launchpad.jungle_scout_cache
DROP CONSTRAINT jungle_scout_cache_asin_marketplace_endpoint_key;

-- Add new unique constraint including request_key
ALTER TABLE launchpad.jungle_scout_cache
ADD CONSTRAINT jungle_scout_cache_unique_lookup UNIQUE (asin, marketplace, endpoint, request_key);

-- Drop old lookup index
DROP INDEX launchpad.idx_js_cache_lookup;

-- Recreate lookup index with request_key
CREATE INDEX idx_js_cache_lookup ON launchpad.jungle_scout_cache (asin, marketplace, endpoint, request_key);

-- Update get_js_cache function to accept request_key parameter
CREATE OR REPLACE FUNCTION launchpad.get_js_cache(
    p_asin        VARCHAR(20),
    p_marketplace VARCHAR(5),
    p_endpoint    VARCHAR(50),
    p_request_key VARCHAR(64) DEFAULT 'default'
)
RETURNS JSONB
LANGUAGE sql STABLE
AS $$
    SELECT response_data
    FROM   launchpad.jungle_scout_cache
    WHERE  asin        = p_asin
      AND  marketplace = p_marketplace
      AND  endpoint    = p_endpoint
      AND  request_key = p_request_key
      AND  (expires_at IS NULL OR expires_at > now())
    LIMIT 1;
$$;

-- Update set_js_cache function to accept request_key and p_ttl_hours parameters
CREATE OR REPLACE FUNCTION launchpad.set_js_cache(
    p_asin           VARCHAR(20),
    p_marketplace    VARCHAR(5),
    p_endpoint       VARCHAR(50),
    p_response_data  JSONB,
    p_api_calls_used INTEGER DEFAULT 1,
    p_request_key    VARCHAR(64) DEFAULT 'default',
    p_ttl_hours      INTEGER DEFAULT NULL
)
RETURNS VOID
LANGUAGE sql
AS $$
    INSERT INTO launchpad.jungle_scout_cache
        (asin, marketplace, endpoint, response_data, fetched_at, api_calls_used, request_key, expires_at)
    VALUES
        (p_asin, p_marketplace, p_endpoint, p_response_data, now(), p_api_calls_used, p_request_key,
         CASE WHEN p_ttl_hours IS NOT NULL THEN now() + (p_ttl_hours || ' hours')::INTERVAL ELSE NULL END)
    ON CONFLICT (asin, marketplace, endpoint, request_key)
    DO UPDATE SET
        response_data  = EXCLUDED.response_data,
        fetched_at     = EXCLUDED.fetched_at,
        api_calls_used = EXCLUDED.api_calls_used,
        expires_at     = EXCLUDED.expires_at;
$$;
