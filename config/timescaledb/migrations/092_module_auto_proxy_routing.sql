-- =============================================================================
-- Migration 092: marketplace_modules auto-proxy routing metadata (canonical)
-- =============================================================================
-- Self-contained routing contract for api-gateway auto_proxy_module().
-- Replaces never-applied 087; idempotent JSONB merge per module id.
-- =============================================================================

-- weather-map (fixes wrong weather-api-service target from 087)
UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/weather-map",
  "backend_service": "http://weather-map-backend:8080",
  "backend_mount": "/api/weather-map",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'weather-map';

-- soil (backend mounts at /v1/soil)
UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/soil",
  "backend_service": "http://soil-module-service:8000",
  "backend_mount": "/v1/soil",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'soil';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/carbon",
  "backend_service": "http://carbon-api-service:8000",
  "backend_mount": "/api/carbon",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'carbon';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/crop-health",
  "backend_service": "http://crop-health-api-service:8000",
  "backend_mount": "/api/crop-health",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'crop-health';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/vegetation",
  "backend_service": "http://vegetation-prime-api-service:8000",
  "backend_mount": "/api/vegetation",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'vegetation-prime';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/field-operations",
  "backend_service": "http://field-operations-api-service:8420",
  "backend_mount": "/api/field-operations",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'field-operations';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/greenhouse",
  "backend_service": "http://greenhouse-dt-backend:8420",
  "backend_mount": "/api/greenhouse",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'greenhouse-dt';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/robotics",
  "backend_service": "http://robotics-api-service:80",
  "backend_mount": "/api/robotics",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'robotics';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/connectivity",
  "backend_service": "http://connectivity-api-service:8000",
  "backend_mount": "/api/connectivity",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'connectivity';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/agrienergy",
  "backend_service": "http://agrienergy-api-service:8000",
  "backend_mount": "/api/agrienergy",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'agrienergy';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/lidar",
  "backend_service": "http://lidar-api-service:80",
  "backend_mount": "/api/lidar",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'lidar';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/datahub",
  "backend_service": "http://datahub-api-service:8000",
  "backend_mount": "/api/datahub",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'datahub';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/modules/cue",
  "backend_service": "http://cue-backend-service:5000",
  "backend_mount": "/api/modules/cue",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'cue';

-- public agriculture graph subset (direct ingress exception documented in spec)
UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/graph/agriculture",
  "backend_service": "http://bioorchestrator-api-service:8420",
  "backend_mount": "/api/graph/agriculture",
  "requires_auth": false
}$json$::jsonb
WHERE id = 'bioorchestrator';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/elevation",
  "backend_service": "http://elevation-api-service:80",
  "backend_mount": "/api/elevation",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'nkz-module-eu-elevation';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/n8n-nkz",
  "backend_service": "http://n8n-nkz-api-service:8000",
  "backend_mount": "/api/n8n-nkz",
  "requires_auth": false
}$json$::jsonb
WHERE id = 'n8n-nkz';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/zulip",
  "backend_service": "http://zulip-service:80",
  "backend_mount": "/api/zulip",
  "requires_auth": false
}$json$::jsonb
WHERE id = 'zulip';

-- modules missing from 087
UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/v1/hydrology",
  "backend_service": "http://hydrology-api-service:8000",
  "backend_mount": "/api/v1/hydrology",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'hydrology';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/routing",
  "backend_service": "http://nkz-module-gis-routing-service:8000",
  "backend_mount": "/api/routing",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'nkz-module-gis-routing';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/cadastral-api",
  "backend_service": "http://catastro-spain-api-service:5000",
  "backend_mount": "/api/cadastral-api",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'catastro-spain';

UPDATE marketplace_modules
SET metadata = metadata || $json${
  "api_prefix": "/api/odoo",
  "backend_service": "http://odoo-backend-service:80",
  "backend_mount": "/api/odoo",
  "requires_auth": true
}$json$::jsonb
WHERE id = 'odoo-erp';
