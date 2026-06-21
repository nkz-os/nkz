-- =============================================================================
-- Add raw value and calibration tracking columns to telemetry_events
-- =============================================================================
ALTER TABLE telemetry_events ADD COLUMN IF NOT EXISTS value_raw DOUBLE PRECISION;
ALTER TABLE telemetry_events ADD COLUMN IF NOT EXISTS calibration_period_id UUID;

COMMENT ON COLUMN telemetry_events.value_raw
  IS 'Original raw value as received from the device (before calibration transform)';
COMMENT ON COLUMN telemetry_events.calibration_period_id
  IS 'FK to calibration_periods — identifies which calibration was applied';
