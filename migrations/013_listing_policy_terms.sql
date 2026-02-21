SET ROLE launchpad_admin;

CREATE TABLE IF NOT EXISTS launchpad.listing_policy_terms (
    term_id BIGSERIAL PRIMARY KEY,
    scope TEXT NOT NULL CHECK (scope IN ('global', 'eu_uk')),
    term TEXT NOT NULL,
    term_normalized TEXT GENERATED ALWAYS AS (lower(btrim(term))) STORED,
    notes TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_listing_policy_scope_term UNIQUE (scope, term_normalized)
);

CREATE INDEX IF NOT EXISTS idx_listing_policy_terms_scope_active
    ON launchpad.listing_policy_terms(scope, is_active, updated_at DESC);

INSERT INTO launchpad.listing_policy_terms (scope, term, notes, is_active)
VALUES
    ('eu_uk', 'physician recommended', 'seed default', TRUE),
    ('eu_uk', 'physician-recommended', 'seed default', TRUE),
    ('eu_uk', 'doctor recommended', 'seed default', TRUE),
    ('eu_uk', 'clinically proven', 'seed default', TRUE),
    ('eu_uk', 'clinically tested', 'seed default', TRUE),
    ('eu_uk', 'medical grade', 'seed default', TRUE),
    ('eu_uk', 'therapeutic', 'seed default', TRUE),
    ('eu_uk', 'heals', 'seed default', TRUE),
    ('eu_uk', 'cures', 'seed default', TRUE),
    ('eu_uk', 'treats', 'seed default', TRUE),
    ('eu_uk', 'prevents disease', 'seed default', TRUE),
    ('eu_uk', 'BPA free', 'seed default', TRUE),
    ('eu_uk', 'BPA-free', 'seed default', TRUE),
    ('eu_uk', 'phthalate free', 'seed default', TRUE),
    ('eu_uk', 'phthalate-free', 'seed default', TRUE),
    ('eu_uk', 'non toxic', 'seed default', TRUE),
    ('eu_uk', 'non-toxic', 'seed default', TRUE),
    ('eu_uk', 'chemical free', 'seed default', TRUE),
    ('eu_uk', 'chemical-free', 'seed default', TRUE),
    ('global', '#1', 'seed default', TRUE),
    ('global', 'number one', 'seed default', TRUE),
    ('global', 'best seller', 'seed default', TRUE),
    ('global', 'guaranteed', 'seed default', TRUE),
    ('global', 'risk free', 'seed default', TRUE),
    ('global', '100% safe', 'seed default', TRUE),
    ('global', 'FDA approved', 'seed default', TRUE),
    ('global', 'CE certified', 'seed default', TRUE),
    ('global', 'genuine', 'seed default', TRUE),
    ('global', 'authentic', 'seed default', TRUE),
    ('global', 'free shipping', 'seed default', TRUE),
    ('global', 'click here', 'seed default', TRUE),
    ('global', 'buy now', 'seed default', TRUE),
    ('global', 'limited time', 'seed default', TRUE),
    ('global', 'cancer', 'seed default', TRUE),
    ('global', 'arthritis', 'seed default', TRUE),
    ('global', 'diabetes', 'seed default', TRUE),
    ('global', 'covid', 'seed default', TRUE),
    ('global', 'aspirin', 'seed default', TRUE),
    ('global', 'ibuprofen', 'seed default', TRUE),
    ('global', 'tylenol', 'seed default', TRUE),
    ('global', 'nike', 'seed default', TRUE),
    ('global', 'adidas', 'seed default', TRUE),
    ('global', 'apple', 'seed default', TRUE),
    ('global', 'samsung', 'seed default', TRUE),
    ('global', 'amazon basics', 'seed default', TRUE)
ON CONFLICT (scope, term_normalized)
DO UPDATE SET
    is_active = TRUE,
    notes = EXCLUDED.notes,
    updated_at = now();
