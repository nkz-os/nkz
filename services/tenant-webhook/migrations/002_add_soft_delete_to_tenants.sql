-- Add soft-delete support to tenants table
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS deleted_at      TIMESTAMPTZ;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS deleted_by      TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS purge_scheduled TIMESTAMPTZ;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS deletion_notes  TEXT;

-- Index for quick lookup of suspended tenants
CREATE INDEX IF NOT EXISTS idx_tenants_deleted_at ON tenants (deleted_at) WHERE deleted_at IS NOT NULL;
