-- =============================================================================
-- Migration 113: observable-property catalogue (global + per-tenant) and seed v1
-- Idempotent, expand-only. tenant_id NULL = global property.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.observation_properties (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             TEXT CHECK (tenant_id IS NULL OR tenant_id <> ''),
    name                  TEXT NOT NULL,
    domain                TEXT NOT NULL CHECK (domain IN
                              ('environment', 'soil', 'plant', 'equipment', 'energy', 'water', 'other')),
    unit                  TEXT NOT NULL,
    default_medium        TEXT NOT NULL,
    allowed_media         TEXT[] NOT NULL,
    temporal_semantics    TEXT NOT NULL CHECK (temporal_semantics IN ('instant', 'cumulative', 'rate')),
    valid_min             DOUBLE PRECISION,
    valid_max             DOUBLE PRECISION,
    max_rate_per_hour     DOUBLE PRECISION,
    fusion                TEXT NOT NULL DEFAULT 'median',
    nearby_radius_m       DOUBLE PRECISION,
    max_age_s             INTEGER,
    min_coverage          DOUBLE PRECISION CHECK (min_coverage IS NULL OR (min_coverage >= 0 AND min_coverage <= 1)),
    sub_context_tolerance JSONB NOT NULL DEFAULT '{}'::jsonb,
    has_model             BOOLEAN NOT NULL DEFAULT false,
    deprecated_at         TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- An expression cannot go in a table UNIQUE constraint, hence a unique index.
CREATE UNIQUE INDEX IF NOT EXISTS observation_properties_scope_name_uq
    ON public.observation_properties ((COALESCE(tenant_id, '__global__')), name);

COMMENT ON COLUMN public.observation_properties.valid_min IS
    'Plausible minimum. For cumulative properties it applies to the increment per period, not to the raw counter (the raw value is checked against the profile counterMax).';
COMMENT ON COLUMN public.observation_properties.valid_max IS
    'Plausible maximum. For cumulative properties it applies to the increment per period, not to the raw counter (the raw value is checked against the profile counterMax).';

ALTER TABLE public.observation_properties OWNER TO nkz_core;
ALTER TABLE public.observation_properties ENABLE ROW LEVEL SECURITY;

-- Global seed v1. Runs before FORCE so a role subject to RLS can still insert the
-- tenant_id NULL rows; the NOT EXISTS guard makes a re-run insert nothing (an insert
-- attempt by such a role would fail the write policy even with ON CONFLICT DO NOTHING).
INSERT INTO public.observation_properties
    (tenant_id, name, domain, unit, default_medium, allowed_media, temporal_semantics,
     valid_min, valid_max, max_rate_per_hour, fusion, nearby_radius_m, max_age_s,
     min_coverage, sub_context_tolerance, has_model)
SELECT NULL, v.name, v.domain, v.unit, v.default_medium, v.allowed_media::text[], v.temporal_semantics,
       v.valid_min, v.valid_max, v.max_rate_per_hour, 'median', v.nearby_radius_m, v.max_age_s,
       v.min_coverage, v.sub_context_tolerance::jsonb, v.has_model
FROM (VALUES
    ('airTemperature', 'environment', '°C', 'ambientAir', '{ambientAir,ambientAirUnderStructure,enclosedAir}', 'instant', -40::float8, 60::float8, NULL::float8, 2000::float8, 10800, 0.83::float8, '{"heightAboveGround": 0.5}', true),
    ('relativeHumidity', 'environment', '%', 'ambientAir', '{ambientAir,ambientAirUnderStructure,enclosedAir}', 'instant', 0, 100, NULL, 2000, 10800, 0.83, '{"heightAboveGround": 0.5}', true),
    ('dewPointTemperature', 'environment', '°C', 'ambientAir', '{ambientAir,ambientAirUnderStructure,enclosedAir}', 'instant', -60, 50, NULL, 2000, 10800, 0.83, '{}', false),
    ('precipitation', 'environment', 'mm', 'ambientAir', '{ambientAir}', 'cumulative', 0, 500, 200, 1000, 10800, 1.0, '{}', true),
    ('solarRadiation', 'environment', 'W/m2', 'ambientAir', '{ambientAir,ambientAirUnderStructure,enclosedAir}', 'instant', 0, 1500, NULL, 2000, 10800, 1.0, '{}', true),
    ('reflectedSolarRadiation', 'environment', 'W/m2', 'ambientAir', '{ambientAir,ambientAirUnderStructure}', 'instant', 0, 1500, NULL, 0, 10800, 1.0, '{}', false),
    ('photosyntheticallyActiveRadiation', 'environment', 'umol/m2/s', 'ambientAir', '{ambientAir,ambientAirUnderStructure,enclosedAir}', 'instant', 0, 3000, NULL, 0, 10800, 1.0, '{}', false),
    ('soilMoisture', 'soil', '%', 'soil', '{soil}', 'instant', 0, 100, NULL, 0, 21600, 0.83, '{}', false),
    ('soilTemperature', 'soil', '°C', 'soil', '{soil}', 'instant', -30, 60, NULL, 0, 21600, 0.83, '{}', false),
    ('stemDiameterVariation', 'plant', 'µm', 'plant', '{plant}', 'instant', 0, 100000, NULL, 0, 21600, 0.83, '{}', false),
    ('equipmentTemperature', 'equipment', '°C', 'equipment', '{equipment}', 'instant', -40, 100, NULL, 0, 10800, 0.5, '{}', false)
) AS v (name, domain, unit, default_medium, allowed_media, temporal_semantics,
        valid_min, valid_max, max_rate_per_hour, nearby_radius_m, max_age_s,
        min_coverage, sub_context_tolerance, has_model)
WHERE NOT EXISTS (
    SELECT 1 FROM public.observation_properties p
    WHERE p.tenant_id IS NULL AND p.name = v.name
)
ON CONFLICT ((COALESCE(tenant_id, '__global__')), name) DO NOTHING;

ALTER TABLE public.observation_properties FORCE ROW LEVEL SECURITY;

-- Same policy pair as sensor_profiles: read global + own, write own only.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public'
                 AND tablename = 'observation_properties' AND policyname = 'observation_properties_select') THEN
    CREATE POLICY observation_properties_select ON public.observation_properties
      FOR SELECT
      USING (tenant_id IS NULL OR tenant_id = current_setting('app.current_tenant', true));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public'
                 AND tablename = 'observation_properties' AND policyname = 'observation_properties_write') THEN
    CREATE POLICY observation_properties_write ON public.observation_properties
      USING (tenant_id = current_setting('app.current_tenant', true))
      WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
  END IF;
END $$;
