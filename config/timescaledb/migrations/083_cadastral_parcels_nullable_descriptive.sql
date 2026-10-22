-- 083_cadastral_parcels_nullable_descriptive.sql
-- The cadastral_parcels read-model was originally designed for the Spanish
-- cadastre flow, which always supplies municipality/province. As the canonical
-- read-model projected from Orion AgriParcel (single source of truth), it must
-- accept parcels that legitimately lack these descriptive fields (management
-- zones, foreign parcels, parcels created before address enrichment).
-- Without this, the projection raised
--   NotNullViolation: null value in column "municipality"/"province"
-- and rows were never written. Make the descriptive columns nullable.
-- Idempotent: DROP NOT NULL is a no-op if already nullable.

ALTER TABLE cadastral_parcels ALTER COLUMN municipality DROP NOT NULL;
ALTER TABLE cadastral_parcels ALTER COLUMN province DROP NOT NULL;
