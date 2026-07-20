-- =============================================================================
-- Migration 095: Resync tenant limits to canonical tier quotas
-- =============================================================================
-- Aligns existing tenants with services/common/tier_quotas.py defaults.
-- Idempotent: updates by plan_level and deleted_at IS NULL.

BEGIN;

-- Keep plan_type and plan_level synchronized.
UPDATE tenants SET plan_type = 'basic'
WHERE deleted_at IS NULL AND plan_level = 0 AND plan_type <> 'basic';

UPDATE tenants SET plan_type = 'pro'
WHERE deleted_at IS NULL AND plan_level = 1 AND plan_type <> 'pro';

UPDATE tenants SET plan_type = 'premium'
WHERE deleted_at IS NULL AND plan_level = 2 AND plan_type <> 'premium';

UPDATE tenants SET plan_type = 'enterprise'
WHERE deleted_at IS NULL AND plan_level = 3 AND plan_type <> 'enterprise';

-- basic defaults
UPDATE tenants SET
    max_users = 1,
    max_sensors = 0,
    max_robots = 0,
    max_parcels = 0,
    max_area_hectares = 0.20,
    max_entities_total = 2
WHERE deleted_at IS NULL AND plan_level = 0;

-- pro defaults
UPDATE tenants SET
    max_users = 5,
    max_sensors = 10,
    max_robots = 2,
    max_parcels = 5,
    max_area_hectares = 50.00,
    max_entities_total = NULL
WHERE deleted_at IS NULL AND plan_level = 1;

-- premium defaults
UPDATE tenants SET
    max_users = 10,
    max_sensors = 50,
    max_robots = 5,
    max_parcels = 20,
    max_area_hectares = 500.00,
    max_entities_total = NULL
WHERE deleted_at IS NULL AND plan_level = 2;

-- enterprise defaults (unlimited)
UPDATE tenants SET
    max_users = NULL,
    max_sensors = NULL,
    max_robots = NULL,
    max_parcels = NULL,
    max_area_hectares = NULL,
    max_entities_total = NULL
WHERE deleted_at IS NULL AND plan_level = 3;

COMMIT;
