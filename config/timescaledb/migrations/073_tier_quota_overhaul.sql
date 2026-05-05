-- =============================================================================
-- Migration 073: Tier & Quota System Overhaul
-- Adds max_parcels, max_entities_total to tenants.
-- Resets marketplace_modules gating to 4-tier model with single source of truth.
-- =============================================================================

BEGIN;

-- 1. Tenant quota columns
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS max_parcels INTEGER,
    ADD COLUMN IF NOT EXISTS max_entities_total INTEGER;

COMMENT ON COLUMN tenants.max_parcels IS 'Max AgriParcel count. NULL = unlimited.';
COMMENT ON COLUMN tenants.max_entities_total IS 'Aggregate cap for ALL NGSI-LD entities (Basic tier). NULL = no aggregate cap.';

-- 2. Tier defaults backfill — align existing tenants to canonical quotas.
--    Mapping: 0=basic, 1=pro, 2=premium, 3=enterprise.
UPDATE tenants SET
    max_users           = 1,
    max_sensors         = 0,
    max_robots          = 0,
    max_parcels         = 0,
    max_area_hectares   = 0.20,
    max_entities_total  = 2
WHERE plan_level = 0;

UPDATE tenants SET
    max_users           = 5,
    max_sensors         = 10,
    max_robots          = 2,
    max_parcels         = 5,
    max_area_hectares   = 50.00,
    max_entities_total  = NULL
WHERE plan_level = 1;

UPDATE tenants SET
    max_users           = 10,
    max_sensors         = 50,
    max_robots          = 5,
    max_parcels         = 20,
    max_area_hectares   = 500.00,
    max_entities_total  = NULL
WHERE plan_level = 2;

UPDATE tenants SET
    max_users           = NULL,
    max_sensors         = NULL,
    max_robots          = NULL,
    max_parcels         = NULL,
    max_area_hectares   = NULL,
    max_entities_total  = NULL
WHERE plan_level = 3;

-- 3. Module gating — single source of truth: required_plan_level
-- All modules available to any tier by default
UPDATE marketplace_modules SET required_plan_level = 0;

-- Only 3 modules are premium-or-above (required_plan_level=2)
UPDATE marketplace_modules SET required_plan_level = 2
    WHERE id IN ('odoo-erp', 'agrienergy', 'n8n-nkz');

-- Also make risks and robotics non-CORE in terms of gating (they should be ADDON_PAID)
-- This is handled by required_plan_level already — they stay at 0

-- 4. Drop redundant module columns (data migrated to required_plan_level above)
ALTER TABLE marketplace_modules
    DROP COLUMN IF EXISTS required_plan_type,
    DROP COLUMN IF EXISTS module_type,
    DROP COLUMN IF EXISTS pricing_tier;

-- 5. Activation codes: extend plan enum to support 'pro' if it doesn't already
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'activation_plan_type') THEN
        BEGIN
            ALTER TYPE activation_plan_type ADD VALUE 'pro';
        EXCEPTION WHEN duplicate_object THEN
            -- already exists, ok
        END;
    END IF;
END$$;

COMMIT;
