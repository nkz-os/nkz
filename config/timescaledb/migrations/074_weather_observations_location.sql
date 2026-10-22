-- =============================================================================
-- Migration 074: Add location GEOMETRY(Point, 4326) to weather_observations
--
-- Removes spatial dependency on catalog_municipalities.geom for KNN queries.
-- catalog_municipalities is retained as a name/code registry.
-- =============================================================================

-- 1. Add geometry column (nullable — backfilled below)
ALTER TABLE weather_observations
    ADD COLUMN IF NOT EXISTS location GEOMETRY(Point, 4326);

COMMENT ON COLUMN weather_observations.location IS
    'Spatial location of the weather observation (EPSG:4326). Populated from catalog_municipalities.geom on backfill.';

-- 2. GIST index for KNN spatial queries
CREATE INDEX IF NOT EXISTS idx_weather_observations_location
    ON weather_observations
    USING GIST (location);

-- 3. Backfill existing rows from catalog_municipalities

-- Phase A: direct copy from catalog_municipalities.geom (preferred)
UPDATE weather_observations wo
SET location = cm.geom
FROM catalog_municipalities cm
WHERE wo.municipality_code = cm.ine_code
  AND cm.geom IS NOT NULL
  AND wo.location IS NULL;

-- Phase B: construct geometry from lat/lon columns when geom is NULL
UPDATE weather_observations wo
SET location = ST_SetSRID(ST_MakePoint(cm.longitude, cm.latitude), 4326)
FROM catalog_municipalities cm
WHERE wo.municipality_code = cm.ine_code
  AND cm.geom IS NULL
  AND cm.latitude IS NOT NULL
  AND cm.longitude IS NOT NULL
  AND wo.location IS NULL;
