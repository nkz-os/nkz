-- 082_fix_parcel_metrics_round.sql
-- Fix: calculate_parcel_metrics() called ROUND(double precision, integer), which
-- does not exist in PostgreSQL (ROUND(v, s) is only defined for numeric). As a
-- result the parcel_metrics_trigger raised
--   "function round(double precision, integer) does not exist"
-- on EVERY INSERT/UPDATE into cadastral_parcels, which is why the read-model
-- mirror could never be populated (table stayed empty). Cast to numeric.
-- Idempotent (CREATE OR REPLACE).

CREATE OR REPLACE FUNCTION calculate_parcel_metrics()
RETURNS TRIGGER AS $$
BEGIN
    -- Calculate area in hectares (cast to numeric: ROUND(double, int) is invalid)
    NEW.area_hectares := ROUND((ST_Area(NEW.geometry::geography) / 10000)::numeric, 4);

    -- Calculate centroid
    NEW.centroid := ST_Centroid(NEW.geometry);

    -- Update timestamp
    NEW.updated_at := CURRENT_TIMESTAMP;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
