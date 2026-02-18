SET ROLE launchpad_admin;

-- Listing drafts (versioned)
CREATE TABLE launchpad.listing_drafts (
    draft_id            BIGSERIAL PRIMARY KEY,
    launch_id           BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    marketplace         VARCHAR(5) NOT NULL,
    version             INTEGER NOT NULL DEFAULT 1,
    title               TEXT,
    bullets             JSONB,          -- array of 5 bullet strings
    description         TEXT,
    backend_keywords    TEXT,           -- 250 byte limit for Amazon
    rufus_optimized     BOOLEAN NOT NULL DEFAULT FALSE,
    a_plus_content      JSONB,          -- structured A+ Content modules
    generated_by        VARCHAR(50),    -- model name (e.g., 'gemini-2.0-flash')
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (launch_id, marketplace, version)
);

-- Image gallery
CREATE TABLE launchpad.image_gallery (
    image_id        BIGSERIAL PRIMARY KEY,
    launch_id       BIGINT NOT NULL REFERENCES launchpad.product_launches(launch_id),
    slot_number     SMALLINT NOT NULL CHECK (slot_number BETWEEN 1 AND 7),
    image_type      VARCHAR(20) NOT NULL
                    CHECK (image_type IN ('main_white_bg','lifestyle','infographic','comparison','dimensions','packaging','in_use')),
    prompt_used     TEXT,
    storage_path    TEXT,               -- local or S3 path
    model_used      VARCHAR(50),        -- e.g., 'imagen-3'
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (launch_id, slot_number)
);
