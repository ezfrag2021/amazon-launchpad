SET ROLE launchpad_admin;

-- Expand image_type CHECK constraint to support the full slot-type catalogue.
-- The original 7 types are preserved; 7 new types are added for product categories
-- where dimensions/packaging are low-value (e.g. consumables, beauty, food).

ALTER TABLE launchpad.image_gallery
    DROP CONSTRAINT IF EXISTS image_gallery_image_type_check;

ALTER TABLE launchpad.image_gallery
    ADD CONSTRAINT image_gallery_image_type_check
    CHECK (image_type IN (
        -- Original 7
        'main_white_bg',
        'lifestyle',
        'infographic',
        'comparison',
        'dimensions',
        'packaging',
        'in_use',
        -- New types
        'before_after',      -- results transformation (beauty, cleaning, supplements)
        'ingredients',       -- key actives / ingredients callout (food, skincare, haircare)
        'how_to_use',        -- step-by-step usage (skincare routine, tools, appliances)
        'sensory',           -- texture / close-up sensory detail (food, cream, fabric)
        'certifications',    -- claims & badges (vegan, organic, dermatologist-tested)
        'social_proof',      -- review quotes / UGC style (any high-review product)
        'variant_range'      -- colour/size/scent range shot (multi-SKU products)
    ));
