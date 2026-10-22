-- Migration 070: User Push Notification Tokens
-- Supports expo-notifications for nkz-mobile

CREATE TABLE IF NOT EXISTS user_push_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    expo_token TEXT NOT NULL UNIQUE,
    platform TEXT DEFAULT 'unknown',
    device_info JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_push_tokens_tenant ON user_push_tokens(tenant_id);
CREATE INDEX IF NOT EXISTS idx_user_push_tokens_user ON user_push_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_user_push_tokens_active ON user_push_tokens(tenant_id, is_active);
