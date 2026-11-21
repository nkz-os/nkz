-- =============================================================================
-- Migration 116: observation columns on telemetry_measurements + trigger update
-- =============================================================================
-- Purpose:
--   - Add nullable observation-context columns (contract 1.1, amendments 8.1/8.3).
--   - Replace sync_telemetry_event_measurements() (from 066) so it copies the
--     worker's payload.observations / event-level keys into those columns.
--
-- Constraints (TimescaleDB 2.10.2):
--   - ADD COLUMN only nullable, no DEFAULT; no CHECK or other constraint on the
--     compressed hypertable. Vocabularies are enforced by the trigger.
--   - the compression segmenting settings are NOT changed.
--   - The trigger 'trg_sync_telemetry_event_measurements' (066) is not recreated;
--     it already points at the function name replaced here.
--
-- Fail-safe: nothing here may raise and abort the INSERT into telemetry_events
-- because of a bad observation field. A bad value becomes NULL + WARNING.
-- Idempotent: safe to run repeatedly.
-- =============================================================================

ALTER TABLE telemetry_measurements ADD COLUMN IF NOT EXISTS observed_property TEXT;
ALTER TABLE telemetry_measurements ADD COLUMN IF NOT EXISTS feature_of_interest TEXT;
ALTER TABLE telemetry_measurements ADD COLUMN IF NOT EXISTS medium TEXT;
ALTER TABLE telemetry_measurements ADD COLUMN IF NOT EXISTS depth_from_cm DOUBLE PRECISION;
ALTER TABLE telemetry_measurements ADD COLUMN IF NOT EXISTS depth_to_cm DOUBLE PRECISION;
ALTER TABLE telemetry_measurements ADD COLUMN IF NOT EXISTS height_m DOUBLE PRECISION;
ALTER TABLE telemetry_measurements ADD COLUMN IF NOT EXISTS organ TEXT;
ALTER TABLE telemetry_measurements ADD COLUMN IF NOT EXISTS context_ref TEXT;
ALTER TABLE telemetry_measurements ADD COLUMN IF NOT EXISTS deployment_id UUID;
ALTER TABLE telemetry_measurements ADD COLUMN IF NOT EXISTS source_kind TEXT;
ALTER TABLE telemetry_measurements ADD COLUMN IF NOT EXISTS location GEOGRAPHY(Point, 4326);
ALTER TABLE telemetry_measurements ADD COLUMN IF NOT EXISTS time_source TEXT;
ALTER TABLE telemetry_measurements ADD COLUMN IF NOT EXISTS quality_flag TEXT;

