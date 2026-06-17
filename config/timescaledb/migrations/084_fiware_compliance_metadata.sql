-- Add fiware_compliance metadata field to marketplace_modules
-- Creates a contract: module must declare compliance before first publish

ALTER TABLE marketplace_modules
ADD COLUMN IF NOT EXISTS fiware_compliance JSONB DEFAULT jsonb_build_object(
    'status', 'pending',
    'orion_client', 'unknown',
    'direct_db_writes', NULL,
    'verification_date', NULL
);

-- Backfill for existing modules
UPDATE marketplace_modules
SET fiware_compliance = jsonb_build_object(
    'status', 'pending',
    'orion_client', 'unknown',
    'direct_db_writes', NULL,
    'verification_date', NULL
)
WHERE fiware_compliance IS NULL;
