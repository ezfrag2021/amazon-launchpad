SET ROLE launchpad_admin;

-- Launchpad's own API call ledger (mirrors market_intel.api_call_ledger design)
CREATE TABLE launchpad.api_call_ledger (
    ledger_id       BIGSERIAL PRIMARY KEY,
    called_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    script_name     TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    marketplace     VARCHAR(5),
    billable_pages  INTEGER NOT NULL DEFAULT 1,
    launch_id       BIGINT REFERENCES launchpad.product_launches(launch_id),
    metadata        JSONB
);

CREATE INDEX idx_ledger_month ON launchpad.api_call_ledger (date_trunc('month', called_at));

-- Budget config (single-row, same pattern as market_intel.budget_config)
CREATE TABLE launchpad.budget_config (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    monthly_hard_cap    INTEGER NOT NULL DEFAULT 500,
    allow_override      BOOLEAN NOT NULL DEFAULT FALSE,
    override_reason     TEXT
);

INSERT INTO launchpad.budget_config (id, monthly_hard_cap) VALUES (1, 500);

-- Revoke destructive ops on budget_config
REVOKE INSERT, DELETE ON launchpad.budget_config FROM launchpad_app;

-- Budget status view
CREATE VIEW launchpad.v_api_budget_status AS
SELECT
    date_trunc('month', CURRENT_DATE)::DATE AS month_start,
    COALESCE(SUM(l.billable_pages), 0)      AS total_billable_pages,
    bc.monthly_hard_cap,
    bc.monthly_hard_cap - COALESCE(SUM(l.billable_pages), 0) AS remaining_budget,
    bc.allow_override,
    bc.override_reason
FROM launchpad.budget_config bc
LEFT JOIN launchpad.api_call_ledger l
    ON l.called_at >= date_trunc('month', CURRENT_DATE)
    AND l.called_at < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
WHERE bc.id = 1
GROUP BY bc.monthly_hard_cap, bc.allow_override, bc.override_reason;
