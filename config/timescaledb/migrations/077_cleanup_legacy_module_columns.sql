-- =============================================================================
-- Migration 077: Clean up legacy module columns
-- =============================================================================
-- Drops columns that were marked for removal in migration 073 but may still exist.
-- Also drops the orphaned can_install_module SQL function (replaced by Python code).

BEGIN;

-- Drop legacy columns if they still exist (073 should have dropped them)
ALTER TABLE marketplace_modules
    DROP COLUMN IF EXISTS required_plan_type,
    DROP COLUMN IF EXISTS module_type,
    DROP COLUMN IF EXISTS pricing_tier;

-- Drop unused SQL function (replaced by Python entity_manager_gating.py)
DROP FUNCTION IF EXISTS can_install_module(TEXT, TEXT);

COMMIT;
