SET ROLE launchpad_admin;

-- Central launch entity
CREATE TABLE launchpad.product_launches (
    launch_id       BIGSERIAL PRIMARY KEY,
    source_asin     VARCHAR(20) NOT NULL,
    source_marketplace VARCHAR(5) NOT NULL DEFAULT 'US',
    target_marketplaces TEXT[] NOT NULL DEFAULT ARRAY['UK','DE','FR','IT','ES'],
    product_description TEXT,
    product_category    TEXT,
    pursuit_score       NUMERIC(5,2),
    pursuit_category    VARCHAR(20) CHECK (pursuit_category IN ('Saturated','Proven','Goldmine')),
    current_stage       SMALLINT NOT NULL DEFAULT 1 CHECK (current_stage BETWEEN 1 AND 4),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_launches_source_asin ON launchpad.product_launches(source_asin);
CREATE INDEX idx_launches_created ON launchpad.product_launches(created_at DESC);

-- Niche mapping (links to market_intel.niche_definitions via read)
CREATE TABLE launchpad.niche_mapping (
    mapping_id  BIGSERIAL PRIMARY KEY,
    launch_id   BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    niche_id    BIGINT NOT NULL,  -- FK target is market_intel.niche_definitions (cross-schema, logical only)
    marketplace VARCHAR(5) NOT NULL,
    confidence  NUMERIC(4,3),     -- 0.000–1.000 mapping confidence
    mapped_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (launch_id, niche_id, marketplace)
);

-- Review moat analysis
CREATE TABLE launchpad.review_moat_analysis (
    moat_id             BIGSERIAL PRIMARY KEY,
    launch_id           BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    marketplace         VARCHAR(5) NOT NULL,
    competitor_count    INTEGER,
    avg_review_count    NUMERIC(10,1),
    avg_rating          NUMERIC(3,2),
    review_velocity_30d NUMERIC(10,1),   -- new reviews/30d for top 10 competitors
    moat_strength       VARCHAR(10) CHECK (moat_strength IN ('Weak','Medium','Strong')),
    analyzed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (launch_id, marketplace)
);
