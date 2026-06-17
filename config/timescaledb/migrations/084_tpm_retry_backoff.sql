-- 084_tpm_retry_backoff.sql
-- Adds retry/backoff cursor columns to tenant_parcel_modules for the
-- parcel reconcile engine. Idempotent (Expand step; no drops).

ALTER TABLE tenant_parcel_modules
    ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE tenant_parcel_modules
    ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ;

-- Existing rows: eligible for immediate retry.
UPDATE tenant_parcel_modules SET next_retry_at = NOW() WHERE next_retry_at IS NULL;
