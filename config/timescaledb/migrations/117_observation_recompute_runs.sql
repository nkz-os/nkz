-- =============================================================================
-- Migration 117: audit of observation recompute runs (contract 8.6)
-- Idempotent, expand-only. One row per recompute of a deployment's time range:
-- who asked, why, the range (range_to NULL = open), rows changed and the status.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.observation_recompute_runs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    TEXT NOT NULL CHECK (tenant_id <> ''),
    device_urn   TEXT NOT NULL,
    deployment_id UUID NOT NULL,
    range_from   TIMESTAMPTZ NOT NULL,
    range_to     TIMESTAMPTZ,
    requested_by TEXT NOT NULL,
    reason       TEXT NOT NULL CHECK (reason <> ''),
    status       TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'done', 'failed')),
    rows_changed BIGINT,
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ
);

ALTER TABLE public.observation_recompute_runs OWNER TO nkz_core;

CREATE INDEX IF NOT EXISTS idx_observation_recompute_runs_status_created
    ON public.observation_recompute_runs (status, created_at);

ALTER TABLE public.observation_recompute_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.observation_recompute_runs FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public'
                 AND tablename = 'observation_recompute_runs' AND policyname = 'observation_recompute_runs_tenant_isolation') THEN
    CREATE POLICY observation_recompute_runs_tenant_isolation ON public.observation_recompute_runs
      USING (tenant_id = current_setting('app.current_tenant', true))
      WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
  END IF;
END $$;
