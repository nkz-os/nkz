-- migrations/001_tenant_parcel_modules.sql
-- Tracks which modules are activated for which parcels.
-- entity-manager owns this state (PostgreSQL), module backends own the entities (Orion-LD).

CREATE TABLE IF NOT EXISTS tenant_parcel_modules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL,
    parcel_id VARCHAR(256) NOT NULL,
    module_id VARCHAR(128) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, parcel_id, module_id)
);

CREATE INDEX IF NOT EXISTS idx_tpm_tenant
    ON tenant_parcel_modules(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tpm_parcel
    ON tenant_parcel_modules(tenant_id, parcel_id);
