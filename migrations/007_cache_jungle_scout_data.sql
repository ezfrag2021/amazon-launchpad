SET ROLE launchpad_admin;

-- Jungle Scout API response cache
-- Stores raw API responses to implement "fetch once, use many times" pattern.
-- Supported endpoints: product_database, keywords_by_asin, sales_estimates, share_of_voice
CREATE TABLE launchpad.jungle_scout_cache (
    cache_id        BIGSERIAL PRIMARY KEY,
    asin            VARCHAR(20) NOT NULL,
    marketplace     VARCHAR(5) NOT NULL,
    endpoint        VARCHAR(50) NOT NULL CHECK (endpoint IN (
                        'product_database',
                        'keywords_by_asin',
                        'sales_estimates',
                        'share_of_voice'
                    )),
    response_data   JSONB NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ,              -- optional TTL; NULL = never expires
    api_calls_used  INTEGER NOT NULL DEFAULT 1,
    UNIQUE (asin, marketplace, endpoint)
);

CREATE INDEX idx_js_cache_lookup ON launchpad.jungle_scout_cache (asin, marketplace, endpoint);
CREATE INDEX idx_js_cache_fetched ON launchpad.jungle_scout_cache (fetched_at);

-- Return cached response_data if it exists and has not expired, else NULL.
CREATE OR REPLACE FUNCTION launchpad.get_js_cache(
    p_asin        VARCHAR(20),
    p_marketplace VARCHAR(5),
    p_endpoint    VARCHAR(50)
)
RETURNS JSONB
LANGUAGE sql STABLE
AS $$
    SELECT response_data
    FROM   launchpad.jungle_scout_cache
    WHERE  asin        = p_asin
      AND  marketplace = p_marketplace
      AND  endpoint    = p_endpoint
      AND  (expires_at IS NULL OR expires_at > now())
    LIMIT 1;
$$;

-- Upsert a Jungle Scout API response into the cache.
CREATE OR REPLACE FUNCTION launchpad.set_js_cache(
    p_asin           VARCHAR(20),
    p_marketplace    VARCHAR(5),
    p_endpoint       VARCHAR(50),
    p_response_data  JSONB,
    p_api_calls_used INTEGER DEFAULT 1
)
RETURNS VOID
LANGUAGE sql
AS $$
    INSERT INTO launchpad.jungle_scout_cache
        (asin, marketplace, endpoint, response_data, fetched_at, api_calls_used)
    VALUES
        (p_asin, p_marketplace, p_endpoint, p_response_data, now(), p_api_calls_used)
    ON CONFLICT (asin, marketplace, endpoint)
    DO UPDATE SET
        response_data  = EXCLUDED.response_data,
        fetched_at     = EXCLUDED.fetched_at,
        api_calls_used = EXCLUDED.api_calls_used;
$$;

-- Cache coverage summary per ASIN / marketplace
CREATE VIEW launchpad.v_js_cache_summary AS
SELECT
    c.asin,
    c.marketplace,
    COUNT(*)                                    AS cached_endpoints,
    SUM(c.api_calls_used)                       AS total_api_calls_used,
    MAX(c.fetched_at)                           AS last_fetched_at,
    COUNT(*) = 4                                AS all_endpoints_cached
FROM launchpad.jungle_scout_cache c
WHERE c.expires_at IS NULL OR c.expires_at > now()
GROUP BY c.asin, c.marketplace;
