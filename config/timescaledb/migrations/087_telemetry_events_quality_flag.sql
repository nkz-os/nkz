-- =============================================================================
-- Add quality_flag column to telemetry_events hypertable
-- =============================================================================
ALTER TABLE telemetry_events ADD COLUMN IF NOT EXISTS quality_flag TEXT;

COMMENT ON COLUMN telemetry_events.quality_flag
  IS 'Data quality indicator: valid, out_of_bounds, nan, stale. NULL = legacy data.';
