SET ROLE launchpad_admin;

-- Persist Module 1 source-share estimation diagnostics per launch
CREATE TABLE IF NOT EXISTS launchpad.source_share_audit (
    launch_id      BIGINT PRIMARY KEY REFERENCES launchpad.product_launches(launch_id) ON DELETE CASCADE,
    analysis       JSONB NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_source_share_audit_updated
    ON launchpad.source_share_audit (updated_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON launchpad.source_share_audit TO launchpad_app;
