-- 094_module_required_roles_rbac.sql
-- Fix marketplace_modules.required_roles so TenantAdmin / TechnicalConsultant
-- are never locked out of modules they can install.
--
-- Context (2026-07-19): ~18 active modules had required_roles = {Farmer} only.
-- /api/modules/me filters by role intersection, so TenantAdmin users (no Farmer
-- role) installed modules that then never appeared in the host. Farmer is the
-- least-privileged end user of field modules, not the sole consumer.
--
-- Role matrix (load / use via /api/modules/me):
--   FIELD  — Farmer + TenantAdmin + TechnicalConsultant + GestorCUE + PlatformAdmin
--   ADMIN  — TenantAdmin + TechnicalConsultant + GestorCUE + PlatformAdmin (no Farmer)
--
-- Install / parcel-activate remains gated separately (TenantAdmin / PlatformAdmin).
-- Idempotent: re-run safe (sets absolute target arrays).

-- ---------------------------------------------------------------------------
-- FIELD modules: operational / agronomic — Farmer may use; admins always may
-- ---------------------------------------------------------------------------
UPDATE marketplace_modules
SET required_roles = ARRAY[
    'Farmer',
    'TenantAdmin',
    'TechnicalConsultant',
    'GestorCUE',
    'PlatformAdmin'
]::text[],
    updated_at = NOW()
WHERE id IN (
    'agrienergy',
    'bioorchestrator',
    'carbon',
    'catastro-spain',
    'crop-health',
    'cue',
    'field-operations',
    'greenhouse-dt',
    'hydrology',
    'lidar',
    'nkz-module-eu-elevation',
    'nkz-module-gis-routing',
    'vegetation-prime',
    'weather-map',
    'soil',
    -- core/field surfaces that already mixed roles but omitted TechnicalConsultant
    'weather',
    'datahub',
    'intelligence',
    'parcels',
    'entities',
    'robotics',
    'predictions',
    'risks',
    'simulation',
    'zulip'
);

-- sensors: DeviceManager retained; add Farmer + TechnicalConsultant + GestorCUE
UPDATE marketplace_modules
SET required_roles = ARRAY[
    'Farmer',
    'DeviceManager',
    'TenantAdmin',
    'TechnicalConsultant',
    'GestorCUE',
    'PlatformAdmin'
]::text[],
    updated_at = NOW()
WHERE id = 'sensors';

-- ---------------------------------------------------------------------------
-- ADMIN / integration modules: no Farmer load access
-- ---------------------------------------------------------------------------
UPDATE marketplace_modules
SET required_roles = ARRAY[
    'TenantAdmin',
    'TechnicalConsultant',
    'GestorCUE',
    'PlatformAdmin'
]::text[],
    updated_at = NOW()
WHERE id IN (
    'odoo-erp',
    'n8n-nkz',
    'nkz-module-vpn',
    'connectivity'
);
