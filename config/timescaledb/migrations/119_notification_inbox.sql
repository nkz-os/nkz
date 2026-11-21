-- =============================================================================
-- Migration 119: durable notification inbox for telemetry-worker
-- Idempotent, expand-only. A delivery buffer, not a time-series store: rows are
-- inserted before the receiver answers, processed by a single consumer per queue
-- and deleted. After repeated infrastructure failures a row moves to
-- notification_inbox_dead, from where it can be sent back.
-- RLS is enabled with a tenant policy but not FORCEd: the consumer reads every
-- tenant as the owner.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.notification_inbox (
    id           BIGSERIAL PRIMARY KEY,
    queue        TEXT NOT NULL DEFAULT 'telemetry',
    tenant_id    TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    notified_at  TIMESTAMPTZ,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    observed_at  TIMESTAMPTZ NOT NULL,
    payload      JSONB NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error   TEXT
);

CREATE TABLE IF NOT EXISTS public.notification_inbox_dead (
    LIKE public.notification_inbox INCLUDING DEFAULTS,
    died_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.notification_inbox OWNER TO nkz_core;
ALTER SEQUENCE public.notification_inbox_id_seq OWNER TO nkz_core;
ALTER TABLE public.notification_inbox_dead OWNER TO nkz_core;

CREATE INDEX IF NOT EXISTS idx_notification_inbox_ready
    ON public.notification_inbox (queue, available_at, id);
CREATE INDEX IF NOT EXISTS idx_notification_inbox_entity_order
    ON public.notification_inbox (queue, tenant_id, entity_id, observed_at, id);
CREATE INDEX IF NOT EXISTS idx_notification_inbox_dead_died
    ON public.notification_inbox_dead (queue, died_at);

ALTER TABLE public.notification_inbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notification_inbox_dead ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public'
                 AND tablename = 'notification_inbox' AND policyname = 'notification_inbox_tenant_isolation') THEN
    CREATE POLICY notification_inbox_tenant_isolation ON public.notification_inbox
      USING (tenant_id = current_setting('app.current_tenant', true))
      WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public'
                 AND tablename = 'notification_inbox_dead' AND policyname = 'notification_inbox_dead_tenant_isolation') THEN
    CREATE POLICY notification_inbox_dead_tenant_isolation ON public.notification_inbox_dead
      USING (tenant_id = current_setting('app.current_tenant', true))
      WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
  END IF;
END $$;
