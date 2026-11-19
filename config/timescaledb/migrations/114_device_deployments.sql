-- =============================================================================
-- Migration 114: versioned device deployments (where / what a device observes, over time)
-- Idempotent, expand-only. An open deployment has an unbounded upper bound.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE IF NOT EXISTS public.device_deployments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL CHECK (tenant_id <> ''),
    device_urn          TEXT NOT NULL,
    valid               TSTZRANGE NOT NULL,
    observes_feature    TEXT,
    observation_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    channel_overrides   JSONB NOT NULL DEFAULT '{}'::jsonb,
    mobility            TEXT NOT NULL DEFAULT 'fixed' CHECK (mobility IN ('fixed', 'mobile')),
    location            GEOGRAPHY(Point, 4326),
    created_by          TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT device_deployments_valid_lower_chk
        CHECK (lower(valid) IS NOT NULL AND NOT lower_inf(valid)),
    CONSTRAINT device_deployments_no_overlap
        EXCLUDE USING gist (tenant_id WITH =, device_urn WITH =, valid WITH &&)
);

CREATE TABLE IF NOT EXISTS public.device_deployment_changes (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT NOT NULL CHECK (tenant_id <> ''),
    deployment_id UUID NOT NULL,
    changed_by    TEXT NOT NULL,
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason        TEXT NOT NULL CHECK (reason <> ''),
    before        JSONB,
    after         JSONB NOT NULL
);

ALTER TABLE public.device_deployments OWNER TO nkz_core;
ALTER TABLE public.device_deployment_changes OWNER TO nkz_core;

CREATE INDEX IF NOT EXISTS idx_device_deployments_tenant_device
    ON public.device_deployments (tenant_id, device_urn);
-- The changes table has no device column; its lookup key is the deployment.
CREATE INDEX IF NOT EXISTS idx_device_deployment_changes_tenant_deployment
    ON public.device_deployment_changes (tenant_id, deployment_id);

ALTER TABLE public.device_deployments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.device_deployments FORCE ROW LEVEL SECURITY;
ALTER TABLE public.device_deployment_changes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.device_deployment_changes FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public'
                 AND tablename = 'device_deployments' AND policyname = 'device_deployments_tenant_isolation') THEN
    CREATE POLICY device_deployments_tenant_isolation ON public.device_deployments
      USING (tenant_id = current_setting('app.current_tenant', true))
      WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public'
                 AND tablename = 'device_deployment_changes' AND policyname = 'device_deployment_changes_tenant_isolation') THEN
    CREATE POLICY device_deployment_changes_tenant_isolation ON public.device_deployment_changes
      USING (tenant_id = current_setting('app.current_tenant', true))
      WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
  END IF;
END $$;
