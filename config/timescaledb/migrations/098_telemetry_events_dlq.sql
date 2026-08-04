-- =============================================================================
-- Migration 098: Dead-letter queue for poison telemetry records
-- =============================================================================
-- telemetry-worker's PostgreSQLSink.write_batch() (services/telemetry-worker/
-- telemetry_worker/event_sink.py) COPYs a batch into telemetry_events and,
-- on COPY failure, falls back to per-record inserts. Previously that fallback
-- ran the whole batch inside one conn.transaction(): a single poison record
-- (constraint violation, malformed payload, etc.) rolled back every good
-- record in the batch and the exception propagated, so the notification
-- retried forever on the same bad record.
--
-- This table lets the sink isolate a poison record instead: the good records
-- in the batch persist to telemetry_events individually, and only the record
-- that failed its own insert is captured here with the error, so ingestion
-- keeps moving and the bad record is available for operator review/replay.
--
-- Not a new direct-write path around Orion-LD: every record here already
-- arrived via the legitimate Orion-LD subscription -> telemetry-worker flow
-- and was already destined for telemetry_events; this table only holds the
-- subset that failed to persist there.
--
-- Columns mirror exactly what PostgreSQLSink.COLUMNS writes to
-- telemetry_events (tenant_id, observed_at, device_id, entity_id,
-- entity_type, payload, quality_flag) -- NOT the full historical
-- telemetry_events schema, which carries legacy columns (sensor_id,
-- profile_code, task_id, value_raw, calibration_period_id, metadata, ...)
-- this write path never populates. All columns are nullable here (a poison
-- record's own NOT NULL/constraint violation on telemetry_events is often
-- exactly why it landed in the DLQ).
--
-- Low-volume operator-review table: intentionally NOT a hypertable.
-- Idempotent (Expand only).
-- =============================================================================

CREATE TABLE IF NOT EXISTS telemetry_events_dlq (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT,
    observed_at TIMESTAMPTZ,
    device_id TEXT,
    entity_id TEXT,
    entity_type TEXT,
    payload JSONB,
    quality_flag TEXT,
    error_message TEXT,
    failed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE telemetry_events_dlq IS
    'Dead-letter queue for telemetry_events records that failed individual insert after a batch COPY failure in telemetry-worker PostgreSQLSink.write_batch(). Records already passed through the legitimate Orion-LD subscription write path; isolating them here lets the rest of the batch persist instead of the whole batch failing on one poison record.';

COMMENT ON COLUMN telemetry_events_dlq.error_message
    IS 'str(exception) raised by the individual INSERT attempt against telemetry_events.';

COMMENT ON COLUMN telemetry_events_dlq.failed_at
    IS 'When the record was dead-lettered (not observed_at, which is the original telemetry timestamp).';

-- Operator review access patterns: most recent failures first, optionally
-- scoped to a tenant.
CREATE INDEX IF NOT EXISTS idx_telemetry_events_dlq_failed_at
    ON telemetry_events_dlq (failed_at DESC);

CREATE INDEX IF NOT EXISTS idx_telemetry_events_dlq_tenant
    ON telemetry_events_dlq (tenant_id, failed_at DESC);

-- =============================================================================
-- End of migration 098
-- =============================================================================
