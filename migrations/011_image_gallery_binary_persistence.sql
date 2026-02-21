SET ROLE launchpad_admin;

ALTER TABLE launchpad.image_gallery
    ADD COLUMN IF NOT EXISTS image_bytes BYTEA;
