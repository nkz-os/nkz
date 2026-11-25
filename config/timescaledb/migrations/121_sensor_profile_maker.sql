-- =============================================================================
-- Migration 121: maker identity of a sensor profile
-- Idempotent, expand-only. Three optional columns that feed the official
-- DeviceModel (manufacturerName, brandName, modelName). A NULL column means the
-- profile declares nothing: the DeviceModel then says `generic` for the maker
-- and the brand and uses the profile code as model name. No default, no
-- backfill, and RLS on sensor_profiles is untouched.
-- =============================================================================

ALTER TABLE sensor_profiles ADD COLUMN IF NOT EXISTS manufacturer_name TEXT;
ALTER TABLE sensor_profiles ADD COLUMN IF NOT EXISTS brand_name TEXT;
ALTER TABLE sensor_profiles ADD COLUMN IF NOT EXISTS model_name TEXT;
