-- =============================================================================
-- 079: tenant_weather_locations → international (lat/lon + location_name)
-- =============================================================================
-- Eliminates hard dependency on catalog_municipalities for climate routing.
-- Enables EU/international tenants to configure weather locations by
-- coordinates instead of Spanish INE municipality codes.
-- =============================================================================

BEGIN;

-- 1. Add new columns for coordinate-based locations
ALTER TABLE public.tenant_weather_locations
  ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS location_name TEXT;

-- 2. Backfill existing rows from catalog_municipalities
--    Preserves current tenant configurations — nobody loses their setup.
UPDATE public.tenant_weather_locations twl
SET
  latitude = cm.latitude,
  longitude = cm.longitude,
  location_name = COALESCE(twl.label, cm.name)
FROM public.catalog_municipalities cm
WHERE twl.municipality_code = cm.ine_code
  AND twl.latitude IS NULL;

-- 3. Drop existing FK (ON DELETE RESTRICT → ON DELETE SET NULL)
ALTER TABLE public.tenant_weather_locations
  DROP CONSTRAINT IF EXISTS tenant_weather_locations_municipality_code_fkey;

-- 4. Re-add FK with ON DELETE SET NULL
--    If a municipality is deleted from the catalog, the weather location
--    remains (coordinates are preserved) but municipality_code becomes NULL.
ALTER TABLE public.tenant_weather_locations
  ADD CONSTRAINT tenant_weather_locations_municipality_code_fkey
    FOREIGN KEY (municipality_code)
    REFERENCES public.catalog_municipalities(ine_code)
    ON DELETE SET NULL;

-- 5. Make municipality_code nullable
--    New international tenants won't have a Spanish municipality code.
--    The unique index (tenant_id, municipality_code) still works:
--    PostgreSQL treats NULLs as distinct, allowing multiple coordinate-based
--    locations per tenant (desirable for multiple weather stations).
ALTER TABLE public.tenant_weather_locations
  ALTER COLUMN municipality_code DROP NOT NULL;

-- 6. Add index for coordinate-based location queries
CREATE INDEX IF NOT EXISTS idx_weather_locations_coords
  ON public.tenant_weather_locations (tenant_id, latitude, longitude);

COMMIT;
