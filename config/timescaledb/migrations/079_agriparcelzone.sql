-- 079_agriparcelzone.sql
-- Create hypertable for AgriParcelZone entities.
-- The subscription from Orion-LD writes into this table.
-- Idempotent: can be run multiple times safely.

BEGIN;

CREATE TABLE IF NOT EXISTS agriparcelzone (
    observed_at TIMESTAMPTZ NOT NULL,
    entity_id TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'AgriParcelZone',
    has_agri_parcel TEXT,
    nkz_zone_id TEXT,
    nkz_centroid DOUBLE PRECISION[],
    nkz_elevation_mean DOUBLE PRECISION,
    nkz_elevation_min DOUBLE PRECISION,
    nkz_elevation_max DOUBLE PRECISION,
    nkz_aspect_sector TEXT,
    nkz_pixel_count INTEGER,
    nkz_area_ha DOUBLE PRECISION,
    t_min DOUBLE PRECISION,
    t_max DOUBLE PRECISION,
    eto DOUBLE PRECISION,
    nkz_sensor_nearby TEXT,
    nkz_sensor_distance_m DOUBLE PRECISION,
    PRIMARY KEY (observed_at, entity_id)
);

SELECT create_hypertable('agriparcelzone', 'observed_at', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_agriparcelzone_parcel
    ON agriparcelzone (has_agri_parcel, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_agriparcelzone_zone
    ON agriparcelzone (has_agri_parcel, nkz_zone_id, observed_at DESC);

ALTER TABLE agriparcelzone SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'has_agri_parcel',
    timescaledb.compress_orderby = 'observed_at DESC'
);

SELECT add_compression_policy('agriparcelzone', INTERVAL '7 days') WHERE NOT EXISTS (
    SELECT 1 FROM timescaledb_information.jobs
    WHERE proc_name LIKE '%compression%' AND hypertable_name = 'agriparcelzone'
);

COMMIT;
