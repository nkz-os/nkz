-- =============================================================================
-- Migration 068: PR-2 (part A) hierarchical 1-hour rollup CAGG
-- =============================================================================
-- Purpose:
--   - Create 1-hour hierarchical rollup CAGG over telemetry_10m.
--   - Enforce rollup-only semantics for toolkit states.
--
-- Preconditions:
--   - telemetry_10m must exist and be in finalized=true format.
--
-- Note:
--   - Daily CAGG with tenant-aware timezone boundaries is intentionally handled
--     in a separate migration once authoritative tenant timezone source is fixed.
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('public.telemetry_1h') IS NULL THEN
        EXECUTE $DDL$
            CREATE MATERIALIZED VIEW telemetry_1h
            WITH (timescaledb.continuous) AS
            SELECT
                time_bucket(INTERVAL '1 hour', bucket) AS bucket,
                tenant_id,
                entity_id,
                attribute_name,
                rollup(stats_summary) AS stats_summary,
                rollup(pct_summary) AS pct_summary
            FROM telemetry_10m
            GROUP BY
                time_bucket(INTERVAL '1 hour', bucket),
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
        EXECUTE 'ALTER MATERIALIZED VIEW telemetry_1h SET (timescaledb.materialized_only = false)';
    EXCEPTION WHEN others THEN
        RAISE NOTICE 'Could not set timescaledb.materialized_only explicitly: %', SQLERRM;
    END;
END $$;

SELECT add_continuous_aggregate_policy(
    'telemetry_1h',
    start_offset => INTERVAL '365 days',
    end_offset => INTERVAL '15 minutes',
    schedule_interval => INTERVAL '15 minutes',
    if_not_exists => TRUE
);

ALTER MATERIALIZED VIEW telemetry_1h SET (
    timescaledb.compress = true,
    timescaledb.compress_segmentby = 'tenant_id,entity_id,attribute_name',
    timescaledb.compress_orderby = 'bucket DESC'
);

SELECT add_compression_policy(
    'telemetry_1h',
    compress_after => INTERVAL '730 days',
    if_not_exists => TRUE
);

CALL refresh_continuous_aggregate(
    'telemetry_1h',
    now() - INTERVAL '365 days',
    now() - INTERVAL '15 minutes'
);

-- =============================================================================
-- End of migration 068
-- =============================================================================