CREATE INDEX IF NOT EXISTS idx_tm_tenant_property_time
    ON telemetry_measurements (tenant_id, observed_property, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_tm_tenant_foi_time
    ON telemetry_measurements (tenant_id, feature_of_interest, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_tm_location
    ON telemetry_measurements USING GIST (location);

-- -----------------------------------------------------------------------------
-- Safe field readers: never raise, return NULL + WARNING on a bad value.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nkz_obs_text(p_val JSONB, p_field TEXT)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_val IS NULL OR jsonb_typeof(p_val) = 'null' THEN
        RETURN NULL;
    END IF;
    IF jsonb_typeof(p_val) <> 'string' THEN
        RAISE WARNING 'observation field % rejected: %', p_field, left(p_val::text, 80);
        RETURN NULL;
    END IF;
    RETURN p_val #>> '{}';
END;
$$;

CREATE OR REPLACE FUNCTION nkz_obs_vocab(p_val JSONB, p_field TEXT, p_allowed TEXT[])
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_txt TEXT;
BEGIN
    IF p_val IS NULL OR jsonb_typeof(p_val) = 'null' THEN
        RETURN NULL;
    END IF;
    IF jsonb_typeof(p_val) = 'string' THEN
        v_txt := p_val #>> '{}';
        IF v_txt = ANY (p_allowed) THEN
            RETURN v_txt;
        END IF;
    END IF;
    RAISE WARNING 'observation field % rejected: %', p_field, left(p_val::text, 80);
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION nkz_obs_number(p_val JSONB, p_field TEXT)
RETURNS DOUBLE PRECISION
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_val IS NULL OR jsonb_typeof(p_val) = 'null' THEN
        RETURN NULL;
    END IF;
    IF jsonb_typeof(p_val) <> 'number' THEN
        RAISE WARNING 'observation field % rejected: %', p_field, left(p_val::text, 80);
        RETURN NULL;
    END IF;
    BEGIN
        RETURN (p_val #>> '{}')::double precision;
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'observation field % rejected: %', p_field, left(p_val::text, 80);
        RETURN NULL;
    END;
END;
$$;

CREATE OR REPLACE FUNCTION nkz_obs_uuid(p_val JSONB, p_field TEXT)
RETURNS UUID
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_val IS NULL OR jsonb_typeof(p_val) = 'null' THEN
        RETURN NULL;
    END IF;
    IF jsonb_typeof(p_val) <> 'string' THEN
        RAISE WARNING 'observation field % rejected: %', p_field, left(p_val::text, 80);
        RETURN NULL;
    END IF;
    BEGIN
        RETURN (p_val #>> '{}')::uuid;
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'observation field % rejected: %', p_field, left(p_val::text, 80);
        RETURN NULL;
    END;
END;
$$;

CREATE OR REPLACE FUNCTION nkz_obs_point(p_val JSONB)
RETURNS GEOGRAPHY
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_val IS NULL OR jsonb_typeof(p_val) = 'null' THEN
        RETURN NULL;
    END IF;
    IF jsonb_typeof(p_val) <> 'object' THEN
        RAISE WARNING 'observation field % rejected: %', 'location', left(p_val::text, 80);
        RETURN NULL;
    END IF;
    BEGIN
        IF p_val->>'type' IS DISTINCT FROM 'Point'
           OR jsonb_typeof(p_val->'coordinates') IS DISTINCT FROM 'array'
           OR jsonb_array_length(p_val->'coordinates') < 2
           OR jsonb_typeof(p_val->'coordinates'->0) IS DISTINCT FROM 'number'
           OR jsonb_typeof(p_val->'coordinates'->1) IS DISTINCT FROM 'number'
           OR abs((p_val->'coordinates'->>0)::double precision) > 180
           OR abs((p_val->'coordinates'->>1)::double precision) > 90 THEN
            RAISE EXCEPTION 'not a valid WGS84 Point';
        END IF;
        RETURN ST_SetSRID(ST_GeomFromGeoJSON(p_val::text), 4326)::geography;
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'observation field % rejected: %', 'location', left(p_val::text, 80);
        RETURN NULL;
    END;
END;
$$;

-- -----------------------------------------------------------------------------
-- Trigger function: telemetry_events -> telemetry_measurements (replaces 066)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sync_telemetry_event_measurements()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    -- Must equal services/common/observation_context.py::MEDIA (static test).
    v_media       CONSTANT TEXT[] := ARRAY['ambientAir', 'ambientAirUnderStructure',
                                           'enclosedAir', 'soil', 'plant', 'water', 'equipment'];
    v_source_kind CONSTANT TEXT[] := ARRAY['device', 'manual', 'import'];
    v_time_source CONSTANT TEXT[] := ARRAY['device', 'server'];
    v_quality     CONSTANT TEXT[] := ARRAY['valid', 'out_of_range', 'suspect'];

    v_obs_all     JSONB;
    v_deployment  UUID;
    v_source_kind_val TEXT;
    v_time_source_val TEXT;
    v_location    GEOGRAPHY;
    kv            RECORD;
    o             JSONB;
BEGIN
    IF NEW.payload ? 'measurements' THEN
        v_obs_all := NEW.payload->'observations';
        IF v_obs_all IS NOT NULL AND jsonb_typeof(v_obs_all) <> 'object' THEN
            RAISE WARNING 'observation field % rejected: %', 'observations', left(v_obs_all::text, 80);
            v_obs_all := NULL;
        END IF;

        v_deployment      := nkz_obs_uuid(NEW.payload->'deploymentId', 'deploymentId');
        v_source_kind_val := nkz_obs_vocab(NEW.payload->'sourceKind', 'sourceKind', v_source_kind);
        v_time_source_val := nkz_obs_vocab(NEW.payload->'timeSource', 'timeSource', v_time_source);
        v_location        := nkz_obs_point(NEW.payload->'location');

        FOR kv IN
            SELECT key, trim(value) AS value_txt
            FROM jsonb_each_text(NEW.payload->'measurements')
        LOOP
            -- JSON null: a NULL comparison below would not skip (066's set-based
            -- WHERE did), and value is NOT NULL, so the event INSERT would abort.
            CONTINUE WHEN kv.value_txt IS NULL;
            -- 066 numeric filter: finite decimal/scientific values only.
            CONTINUE WHEN kv.value_txt !~ '^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$';
            CONTINUE WHEN lower(kv.value_txt) IN ('nan', '+nan', '-nan', 'inf', '+inf', '-inf',
                                                  'infinity', '+infinity', '-infinity');

            o := v_obs_all -> kv.key;
            IF o IS NOT NULL AND jsonb_typeof(o) <> 'object' THEN
                RAISE WARNING 'observation field % rejected: %', kv.key, left(o::text, 80);
                o := NULL;
            END IF;

            INSERT INTO telemetry_measurements (
                tenant_id, observed_at, source_event_id, entity_id, entity_type,
                device_id, sensor_id, task_id, attribute_name, value, source,
                observed_property, feature_of_interest, medium,
                depth_from_cm, depth_to_cm, height_m, organ, context_ref,
                deployment_id, source_kind, location, time_source, quality_flag
            ) VALUES (
                NEW.tenant_id, NEW.observed_at, NEW.id, NEW.entity_id, NEW.entity_type,
                NEW.device_id, NEW.sensor_id, NEW.task_id, kv.key,
                kv.value_txt::double precision, 'telemetry_events',
                CASE WHEN o IS NULL THEN NULL ELSE nkz_obs_text(o->'observedProperty', 'observedProperty') END,
                CASE WHEN o IS NULL THEN NULL ELSE nkz_obs_text(o->'featureOfInterest', 'featureOfInterest') END,
                CASE WHEN o IS NULL THEN NULL ELSE nkz_obs_vocab(o->'medium', 'medium', v_media) END,
                CASE WHEN o IS NULL THEN NULL ELSE nkz_obs_number(o->'depthFromCm', 'depthFromCm') END,
                CASE WHEN o IS NULL THEN NULL ELSE nkz_obs_number(o->'depthToCm', 'depthToCm') END,
                CASE WHEN o IS NULL THEN NULL ELSE nkz_obs_number(o->'heightM', 'heightM') END,
                CASE WHEN o IS NULL THEN NULL ELSE nkz_obs_text(o->'organ', 'organ') END,
                CASE WHEN o IS NULL THEN NULL ELSE nkz_obs_text(o->'contextRef', 'contextRef') END,
                CASE WHEN o IS NULL THEN NULL ELSE v_deployment END,
                CASE WHEN o IS NULL THEN NULL ELSE v_source_kind_val END,
                CASE WHEN o IS NULL THEN NULL ELSE v_location END,
                CASE WHEN o IS NULL THEN NULL ELSE v_time_source_val END,
                CASE WHEN o IS NULL THEN NULL ELSE nkz_obs_vocab(o->'qualityFlag', 'qualityFlag', v_quality) END
            )
            ON CONFLICT (tenant_id, observed_at, source_event_id, attribute_name) DO NOTHING;
        END LOOP;
    END IF;

    RETURN NEW;
END;
$$;

-- =============================================================================
-- End of migration 116
-- =============================================================================
