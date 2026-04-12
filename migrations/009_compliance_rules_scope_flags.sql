-- Add scope flags to compliance_rules for product category matching
-- These columns indicate which product categories each compliance rule applies to
ALTER TABLE launchpad.compliance_rules
ADD COLUMN applies_to_electrical BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_electronic BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_toy BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_batteries BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_radio_equipment BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_textile BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_furniture BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_food_contact BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_cosmetic BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_chemical BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_medical BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_ppe BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_childcare BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_lighting BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_construction BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_machinery BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_pressure_equipment BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN applies_to_dpp_category BOOLEAN NOT NULL DEFAULT FALSE;
