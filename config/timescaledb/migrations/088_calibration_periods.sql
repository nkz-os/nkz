-- =============================================================================
-- Create calibration_periods table for time-ranged sensor calibration history
-- =============================================================================
CREATE TABLE IF NOT EXISTS calibration_periods (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sensor_id         TEXT NOT NULL,
    tenant_id         TEXT NOT NULL,
    variable          TEXT NOT NULL,
    slope             DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    offset_val        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    valid_from        TIMESTAMPTZ NOT NULL,
    valid_to          TIMESTAMPTZ,
    sensor_hardware_id TEXT NOT NULL,
    notes             TEXT,
    created_by        TEXT,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookup of active period
CREATE INDEX IF NOT EXISTS idx_calibration_periods_active
    ON calibration_periods (sensor_id, variable, valid_from)
    WHERE valid_to IS NULL;

-- Index for historical queries
CREATE INDEX IF NOT EXISTS idx_calibration_periods_sensor
    ON calibration_periods (sensor_id, variable, valid_from DESC);
