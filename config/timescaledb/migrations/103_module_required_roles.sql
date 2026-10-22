-- =============================================================================
-- 103 — Canonical role set for tenant-facing modules
-- Idempotent. Admin/metadata only.
--
-- Module publishes used to reset required_roles to {Farmer} whenever the
-- manifest omitted them, and one module declared roles the platform does not
-- define, so it was visible only to admins. Tenant-facing modules are opened
-- by every platform role.
-- =============================================================================

UPDATE marketplace_modules
SET required_roles = ARRAY['Farmer', 'TechnicalConsultant', 'TenantAdmin', 'PlatformAdmin']::text[],
    updated_at = NOW()
WHERE id IN (
    'agrienergy', 'bioorchestrator', 'carbon', 'catastro-spain', 'connectivity',
    'crop-health', 'cue', 'datahub', 'field-operations', 'hydrology', 'lidar',
    'nkz-module-eu-elevation', 'nkz-module-gis-routing', 'risk', 'soil',
    'vegetation-prime', 'weather-map'
)
AND required_roles IS DISTINCT FROM
    ARRAY['Farmer', 'TechnicalConsultant', 'TenantAdmin', 'PlatformAdmin']::text[];
