-- =============================================================================
-- Migration 099: Canonical device profiles (Device and ManufacturingMachine)
-- =============================================================================
-- Migrate sensor_profiles from six application-specific entity types to two
-- FIWARE Smart Data Model canonical types: Device and ManufacturingMachine.
--
-- Mapping (row counts from production data):
--   AgriSensor (40) → Device
--   WeatherObserved (8) → Device
--   AgriculturalImplement (12) → ManufacturingMachine + sdm_device_category='implement'
--   AgriculturalRobot (10) → ManufacturingMachine + sdm_device_category='robot'
--   AgriculturalTractor (8) → ManufacturingMachine + sdm_device_category='tractor'
--   AgriOperation (5) → ManufacturingMachine + sdm_device_category=NULL
--
-- The new sdm_device_category column discriminates machine types for GIS
-- routing (tractor vs. implement). It is NULL for profiles that map to
-- Device and for AgriOperation (an open design question).
--
-- sdm_category is NOT modified: it holds domain groupings (isobus, robotics,
-- weather, soil, etc.) that other code filters on and is orthogonal to
-- the machine discriminator.
--
-- Three consumers read sdm_entity_type from this table:
--   entity-manager/blueprints/sensors.py
--   sdm-integration/device_profiles.py
--   telemetry-worker/telemetry_worker/sdm.py
-- Task 2 and 3 update the hardcoded fallbacks in those services.
--
-- Idempotent (Expand only): each UPDATE carries a WHERE clause, so
-- reaplicarla touches only rows still in the old value.
-- =============================================================================

-- Add the machine discriminator column. Profiles that become Device will
-- have NULL; only ManufacturingMachine profiles carry the discriminator.
ALTER TABLE IF EXISTS sensor_profiles
ADD COLUMN IF NOT EXISTS sdm_device_category TEXT;

COMMENT ON COLUMN sensor_profiles.sdm_device_category IS
    'Machine type discriminator (tractor, implement, robot) for gis-routing. NULL for Device profiles and AgriOperation. Orthogonal to sdm_category (domain grouping).';

-- Device profiles (Sensors): AgriSensor and WeatherObserved.
-- sdm_device_category stays NULL for these.
UPDATE sensor_profiles
SET sdm_entity_type = 'Device'
WHERE sdm_entity_type = 'AgriSensor';

UPDATE sensor_profiles
SET sdm_entity_type = 'Device'
WHERE sdm_entity_type = 'WeatherObserved';

-- ManufacturingMachine profiles.
UPDATE sensor_profiles
SET sdm_entity_type = 'ManufacturingMachine', sdm_device_category = 'tractor'
WHERE sdm_entity_type = 'AgriculturalTractor';

UPDATE sensor_profiles
SET sdm_entity_type = 'ManufacturingMachine', sdm_device_category = 'implement'
WHERE sdm_entity_type = 'AgriculturalImplement';

UPDATE sensor_profiles
SET sdm_entity_type = 'ManufacturingMachine', sdm_device_category = 'robot'
WHERE sdm_entity_type = 'AgriculturalRobot';

-- AgriOperation: maps to ManufacturingMachine but sdm_device_category = NULL
-- (open question: these emit operation-level telemetry, not apparatus telemetry).
UPDATE sensor_profiles
SET sdm_entity_type = 'ManufacturingMachine'
WHERE sdm_entity_type = 'AgriOperation';

-- =============================================================================
-- End of migration 099
-- =============================================================================
