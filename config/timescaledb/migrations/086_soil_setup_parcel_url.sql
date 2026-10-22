-- 086_soil_setup_parcel_url.sql
-- Configure setup_parcel_url for the soil module so the parcel-reconcile
-- engine can trigger soil ingestion when a parcel is activated.
-- Idempotent: safe to re-run.

UPDATE marketplace_modules
SET metadata = COALESCE(metadata, '{}') || 
    '{"setup_parcel_url": "http://soil-module-service:8000/v1/soil/internal/setup-parcel"}'::jsonb
WHERE id = 'soil'
  AND (metadata->>'setup_parcel_url' IS NULL
       OR metadata->>'setup_parcel_url' != 'http://soil-module-service:8000/v1/soil/internal/setup-parcel');
