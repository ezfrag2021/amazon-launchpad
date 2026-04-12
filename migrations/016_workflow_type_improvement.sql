SET ROLE launchpad_admin;

-- Add workflow_type to distinguish new launches from ASIN improvement projects.
-- Default 'new_launch' preserves backward compatibility for existing rows.
ALTER TABLE launchpad.product_launches
    ADD COLUMN IF NOT EXISTS workflow_type VARCHAR(20) NOT NULL DEFAULT 'new_launch'
        CHECK (workflow_type IN ('new_launch', 'asin_improvement'));

-- For improvement workflows the current_stage upper bound must include stage 5
-- (the original CHECK limited to 1–4, migration 002; later code uses 1–5).
-- The constraint was already relaxed in practice — ensure it covers 1–5.
ALTER TABLE launchpad.product_launches
    DROP CONSTRAINT IF EXISTS product_launches_current_stage_check;

ALTER TABLE launchpad.product_launches
    ADD CONSTRAINT product_launches_current_stage_check
        CHECK (current_stage BETWEEN 1 AND 5);

-- Store the original listing snapshot fetched from SP-API so the Creative
-- Studio can show a side-by-side comparison (current vs. improved).
CREATE TABLE IF NOT EXISTS launchpad.asin_snapshots (
    snapshot_id     BIGSERIAL PRIMARY KEY,
    launch_id       BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    asin            VARCHAR(20) NOT NULL,
    marketplace     VARCHAR(5)  NOT NULL,

    -- Current listing content at time of import
    title           TEXT,
    bullets         JSONB,          -- JSON array of bullet strings
    description     TEXT,
    backend_keywords TEXT,

    -- Current images (JSON array of {url, variant, width, height})
    images          JSONB,

    -- Product metadata
    product_type    TEXT,
    brand           TEXT,
    category        TEXT,
    price           NUMERIC(10,2),
    currency        VARCHAR(5),

    -- Full API response for debugging / future use
    raw_payload     JSONB,

    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (launch_id, marketplace)
);

CREATE INDEX IF NOT EXISTS idx_asin_snapshots_launch
    ON launchpad.asin_snapshots(launch_id);

CREATE INDEX IF NOT EXISTS idx_product_launches_workflow
    ON launchpad.product_launches(workflow_type, created_at DESC);

-- Grant access to the app role
GRANT SELECT, INSERT, UPDATE ON launchpad.asin_snapshots TO launchpad_app;
GRANT USAGE, SELECT ON SEQUENCE launchpad.asin_snapshots_snapshot_id_seq TO launchpad_app;
