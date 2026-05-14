-- Migration 076: agro_status_log for historical semaphore tracking
-- Stores each agro-status calculation for trend analysis and audit.

CREATE TABLE IF NOT EXISTS agro_status_log (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    parcel_id TEXT NOT NULL,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    spraying TEXT,
    workability TEXT,
    irrigation TEXT,
    source_confidence TEXT,
    soil_texture TEXT,
    field_capacity DOUBLE PRECISION,
    wilting_point DOUBLE PRECISION,
    delta_t DOUBLE PRECISION,
    water_balance DOUBLE PRECISION,
    downscaling_applied BOOLEAN DEFAULT FALSE,
    sensor_count INTEGER,
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_agro_status_log_parcel
    ON agro_status_log (tenant_id, parcel_id, calculated_at DESC);
