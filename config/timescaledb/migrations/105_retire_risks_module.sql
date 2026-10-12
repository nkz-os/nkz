-- =============================================================================
-- 105 — Retire the legacy 'risks' module
-- Idempotent. Admin/metadata only.
--
-- 'risks' predates the 'risk' module, which replaced it. It has no published
-- frontend bundle, so installing it gives tenants a module that cannot load,
-- and the 'risk' backend does not serve tenants that only have 'risks'.
-- Same pattern as 042: installations first, then the catalog row (the
-- foreign key cascades anyway; the explicit delete keeps intent readable).
-- =============================================================================

DELETE FROM tenant_installed_modules
WHERE module_id = 'risks';

DELETE FROM marketplace_modules
WHERE id = 'risks';
