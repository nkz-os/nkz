-- 093_setup_parcel_urls.sql
-- Register metadata.setup_parcel_url for the 8 modules implementing
-- POST /internal/setup-parcel, so per-parcel module activation (entity-manager
-- parcel_activation.dispatch_to_module) can reach them. No convention-based
-- fallback exists in code (parcel_activation.py) — modules without this key
-- fail activation with a 502 "no setup_parcel_url" error.
--
-- Idempotent (Expand-only): jsonb_set over COALESCE(metadata, '{}'), only
-- writes when the value is missing or different. Safe to re-run.
--
-- URLs verified against:
--   - Service name+port: gitops-config/overlays/modules/<module>/ (ClusterIP Service)
--     (biorefinery has no gitops overlay/ArgoCD Application yet — sourced from
--     the module repo's own k8s/backend-deployment.yaml, same naming pattern
--     as its sibling modules; see ASSUMPTION below)
--   - Route prefix: each module's FastAPI main.py include_router() prefix
--     composed with the internal/setup router's own APIRouter(prefix=...)
--
-- soil and field-operations already carry this key from migrations 086 and
-- 081 respectively; re-applying here with the same value is a no-op and keeps
-- one canonical, auditable place documenting all 8 URLs together.
--
-- Does NOT set auto_provision for any module (owner decision, out of scope).

-- soil (086_soil_setup_parcel_url.sql origin — value unchanged)
UPDATE marketplace_modules
SET metadata = jsonb_set(
    COALESCE(metadata, '{}'::jsonb),
    '{setup_parcel_url}',
    '"http://soil-module-service:8000/v1/soil/internal/setup-parcel"'::jsonb,
    true
)
WHERE id = 'soil'
  AND COALESCE(metadata->>'setup_parcel_url', '') != 'http://soil-module-service:8000/v1/soil/internal/setup-parcel';

-- crop-health
UPDATE marketplace_modules
SET metadata = jsonb_set(
    COALESCE(metadata, '{}'::jsonb),
    '{setup_parcel_url}',
    '"http://crop-health-api-service:8000/api/crop-health/internal/setup-parcel"'::jsonb,
    true
)
WHERE id = 'crop-health'
  AND COALESCE(metadata->>'setup_parcel_url', '') != 'http://crop-health-api-service:8000/api/crop-health/internal/setup-parcel';

-- vegetation-health (marketplace id: vegetation-prime)
-- Route resolved to app/api/internal.py's `/api/internal/setup-parcel` (uses
-- SDK SubscriptionRegistrar, accepts {parcel_id, tenant_id, parcel_name,
-- action} matching entity-manager's dispatch_to_module payload exactly).
-- NOT app/api/internal_setup.py's `/api/vegetation/internal/setup-parcel`,
-- which is an older/parallel Celery-LST-only handler that ignores `action`
-- and does not distinguish activate/deactivate.
UPDATE marketplace_modules
SET metadata = jsonb_set(
    COALESCE(metadata, '{}'::jsonb),
    '{setup_parcel_url}',
    '"http://vegetation-prime-api-service:8000/api/internal/setup-parcel"'::jsonb,
    true
)
WHERE id = 'vegetation-prime'
  AND COALESCE(metadata->>'setup_parcel_url', '') != 'http://vegetation-prime-api-service:8000/api/internal/setup-parcel';

-- field-operations (081_field_operations_marketplace.sql origin — value unchanged)
UPDATE marketplace_modules
SET metadata = jsonb_set(
    COALESCE(metadata, '{}'::jsonb),
    '{setup_parcel_url}',
    '"http://field-operations-api-service:8420/internal/setup-parcel"'::jsonb,
    true
)
WHERE id = 'field-operations'
  AND COALESCE(metadata->>'setup_parcel_url', '') != 'http://field-operations-api-service:8420/internal/setup-parcel';

-- hydrology
UPDATE marketplace_modules
SET metadata = jsonb_set(
    COALESCE(metadata, '{}'::jsonb),
    '{setup_parcel_url}',
    '"http://hydrology-api-service:8000/api/v1/hydrology/internal/setup-parcel"'::jsonb,
    true
)
WHERE id = 'hydrology'
  AND COALESCE(metadata->>'setup_parcel_url', '') != 'http://hydrology-api-service:8000/api/v1/hydrology/internal/setup-parcel';

-- carbon
UPDATE marketplace_modules
SET metadata = jsonb_set(
    COALESCE(metadata, '{}'::jsonb),
    '{setup_parcel_url}',
    '"http://carbon-api-service:8000/api/carbon/internal/setup-parcel"'::jsonb,
    true
)
WHERE id = 'carbon'
  AND COALESCE(metadata->>'setup_parcel_url', '') != 'http://carbon-api-service:8000/api/carbon/internal/setup-parcel';

-- greenhouse-dt
-- Service "greenhouse-dt-backend" is not in the gitops-config overlay (which
-- only carries Deployment+ConfigMap+worker); it comes from the module repo's
-- own k8s/backend-service.yaml (applied once out-of-band, per CLAUDE.md §5
-- "overlay has Deployment+ConfigMap; not module k8s/" note for this module).
UPDATE marketplace_modules
SET metadata = jsonb_set(
    COALESCE(metadata, '{}'::jsonb),
    '{setup_parcel_url}',
    '"http://greenhouse-dt-backend:8420/api/internal/setup-parcel"'::jsonb,
    true
)
WHERE id = 'greenhouse-dt'
  AND COALESCE(metadata->>'setup_parcel_url', '') != 'http://greenhouse-dt-backend:8420/api/internal/setup-parcel';

-- biorefinery
-- ASSUMPTION: no gitops-config overlay/ArgoCD Application exists for
-- biorefinery yet (not found under overlays/modules/ or gitops/config/) —
-- service name+port sourced from nkz-module-biorefinery/k8s/backend-deployment.yaml
-- (biorefinery-api-service:8000), same pattern as carbon/crop-health. URL
-- formula is high-confidence; live deployment status is not — confirm before
-- activating this module for real parcels.
UPDATE marketplace_modules
SET metadata = jsonb_set(
    COALESCE(metadata, '{}'::jsonb),
    '{setup_parcel_url}',
    '"http://biorefinery-api-service:8000/api/biorefinery/internal/setup-parcel"'::jsonb,
    true
)
WHERE id = 'biorefinery'
  AND COALESCE(metadata->>'setup_parcel_url', '') != 'http://biorefinery-api-service:8000/api/biorefinery/internal/setup-parcel';
