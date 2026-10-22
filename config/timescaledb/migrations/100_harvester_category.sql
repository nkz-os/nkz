-- =============================================================================
-- Migration 100: Harvester profiles receive their own machine category
-- =============================================================================
-- Five profiles migrated from AgriOperation (migration 099) emit field-level
-- operation telemetry: yield, fuelConsumption, areaWorked, grainLosses, workQuality.
--
-- A combine harvester is self-propelled machinery, distinct from:
--   tractor (primary mover, no specialized operation)
--   implement (specialized tool, towed or integral)
--   robot (autonomous or semi-autonomous machinery)
--
-- Research against the official FIWARE Smart Data Models catalogue found no
-- canonical type or enum value for harvesting machinery, and none of these five
-- telemetry fields appear in any published SDM schema. This is a declared platform
-- extension (documented in docs/development/SDM_EXTENSIONS.md).
--
-- Mapping:
--   operation_area_worked     → sdm_device_category = 'harvester'
--   operation_fuel_consumption→ sdm_device_category = 'harvester'
--   operation_grain_losses    → sdm_device_category = 'harvester'
--   operation_work_quality    → sdm_device_category = 'harvester'
--   operation_yield           → sdm_device_category = 'harvester'
--
-- Idempotent (Expand only): each UPDATE carries a WHERE clause matching both
-- the code AND the current NULL value, so rerunning touches only rows still
-- waiting for this migration. Migration 099 has already rewritten sdm_entity_type
-- to 'ManufacturingMachine', so we cannot select by the old type — we identify
-- by code instead.
-- =============================================================================

UPDATE sensor_profiles
SET sdm_device_category = 'harvester'
WHERE code = 'operation_area_worked'
  AND sdm_device_category IS NULL;

UPDATE sensor_profiles
SET sdm_device_category = 'harvester'
WHERE code = 'operation_fuel_consumption'
  AND sdm_device_category IS NULL;

UPDATE sensor_profiles
SET sdm_device_category = 'harvester'
WHERE code = 'operation_grain_losses'
  AND sdm_device_category IS NULL;

UPDATE sensor_profiles
SET sdm_device_category = 'harvester'
WHERE code = 'operation_work_quality'
  AND sdm_device_category IS NULL;

UPDATE sensor_profiles
SET sdm_device_category = 'harvester'
WHERE code = 'operation_yield'
  AND sdm_device_category IS NULL;

-- =============================================================================
-- End of migration 100
-- =============================================================================
