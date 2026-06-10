-- 080_tenant_parcel_modules.sql
-- Per-parcel module activation state (admin/metadata — PostgreSQL is correct
-- here per platform rules; entity instantiation lives in Orion-LD).
CREATE TABLE IF NOT EXISTS tenant_parcel_modules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL,
    parcel_id VARCHAR(256) NOT NULL,
    module_id VARCHAR(128) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    -- Honest dispatch state: pending (not yet set up in module),
    -- ok (module confirmed), error (last dispatch failed; re-POST retries).
    setup_status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (setup_status IN ('pending', 'ok', 'error')),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, parcel_id, module_id)
);

CREATE INDEX IF NOT EXISTS idx_tpm_tenant
    ON tenant_parcel_modules(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tpm_parcel
    ON tenant_parcel_modules(tenant_id, parcel_id);
