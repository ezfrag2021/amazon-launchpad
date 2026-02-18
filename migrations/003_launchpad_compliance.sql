SET ROLE launchpad_admin;

-- Compliance rules reference (seeded by scripts/seed_compliance_rules.py)
CREATE TABLE launchpad.compliance_rules (
    rule_id                 BIGSERIAL PRIMARY KEY,
    regime                  VARCHAR(20) NOT NULL,  -- CE, UKCA, WEEE, RoHS, ToyEN71, DPP
    category_pattern        TEXT NOT NULL,          -- regex or keyword match against product category
    requirement_name        TEXT NOT NULL,
    requirement_description TEXT,
    documentation_required  TEXT[],                 -- e.g., ARRAY['Safety Data Sheet','Lab Test Report']
    is_2026_dpp_relevant    BOOLEAN NOT NULL DEFAULT FALSE,
    effective_date          DATE,
    source_url              TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_compliance_regime ON launchpad.compliance_rules(regime);

-- Per-launch compliance checklist
CREATE TABLE launchpad.launch_compliance_checklist (
    checklist_id    BIGSERIAL PRIMARY KEY,
    launch_id       BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    rule_id         BIGINT NOT NULL REFERENCES launchpad.compliance_rules(rule_id),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','in_progress','completed','not_applicable','blocked')),
    evidence_url    TEXT,
    notes           TEXT,
    completed_at    TIMESTAMPTZ,
    UNIQUE (launch_id, rule_id)
);
