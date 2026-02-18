SET ROLE launchpad_admin;

-- Pricing analysis
CREATE TABLE launchpad.pricing_analysis (
    pricing_id              BIGSERIAL PRIMARY KEY,
    launch_id               BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    marketplace             VARCHAR(5) NOT NULL,
    recommended_launch_price NUMERIC(10,2),
    price_floor             NUMERIC(10,2),
    price_ceiling           NUMERIC(10,2),
    margin_estimate_pct     NUMERIC(5,2),
    competitor_price_p25    NUMERIC(10,2),
    competitor_price_p50    NUMERIC(10,2),
    competitor_price_p75    NUMERIC(10,2),
    competitor_count        INTEGER,
    data_freshness_date     DATE,
    analyzed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (launch_id, marketplace)
);

-- PPC simulation per keyword
CREATE TABLE launchpad.ppc_simulation (
    sim_id                  BIGSERIAL PRIMARY KEY,
    launch_id               BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    marketplace             VARCHAR(5) NOT NULL,
    keyword                 TEXT NOT NULL,
    search_volume_exact     INTEGER,
    estimated_cpc           NUMERIC(8,2),
    estimated_acos_pct      NUMERIC(5,2),
    estimated_tacos_pct     NUMERIC(5,2),
    organic_rank_target     INTEGER,
    estimated_daily_spend   NUMERIC(10,2),
    estimated_days_to_page1 INTEGER,
    source_field            VARCHAR(30),  -- ppc_bid_exact or ppc_bid_broad from keyword_bank
    simulated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (launch_id, marketplace, keyword)
);

-- Risk assessment
CREATE TABLE launchpad.risk_assessment (
    risk_id         BIGSERIAL PRIMARY KEY,
    launch_id       BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    risk_category   VARCHAR(30) NOT NULL,  -- 'safety','fragility','IP','compliance','market'
    risk_description TEXT NOT NULL,
    severity        VARCHAR(10) CHECK (severity IN ('Low','Medium','High','Critical')),
    mitigation      TEXT,
    assessed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
