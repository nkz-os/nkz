-- =============================================================================
-- Migration 077: Immutable Module Deployments
-- =============================================================================
-- Adds deployed_version and deployment_history columns to marketplace_modules
-- for tracking versioned (git SHA) deployments with atomic rollback capability.
-- =============================================================================

ALTER TABLE marketplace_modules
    ADD COLUMN IF NOT EXISTS deployed_version TEXT;

ALTER TABLE marketplace_modules
    ADD COLUMN IF NOT EXISTS deployment_history JSONB DEFAULT '[]'::jsonb;

COMMENT ON COLUMN marketplace_modules.deployed_version
    IS 'Active deployment version hash (git SHA). NULL for legacy flat-path deployments.';
COMMENT ON COLUMN marketplace_modules.deployment_history
    IS 'Array of {"version":"<sha>","deployedAt":"<iso>","deployedBy":"<user>"} objects. Last 10 entries retained.';

CREATE INDEX IF NOT EXISTS idx_marketplace_modules_deployed_version
    ON marketplace_modules(deployed_version)
    WHERE deployed_version IS NOT NULL;
