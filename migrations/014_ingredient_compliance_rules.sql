SET ROLE launchpad_admin;

CREATE TABLE IF NOT EXISTS launchpad.ingredient_registry (
    ingredient_id BIGSERIAL PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    cas_number VARCHAR(64),
    ec_number VARCHAR(64),
    synonyms TEXT[] NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ingredient_registry_normalized_name UNIQUE (normalized_name)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ingredient_registry_cas_unique
    ON launchpad.ingredient_registry (cas_number)
    WHERE cas_number IS NOT NULL;

CREATE TABLE IF NOT EXISTS launchpad.ingredient_compliance_rules (
    ingredient_rule_id BIGSERIAL PRIMARY KEY,
    ingredient_id BIGINT NOT NULL REFERENCES launchpad.ingredient_registry(ingredient_id) ON DELETE CASCADE,
    jurisdiction VARCHAR(10) NOT NULL,
    product_category VARCHAR(120) NOT NULL DEFAULT 'all',
    product_subtype VARCHAR(120) NOT NULL DEFAULT '',
    rule_type VARCHAR(40) NOT NULL,
    max_concentration NUMERIC(12,6),
    max_unit VARCHAR(20),
    basis VARCHAR(20),
    condition_text TEXT,
    exceptions_text TEXT,
    source_title TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_clause TEXT,
    effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to DATE,
    rule_version VARCHAR(40) NOT NULL DEFAULT '1.0',
    last_reviewed_at DATE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_ing_rule_jurisdiction CHECK (jurisdiction IN ('EU', 'UK')),
    CONSTRAINT ck_ing_rule_type CHECK (
        rule_type IN ('max_concentration', 'prohibited', 'restricted_conditionally', 'allowed')
    ),
    CONSTRAINT ck_ing_rule_dates CHECK (effective_to IS NULL OR effective_to >= effective_from),
    CONSTRAINT ck_ing_rule_max_required CHECK (
        (rule_type = 'max_concentration' AND max_concentration IS NOT NULL)
        OR (rule_type <> 'max_concentration')
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ing_rules_unique_versioned
    ON launchpad.ingredient_compliance_rules (
        ingredient_id,
        jurisdiction,
        product_category,
        product_subtype,
        rule_type,
        effective_from
    );

CREATE INDEX IF NOT EXISTS idx_ing_rules_lookup
    ON launchpad.ingredient_compliance_rules (jurisdiction, product_category, product_subtype, is_active);

CREATE INDEX IF NOT EXISTS idx_ing_rules_ingredient
    ON launchpad.ingredient_compliance_rules (ingredient_id);
