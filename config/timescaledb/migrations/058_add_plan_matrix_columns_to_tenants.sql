-- =============================================================================
-- Migration 058: Add Plan Matrix columns to tenants table
-- =============================================================================

-- Add columns for quota management
ALTER TABLE tenants 
ADD COLUMN IF NOT EXISTS plan_level INTEGER NOT NULL DEFAULT 0, -- 0: Basic, 1: Pro, 2: Enterprise
ADD COLUMN IF NOT EXISTS max_area_hectares NUMERIC(10,2) DEFAULT 20.0,
ADD COLUMN IF NOT EXISTS max_users INTEGER DEFAULT 2,
ADD COLUMN IF NOT EXISTS max_sensors INTEGER DEFAULT 10,
ADD COLUMN IF NOT EXISTS max_robots INTEGER DEFAULT 0;

-- Update existing trial/basic tenants to the new SOTA basic defaults
UPDATE tenants SET 
    max_area_hectares = 20.0,
    max_users = 2,
    max_sensors = 10
WHERE plan_level = 0;

-- Add plan_level to marketplace_modules to control visibility
ALTER TABLE marketplace_modules
ADD COLUMN IF NOT EXISTS required_plan_level INTEGER NOT NULL DEFAULT 0;

-- Set requirements based on the new matrix
-- Basic (0): weather, sensors
-- Pro (1): risks, catastro-spain, vegetation-prime, datahub
-- Enterprise (2): lidar, robotics, n8n-nkz, vpn

UPDATE marketplace_modules SET required_plan_level = 1 
WHERE id IN ('risks', 'catastro-spain', 'vegetation-prime', 'datahub');

UPDATE marketplace_modules SET required_plan_level = 2 
WHERE id IN ('lidar', 'robotics', 'n8n-nkz', 'vpn');
