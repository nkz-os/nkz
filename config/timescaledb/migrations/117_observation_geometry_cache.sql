-- =============================================================================
-- Migration 117: observation geometry cache (contract 1.4 / 8.5)
-- Idempotent, expand-only. Holds the current geometry of the entities a reading
-- can be located on (AgriParcel, zones, AgriPlant, greenhouses, solar parks).
-- Filled from Orion subscriptions and a TTL refresh; deleted entities are removed.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.observation_geometry_cache (
    tenant_id   TEXT NOT NULL,
    entity_urn  TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    geom        GEOGRAPHY NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, entity_urn)
);

ALTER TABLE public.observation_geometry_cache OWNER TO nkz_core;

CREATE INDEX IF NOT EXISTS idx_observation_geometry_cache_geom
    ON public.observation_geometry_cache USING GIST (geom);

ALTER TABLE public.observation_geometry_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.observation_geometry_cache FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public'
                 AND tablename = 'observation_geometry_cache' AND policyname = 'observation_geometry_cache_tenant_isolation') THEN
    CREATE POLICY observation_geometry_cache_tenant_isolation ON public.observation_geometry_cache
      USING (tenant_id = current_setting('app.current_tenant', true))
      WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
  END IF;
END $$;
