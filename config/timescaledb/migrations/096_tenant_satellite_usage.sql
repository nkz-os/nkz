-- =============================================================================
-- Migration 096: Satellite-computation quota (Vegetation-Health BYOK)
-- =============================================================================
-- Adds the satellite-processing quota to the canonical tier model so a later
-- task can cap monthly Copernicus/Sentinel-Hub index computations per tenant
-- and surface the limit in the UI. Canonical values live in
-- services/common/tier_quotas.py: max_satellite_computations =
-- basic 0, pro 100, premium 500, enterprise NULL (unlimited).
--
-- NOTE on table target: the tenant limit columns (max_users, max_parcels,
-- max_entities_total, ...) live directly on the `tenants` table, NOT on a
-- separate `tenant_limits` table. `admin_platform.tenant_limits` was dropped
-- in migration 076 (076_consolidate_tenant_limits.sql) -- "All limit columns
-- now live ONLY in the tenants table." This migration follows that same
-- pattern: ALTER TABLE tenants, not tenant_limits.
--
-- Idempotent: IF NOT EXISTS / additive only (Expand, no Contract).

BEGIN;

-- 1. New usage-tracking table: computations consumed per tenant per month.
CREATE TABLE IF NOT EXISTS tenant_satellite_usage (
    tenant_id TEXT NOT NULL,
    period_month DATE NOT NULL,
    computations INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, period_month)
);

-- 2. New limit column on tenants (mirrors max_parcels / max_entities_total).
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS max_satellite_computations INTEGER;

-- 3. Resync existing tenants to the canonical tier defaults, mirroring the
--    per-plan_level pattern in 095_resync_tenant_limits_to_tier_quotas.sql.
--    Not folded into 095 itself: migrations are applied once per file by the
--    ops runbook (no schema_migrations tracking table -- see
--    docker/run-migrations.sh / DEPLOYMENT.md), so editing an already-applied
--    095 would never re-execute against tenants created before this change.
UPDATE tenants SET max_satellite_computations = 0
WHERE deleted_at IS NULL AND plan_level = 0;

UPDATE tenants SET max_satellite_computations = 100
WHERE deleted_at IS NULL AND plan_level = 1;

UPDATE tenants SET max_satellite_computations = 500
WHERE deleted_at IS NULL AND plan_level = 2;

UPDATE tenants SET max_satellite_computations = NULL
WHERE deleted_at IS NULL AND plan_level = 3;

COMMIT;
