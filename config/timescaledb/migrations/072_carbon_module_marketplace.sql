-- Migration 072: Register Carbon module in marketplace
-- Phase 10 production deploy

INSERT INTO marketplace_modules (id, name, display_name, description, remote_entry_url, scope, exposed_module, version, author, category, is_active, metadata)
VALUES (
    'carbon',
    'carbon',
    'Carbon Intelligence',
    'Carbon sequestration and biomass analytics with LUE-based GPP/NPP, RothC soil carbon model, and Verra VM0042 MRV reporting',
    '/modules/carbon/nkz-module.js',
    'carbon',
    './App',
    '0.1.0',
    'Nekazari',
    'analytics',
    true,
    '{"slots": ["context-panel", "dashboard-widget", "bottom-panel"]}'::jsonb
)
ON CONFLICT (id) DO NOTHING;
