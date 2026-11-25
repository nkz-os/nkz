-- =============================================================================
-- Migration 122: a suspended tenant is not an installation
-- =============================================================================
-- tenants.status accompanies deleted_at from now on (the admin endpoints write
-- both). module_installations stops listing suspended tenants, so the modules
-- that read it stop working on them without any change of their own.
-- Same columns, order, owner and grants as migration 106. Idempotent.
--
-- Roll back: CREATE OR REPLACE VIEW public.module_installations AS
--   SELECT module_id, tenant_id, is_enabled, installed_at
--   FROM public.tenant_installed_modules;
-- =============================================================================

UPDATE public.tenants SET status = 'suspended'
 WHERE deleted_at IS NOT NULL AND status = 'active';

CREATE OR REPLACE VIEW public.module_installations AS
SELECT tim.module_id, tim.tenant_id, tim.is_enabled, tim.installed_at
  FROM public.tenant_installed_modules tim
  JOIN public.tenants t ON t.tenant_id = tim.tenant_id AND t.deleted_at IS NULL;
ALTER VIEW public.module_installations OWNER TO postgres;
