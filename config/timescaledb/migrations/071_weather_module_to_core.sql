-- =============================================================================
-- Migration 071: Promote Weather from ADDON_FREE to CORE
-- =============================================================================
-- Weather is infrastructure, not an optional addon. Without it:
--   6 risk models stop working, agro-panel breaks, water balance fails,
--   vegetation-prime loses correlation context, crop-health loses thermal/
--   water stress inputs, agrienergy loses solar predictions.
--
-- Changing module_type to CORE makes it non-toggleable — the
-- can_install_module() function already treats CORE as always-available
-- with no plan checks (migration 025, line 132-135).
--
-- Dependencies: 025_tenant_governance.sql, 028_register_platform_addons.sql
-- =============================================================================

UPDATE marketplace_modules
SET module_type = 'CORE',
    pricing_tier = NULL,
    required_plan_type = NULL,
    updated_at = NOW()
WHERE id = 'weather';

-- Verify: module_type should be CORE, pricing_tier NULL, is_active TRUE
-- SELECT id, module_type, pricing_tier, required_plan_type, is_active
-- FROM marketplace_modules WHERE id = 'weather';

-- =============================================================================
-- End of migration 071
-- =============================================================================
