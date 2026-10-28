-- =============================================================================
-- 108 — Normalize marketplace_modules icon_url vs metadata.icon
-- Idempotent. Admin/metadata only (Expand and Contract).
--
-- Module publishes previously wrote manifest.icon (typically a Lucide icon
-- name like 'tractor' or 'sprout') into marketplace_modules.icon_url.
-- The host treated icon_url as an <img src="..."> URL, which failed with 404.
--
-- This migration:
-- 1. Copies any non-URL icon_url into metadata.icon (when metadata.icon is
--    missing or empty) and clears icon_url to NULL so real image URLs
--    (http://, https://, /, data:image/) are preserved untouched.
-- 2. Replaces the non-existent Lucide icon 'terrain' on the soil module with
--    'layers'.
-- =============================================================================

BEGIN;

-- 1. Move non-URL icon_url values into metadata.icon and clear icon_url
UPDATE marketplace_modules
SET
    metadata = CASE
        WHEN btrim(icon_url) <> ''
         AND COALESCE(btrim(metadata->>'icon'), '') = ''
        THEN COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('icon', btrim(icon_url))
        ELSE metadata
    END,
    icon_url = NULL,
    updated_at = NOW()
WHERE icon_url IS NOT NULL
  AND btrim(icon_url) !~ '^(https?://|/|data:image/)';

-- 2. Replace invalid Lucide icon 'terrain' with 'layers' on soil module
UPDATE marketplace_modules
SET
    metadata = COALESCE(metadata, '{}'::jsonb) || '{"icon": "layers"}'::jsonb,
    updated_at = NOW()
WHERE id = 'soil'
  AND COALESCE(metadata->>'icon', '') IN ('', 'terrain');

COMMIT;
