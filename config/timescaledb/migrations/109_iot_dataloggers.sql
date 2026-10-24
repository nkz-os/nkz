-- =============================================================================
-- Migration 109: datalogger MQTT accounts (admin metadata, not telemetry)
-- Idempotent, expand-only. No password column: passwords live only in the broker.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.iot_dataloggers (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               TEXT NOT NULL,
    name                    TEXT NOT NULL,
    kind                    TEXT NOT NULL CHECK (kind IN ('datak', 'generic_mqtt')),
    mqtt_username           TEXT NOT NULL UNIQUE,
    status                  TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    credentials_rotated_at  TIMESTAMPTZ,
    revoked_at              TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS public.iot_datalogger_devices (
    datalogger_id  UUID NOT NULL REFERENCES public.iot_dataloggers (id) ON DELETE RESTRICT,
    tenant_id      TEXT NOT NULL,
    device_urn     TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    PRIMARY KEY (datalogger_id, device_urn),
    UNIQUE (tenant_id, device_urn)
);

ALTER TABLE public.iot_dataloggers OWNER TO nkz_core;
ALTER TABLE public.iot_datalogger_devices OWNER TO nkz_core;

CREATE INDEX IF NOT EXISTS idx_iot_dataloggers_tenant
    ON public.iot_dataloggers (tenant_id);
CREATE INDEX IF NOT EXISTS idx_iot_datalogger_devices_tenant
    ON public.iot_datalogger_devices (tenant_id);

ALTER TABLE public.iot_dataloggers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.iot_dataloggers FORCE ROW LEVEL SECURITY;
ALTER TABLE public.iot_datalogger_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.iot_datalogger_devices FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public'
                 AND tablename = 'iot_dataloggers' AND policyname = 'iot_dataloggers_tenant_isolation') THEN
    CREATE POLICY iot_dataloggers_tenant_isolation ON public.iot_dataloggers
      USING (tenant_id = current_setting('app.current_tenant', true))
      WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public'
                 AND tablename = 'iot_datalogger_devices' AND policyname = 'iot_datalogger_devices_tenant_isolation') THEN
    CREATE POLICY iot_datalogger_devices_tenant_isolation ON public.iot_datalogger_devices
      USING (tenant_id = current_setting('app.current_tenant', true))
      WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
  END IF;
END $$;
