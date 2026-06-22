-- =============================================================================
-- Create notification_config table for per-tenant notification channel config
-- =============================================================================
CREATE TABLE IF NOT EXISTS admin_platform.notification_config (
    tenant_id       TEXT PRIMARY KEY,
    email_config    JSONB,   -- { "enabled": true, "to": "admin@..." }
    zulip_config    JSONB,   -- { "enabled": true, "stream": "tenant-{id}-alerts" }
    webhook_config  JSONB,   -- { "enabled": true, "url": "https://...", "secret": "..." }
    enabled         BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE admin_platform.notification_config
    IS 'Per-tenant notification channel configuration for Alert entities';
