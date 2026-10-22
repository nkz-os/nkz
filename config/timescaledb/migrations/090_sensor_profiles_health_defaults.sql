-- =============================================================================
-- Add health configuration defaults to sensor_profiles
-- =============================================================================
-- When a user selects a sensor profile in the wizard, the health thresholds
-- for each variable are pre-filled from this column (user can override them).
-- =============================================================================
ALTER TABLE sensor_profiles ADD COLUMN IF NOT EXISTS health_defaults JSONB;

COMMENT ON COLUMN sensor_profiles.health_defaults
  IS 'Default health thresholds per variable. E.g. {"temperature": {"minValid": -20, "maxValid": 60, "maxStagnantHours": 4}, "humidity": {"minValid": 0, "maxValid": 100, "maxStagnantHours": 6}}';
