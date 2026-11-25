-- =============================================================================
-- Migration 122: suspended tenants, as the modules see them
-- =============================================================================
-- tenants.deleted_at is the one fact. Both module views derive from it, so no
-- other writer of tenants.status can make them disagree:
--   module_tenant_plan.status    'suspended' while deleted_at is set
--   module_installations         does not list suspended tenants
-- Same columns, order, types, owner and grants as migration 106. Idempotent.
--
-- Roll back: the two CREATE OR REPLACE VIEW of migration 106.
-- =============================================================================

CREATE OR REPLACE VIEW public.module_tenant_plan AS
SELECT tenant_id, tenant_name, plan_level, plan_type,
       CASE WHEN deleted_at IS NOT NULL THEN 'suspended' ELSE status END AS status,
       (deleted_at IS NULL AND status = 'active') AS is_active,
       expires_at, max_users, max_robots, max_sensors, max_parcels, max_area_hectares,
       max_entities_total, max_satellite_computations
  FROM public.tenants;
ALTER VIEW public.module_tenant_plan OWNER TO postgres;

CREATE OR REPLACE VIEW public.module_installations AS
SELECT tim.module_id, tim.tenant_id, tim.is_enabled, tim.installed_at
  FROM public.tenant_installed_modules tim
  JOIN public.tenants t ON t.tenant_id = tim.tenant_id AND t.deleted_at IS NULL;
ALTER VIEW public.module_installations OWNER TO postgres;
