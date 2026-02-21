SET ROLE launchpad_admin;

ALTER TABLE launchpad.product_launches
    ADD COLUMN IF NOT EXISTS launch_name TEXT,
    ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_product_launches_archived
    ON launchpad.product_launches(is_archived, created_at DESC);
