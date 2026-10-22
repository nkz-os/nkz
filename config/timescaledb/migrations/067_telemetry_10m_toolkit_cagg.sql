-- =============================================================================
-- Migration 067: PR-1 Toolkit + telemetry_10m continuous aggregate
-- =============================================================================
-- Purpose:
--   - Enable timescaledb_toolkit in the production analytics DB.
--   - Create first-level 10-minute CAGG over telemetry_measurements using two-step aggregates.
--   - Apply explicit freshness and compression policies.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit;

DO $$
BEGIN
    IF to_regclass('public.telemetry_10m') IS NULL THEN
        EXECUTE $DDL$
            CREATE MATERIALIZED VIEW telemetry_10m
            WITH (timescaledb.continuous) AS
            SELECT
                time_bucket(INTERVAL '10 minutes', observed_at) AS bucket,
                tenant_id,
                entity_id,
                attribute_name,
                stats_agg(value) AS stats_summary,
                percentile_agg(value) AS pct_summary
            FROM telemetry_measurements
            GROUP BY bucket, tenant_id, entity_id, attribute_name
            WITH NO DATA
        $DDL$;
    END IF;
END $$;

-- Explicitly prefer realtime union semantics for operational truth.
DO $$
BEGIN
    BEGIN
        EXECUTE 'ALTER MATERIALIZED VIEW telemetry_10m SET (timescaledb.materialized_only = false)';
    EXCEPTION WHEN others THEN
        RAISE NOTICE 'Could not set timescaledb.materialized_only explicitly: %', SQLERRM;
    END;
END $$;

-- Refresh policy: keep head close to realtime while limiting churn in very recent minutes.
SELECT add_continuous_aggregate_policy(
    'telemetry_10m',
    start_offset => INTERVAL '30 days',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);

-- Compression and policy for materialized chunks.
ALTER MATERIALIZED VIEW telemetry_10m SET (
    timescaledb.compress = true,
    timescaledb.compress_segmentby = 'tenant_id,entity_id,attribute_name',
    timescaledb.compress_orderby = 'bucket DESC'
);

SELECT add_compression_policy(
    'telemetry_10m',
    compress_after => INTERVAL '45 days',
    if_not_exists => TRUE
);

-- Seed first refresh window (bounded).
CALL refresh_continuous_aggregate(
    'telemetry_10m',
    now() - INTERVAL '30 days',
    now() - INTERVAL '5 minutes'
);

-- =============================================================================
-- End of migration 067
-- =============================================================================
