-- =============================================================================
-- Migration 111: users already sent the support welcome email
-- Idempotent, expand-only. Keyed by Keycloak user id; no email or tenant stored.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.support_welcome_emails (
    user_id  TEXT PRIMARY KEY CHECK (user_id <> ''),
    sent_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.support_welcome_emails OWNER TO nkz_core;
