-- =============================================================================
-- Migration 087: Populate marketplace_modules metadata for auto-proxy routing
-- =============================================================================
-- Each active module declares api_prefix + backend_service in metadata JSONB
-- so the api-gateway auto-proxy can discover routes without a hardcoded list.
-- Run: idempotent (SET metadata = metadata || …)
-- =============================================================================

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/soil", "backend_service": "http://soil-module-service:8000", "requires_auth": true}'::jsonb
WHERE id = 'soil'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/soil');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/carbon", "backend_service": "http://carbon-api-service:8000", "requires_auth": true}'::jsonb
WHERE id = 'carbon'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/carbon');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/crop-health", "backend_service": "http://crop-health-api-service:8000", "requires_auth": true}'::jsonb
WHERE id = 'crop-health'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/crop-health');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/weather-map", "backend_service": "http://weather-api-service:8000", "requires_auth": true}'::jsonb
WHERE id = 'weather-map'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/weather-map');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/vegetation", "backend_service": "http://vegetation-prime-api-service:8420", "requires_auth": true}'::jsonb
WHERE id = 'vegetation-prime'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/vegetation');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/modules/cue", "backend_service": "http://cue-backend-service:5000", "requires_auth": true}'::jsonb
WHERE id = 'cue'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/modules/cue');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/graph/agriculture", "backend_service": "http://bioorchestrator-api-service:8420", "requires_auth": false}'::jsonb
WHERE id = 'bioorchestrator'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/graph/agriculture');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/elevation", "backend_service": "http://elevation-api-service:80", "requires_auth": true}'::jsonb
WHERE id = 'nkz-module-eu-elevation'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/elevation');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/field-operations", "backend_service": "http://field-operations-api-service:8420", "requires_auth": true}'::jsonb
WHERE id = 'field-operations'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/field-operations');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/modules/datahub", "backend_service": "http://datahub-backend-service:8000", "requires_auth": true}'::jsonb
WHERE id = 'datahub'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/modules/datahub');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/greenhouse", "backend_service": "http://greenhouse-dt-api-service:8420", "requires_auth": true}'::jsonb
WHERE id = 'greenhouse-dt'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/greenhouse');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/robotics", "backend_service": "http://robotics-api-service:8000", "requires_auth": true}'::jsonb
WHERE id = 'robotics'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/robotics');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/connectivity", "backend_service": "http://connectivity-api-service:8000", "requires_auth": true}'::jsonb
WHERE id = 'connectivity'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/connectivity');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/agrienergy", "backend_service": "http://agrienergy-api-service:8000", "requires_auth": true}'::jsonb
WHERE id = 'agrienergy'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/agrienergy');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/n8n-nkz", "backend_service": "http://n8n-nkz-frontend-service:80", "requires_auth": false}'::jsonb
WHERE id = 'n8n-nkz'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/n8n-nkz');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/zulip", "backend_service": "http://zulip-service:80", "requires_auth": false}'::jsonb
WHERE id = 'zulip'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/zulip');

UPDATE marketplace_modules
SET metadata = metadata || '{"api_prefix": "/api/lidar", "backend_service": "http://lidar-api-service:8000", "requires_auth": true}'::jsonb
WHERE id = 'lidar'
  AND (metadata->>'api_prefix' IS NULL OR metadata->>'api_prefix' != '/api/lidar');
