-- Migration 123: the datalogger as an official Device (admin metadata, not telemetry)
-- Idempotent, expand-only. gateway_external_id is the `gw-<12 hex>` id of the box (its IoT Agent
-- device id and the last part of its Device URN); NULL until the entity exists.
-- gateway_provisioned_at is when the projection last completed. Table owner and row level
-- security are unchanged.
--
-- Roll back: drop the index, the constraint and the two columns.

ALTER TABLE public.iot_dataloggers
    ADD COLUMN IF NOT EXISTS gateway_external_id text;

ALTER TABLE public.iot_dataloggers
    ADD COLUMN IF NOT EXISTS gateway_provisioned_at timestamptz;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'iot_dataloggers_gateway_external_id_check'
                    AND conrelid = 'public.iot_dataloggers'::regclass) THEN
    ALTER TABLE public.iot_dataloggers
      ADD CONSTRAINT iot_dataloggers_gateway_external_id_check
      CHECK (gateway_external_id IS NULL OR gateway_external_id ~ '^gw-[0-9a-f]{12}$');
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS iot_dataloggers_tenant_gateway_external_id_key
    ON public.iot_dataloggers (tenant_id, gateway_external_id);
