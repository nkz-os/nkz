-- =============================================================================
-- Migration 076: Consolidate Tenant Limits into tenants table
-- =============================================================================
-- Eliminates admin_platform.tenant_limits as a separate source of truth.
-- All limit columns now live ONLY in the tenants table.
-- tier_quotas.py remains the canonical source for defaults.

BEGIN;

-- 1. Ensure ALL limit columns exist on tenants
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS max_users INTEGER,
    ADD COLUMN IF NOT EXISTS max_robots INTEGER,
    ADD COLUMN IF NOT EXISTS max_sensors INTEGER,
    ADD COLUMN IF NOT EXISTS max_area_hectares REAL,
    ADD COLUMN IF NOT EXISTS max_parcels INTEGER,
    ADD COLUMN IF NOT EXISTS max_entities_total INTEGER;

-- 2. Migrate data from admin_platform.tenant_limits → tenants (where tenants has NULLs)
UPDATE tenants t
SET
    max_users = COALESCE(t.max_users, tl.max_users),
    max_robots = COALESCE(t.max_robots, tl.max_robots),
    max_sensors = COALESCE(t.max_sensors, tl.max_sensors),
    max_area_hectares = COALESCE(t.max_area_hectares, tl.max_area_hectares)
FROM admin_platform.tenant_limits tl
WHERE t.tenant_id = tl.tenant_id
  AND (
    t.max_users IS NULL
    OR t.max_robots IS NULL
    OR t.max_sensors IS NULL
    OR t.max_area_hectares IS NULL
  );

-- 3. Drop the redundant table (data has been migrated above)
DROP TABLE IF EXISTS admin_platform.tenant_limits;

-- 4. Drop orphaned tenant_capabilities table (never used by any code)
DROP TABLE IF EXISTS admin_platform.tenant_capabilities;

COMMIT;
