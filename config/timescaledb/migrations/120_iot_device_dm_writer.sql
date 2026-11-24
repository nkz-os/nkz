-- Migration 120: who writes the DeviceMeasurement entities of a device (admin metadata)
-- Idempotent, expand-only. Every existing device stays on the entity-manager ('em').
-- dm_shape is the DeviceMeasurement shape provisioned in the IoT Agent ('legacy' or 'sdm');
-- NULL while the entity-manager writes.

ALTER TABLE public.iot_datalogger_devices
    ADD COLUMN IF NOT EXISTS dm_writer text NOT NULL DEFAULT 'em' CHECK (dm_writer IN ('em', 'iota'));

ALTER TABLE public.iot_datalogger_devices
    ADD COLUMN IF NOT EXISTS dm_shape text CHECK (dm_shape IN ('legacy', 'sdm'));
