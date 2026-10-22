-- =============================================================================
-- Migration 060: Remove connectivity module from marketplace
-- =============================================================================
-- The connectivity module was a stub implementation that duplicated
-- device profile functionality already provided by SDM Integration.
-- It has been removed from the platform.

-- Deactivate and uninstall connectivity module
UPDATE marketplace_modules
SET is_active = false, updated_at = NOW()
WHERE id = 'connectivity';

-- Remove tenant installations
DELETE FROM tenant_installed_modules WHERE module_id = 'connectivity';

-- Verification
-- SELECT id, is_active FROM marketplace_modules WHERE id = 'connectivity';
