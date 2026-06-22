-- =============================================================================
-- Add raw value and calibration tracking columns to telemetry_events
-- =============================================================================
-- NOTE: Telemetry events can contain multiple measurements (e.g. temperature +
-- humidity in one event). The per-measurement raw values are stored inside the
-- payload JSONB column as payload->'raw_measurements'->>'variable' and
-- payload->'calibration_period_ids'->>'variable'. These top-level columns are
-- reserved for single-measurement events or aggregate use.
-- =============================================================================
ALTER TABLE telemetry_events ADD COLUMN IF NOT EXISTS value_raw DOUBLE PRECISION;
ALTER TABLE telemetry_events ADD COLUMN IF NOT EXISTS calibration_period_id UUID;

COMMENT ON COLUMN telemetry_events.value_raw
  IS 'Raw value for single-measurement events. For multi-measurement events, see payload->raw_measurements.';
COMMENT ON COLUMN telemetry_events.calibration_period_id
  IS 'Calibration period for single-measurement events. For multi-measurement events, see payload->calibration_period_ids.';
