-- =============================================================================
-- Migration 069: PR-2 (part B) daily layer + tenant timezone catalog
-- =============================================================================
-- Purpose:
--   - Introduce authoritative tenant timezone mapping table.
--   - Create UTC daily hierarchical CAGG (telemetry_1d) from telemetry_1h.
--   - Expose tenant-local daily aggregation view using timezone-aware buckets.
--
-- Note:
--   - In TimescaleDB 2.10.x, hierarchical CAGG validation accepts strict
--     bucket lineage patterns; timezone-parameterized time_bucket in CAGG
--     creation can fail validator checks. Therefore:
--       * telemetry_1d is the canonical persisted UTC daily rollup.
--       * telemetry_1d_tenant_localized provides tenant-timezone daily grouping
--         at query time over telemetry_1h state summaries.
-- =============================================================================

CREATE TABLE IF NOT EXISTS admin_platform.tenant_timezones (
    tenant_id TEXT PRIMARY KEY,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Seed missing tenants with UTC.
INSERT INTO admin_platform.tenant_timezones (tenant_id, timezone)
SELECT t.tenant_id, 'UTC'
FROM tenants t
ON CONFLICT (tenant_id) DO NOTHING;

DO $$
BEGIN
    IF to_regclass('public.telemetry_1d') IS NULL THEN
        EXECUTE $DDL$
            CREATE MATERIALIZED VIEW telemetry_1d
            WITH (timescaledb.continuous) AS
            SELECT
                time_bucket(INTERVAL '1 day', bucket) AS bucket,
                tenant_id,
                entity_id,
                attribute_name,
                rollup(stats_summary) AS stats_summary,
                rollup(pct_summary) AS pct_summary
            FROM telemetry_1h
            GROUP BY
                time_bucket(INTERVAL '1 day', bucket),
                tenant_id,
                entity_id,
                attribute_name
            WITH NO DATA
        $DDL$;
    END IF;
END $$;

DO $$
BEGIN
    BEGIN
        EXECUTE 'ALTER MATERIALIZED VIEW telemetry_1d SET (timescaledb.materialized_only = false)';
    EXCEPTION WHEN others THEN
        RAISE NOTICE 'Could not set timescaledb.materialized_only explicitly: %', SQLERRM;
    END;
END $$;

SELECT add_continuous_aggregate_policy(
    'telemetry_1d',
    start_offset => INTERVAL '1825 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

ALTER MATERIALIZED VIEW telemetry_1d SET (
    timescaledb.compress = true,
    timescaledb.compress_segmentby = 'tenant_id,entity_id,attribute_name',
    timescaledb.compress_orderby = 'bucket DESC'
);

SELECT add_compression_policy(
    'telemetry_1d',
    compress_after => INTERVAL '3650 days',
    if_not_exists => TRUE
);

CALL refresh_continuous_aggregate(
    'telemetry_1d',
    now() - INTERVAL '1825 days',
    now() - INTERVAL '1 hour'
);

-- Tenant-local daily view (query-time timezone semantics, DST-aware via timezone arg).
CREATE OR REPLACE VIEW telemetry_1d_tenant_localized AS
SELECT
    time_bucket(
        INTERVAL '1 day',
        h.bucket,
        tz.timezone,
        TIMESTAMPTZ '2000-01-01 00:00:00+00',
        INTERVAL '0'
    ) AS local_day_bucket,
    h.tenant_id,
    h.entity_id,
    h.attribute_name,
    rollup(h.stats_summary) AS stats_summary,
    rollup(h.pct_summary) AS pct_summary
FROM telemetry_1h h
JOIN admin_platform.tenant_timezones tz
  ON tz.tenant_id = h.tenant_id
GROUP BY
    time_bucket(
        INTERVAL '1 day',
        h.bucket,
        tz.timezone,
        TIMESTAMPTZ '2000-01-01 00:00:00+00',
        INTERVAL '0'
    ),
    h.tenant_id,
    h.entity_id,
    h.attribute_name;

-- =============================================================================
-- End of migration 069
-- =============================================================================
