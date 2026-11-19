-- =============================================================================
-- Migration 115: vegetation-prime declares which roles may activate it on a parcel
-- Idempotent, expand-only: merges one metadata key; no column or row is dropped.
-- =============================================================================

UPDATE public.marketplace_modules
SET metadata = COALESCE(metadata, '{}'::jsonb)
    || '{"activation_roles": ["Farmer", "TechnicalConsultant", "TenantAdmin", "PlatformAdmin"]}'::jsonb
WHERE id = 'vegetation-prime';
