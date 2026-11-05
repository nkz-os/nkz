-- =============================================================================
-- Migration 112: weekly report sources (active modules that publish a report URL)
-- Idempotent, expand-only. Read-only view; no rows or columns are altered.
-- =============================================================================

CREATE OR REPLACE VIEW public.module_report_sources AS
SELECT id, display_name, metadata->>'weekly_report_url' AS weekly_report_url
FROM public.marketplace_modules
WHERE is_active AND metadata ? 'weekly_report_url';
ALTER VIEW public.module_report_sources OWNER TO postgres;
GRANT SELECT ON public.module_report_sources TO nkz_module_notifications;
