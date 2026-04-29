-- =============================================================================
-- Migration 066: Canonical long-format telemetry_measurements (PR-0 bridge)
-- =============================================================================
-- Purpose:
--   - Introduce analytics-friendly long-format table for telemetry values.
--   - Keep migration DDL-only and lightweight (no historical mass backfill).
--   - Add trigger-based incremental sync from telemetry_events for new writes.
--
-- Notes:
--   - Historical backfill must run as asynchronous chunked job outside this migration.
--   - Numeric extraction accepts finite decimal/scientific notation values only.
-- =============================================================================

CREATE TABLE IF NOT EXISTS telemetry_measurements (
    tenant_id TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    source_event_id BIGINT NOT NULL,
    entity_id TEXT,
    entity_type TEXT,
    device_id TEXT,
    sensor_id UUID,
    task_id TEXT,
    attribute_name TEXT NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    source TEXT NOT NULL DEFAULT 'telemetry_events',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, observed_at, source_event_id, attribute_name)
);

DO $$
BEGIN
    PERFORM create_hypertable(
        'telemetry_measurements',
        'observed_at',
        if_not_exists => TRUE,
        migrate_data => FALSE
    );
END $$;

CREATE INDEX IF NOT EXISTS idx_telemetry_measurements_tenant_entity_attr_time
    ON telemetry_measurements (tenant_id, entity_id, attribute_name, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_telemetry_measurements_tenant_attr_time
    ON telemetry_measurements (tenant_id, attribute_name, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_telemetry_measurements_device_time
    ON telemetry_measurements (device_id, observed_at DESC);

ALTER TABLE telemetry_measurements SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'tenant_id,entity_id,attribute_name',
    timescaledb.compress_orderby = 'observed_at DESC,source_event_id DESC'
);

SELECT add_compression_policy('telemetry_measurements', INTERVAL '7 days', if_not_exists => TRUE);

-- -----------------------------------------------------------------------------
-- Backfill progress state (used by async chunked script)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetry_measurements_backfill_state (
    job_name TEXT PRIMARY KEY,
    last_observed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- -----------------------------------------------------------------------------
-- Incremental sync trigger: telemetry_events -> telemetry_measurements
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sync_telemetry_event_measurements()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.payload ? 'measurements' THEN
        INSERT INTO telemetry_measurements (
            tenant_id,
            observed_at,
            source_event_id,
            entity_id,
            entity_type,
            device_id,
            sensor_id,
            task_id,
            attribute_name,
            value,
            source
        )
        SELECT
            NEW.tenant_id,
            NEW.observed_at,
            NEW.id,
            NEW.entity_id,
            NEW.entity_type,
            NEW.device_id,
            NEW.sensor_id,
            NEW.task_id,
            kv.key,
            kv.value_txt::double precision,
            'telemetry_events'
        FROM (
            SELECT key, trim(value) AS value_txt
            FROM jsonb_each_text(NEW.payload->'measurements')
        ) kv
        WHERE kv.value_txt ~ '^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$'
          AND lower(kv.value_txt) NOT IN ('nan', '+nan', '-nan', 'inf', '+inf', '-inf', 'infinity', '+infinity', '-infinity')
        ON CONFLICT (tenant_id, observed_at, source_event_id, attribute_name) DO NOTHING;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_sync_telemetry_event_measurements ON telemetry_events;
CREATE TRIGGER trg_sync_telemetry_event_measurements
AFTER INSERT ON telemetry_events
FOR EACH ROW
EXECUTE FUNCTION sync_telemetry_event_measurements();

-- =============================================================================
-- End of migration 066
-- =============================================================================
