# Nekazari — Production State

> **Living document.** Update after every sprint/deploy.
> Last updated: **2026-05-14**

---

## Cluster & Infrastructure

| Item | State |
|------|-------|
| Server | `109.123.252.120` — Ubuntu 24.04.3 LTS, K3s v1.33.6 (single node) |
| Namespace | `nekazari` |
| Domains | `nekazari.robotika.cloud` (frontend), `nkz.robotika.cloud` (API), `auth.robotika.cloud` (Keycloak), `argo.robotika.cloud` (ArgoCD) |
| TLS | Let's Encrypt via cert-manager — auto-renewal active |
| Firewall | UFW active — only 22, 80, 443 open |
| Disk | ~83% (79G/96G) — liberados 6GB (docker prune + k3s ctr prune) el 2026-05-02. Aún con disk pressure en nodo K3s |
| Registry | `ghcr.io/nkz-os/nkz/` (migrated from `k8-benetis` 2026-03-22) |

---

## Frontend (Host)

| Item | State |
|------|-------|
| Deployed bundle | Built from `main` (PR #236 merge, 2026-05-11). Docker image `ghcr.io/nkz-os/nkz/host:latest` |
| Serving | `frontend-host` Docker pod (via main `nekazari-ingress`). `/modules` from `frontend-static` (MinIO). Module backends via api-gateway canary (MODULE_GATEWAY_ENABLED) |
| Runtime config | `VITE_API_URL=https://nkz.robotika.cloud` (no `/api` suffix) |
| Last deploy | 2026-05-14 — PR #263, #265, #267 (PAT scope expansion). api-gateway, tenant-webhook rolled out |
| Module loading | IIFE Runtime Injection — `window.__NKZ__.register()`. SDK types canonical in `@nekazari/sdk` v1.1.0. `@nekazari/module-kit` v0.1.0 provides `defineModule()`. Host exposes `window.__NKZ_MODULE_KIT__` for IIFE externals. |
| Module lint/validate | `nkz module validate` CLI — manifest schema, i18n parity, NGSI-LD entity check |
| Module dev | `nkz module dev` — lightweight host shell with HMR |
| Module migration | Phase 2 partial: 16/18 modules have @nekazari/module-kit dep. Backend migration (require_auth, OrionClient) pending nkz-platform-sdk PyPI publish |

### Published packages

| Package | Version | Registry |
|---------|---------|----------|
| @nekazari/sdk | 1.1.0 | npm |
| @nekazari/module-kit | 0.1.0 | npm |
| nkz-platform-sdk | 0.1.0 | repo (pending PyPI) |
| IoT provisioning | `api.createSDMIoTEntity()` → SDM Integration (`POST /sdm/entities/<type>/instances`). Returns MQTT credentials in response. Added 2026-03-18 |

### Pages & features live in production (2026-05-11)

| Feature | Commit | Notes |
|---------|--------|-------|
| EntityEditor (EM-EDIT-ENT) | `4804b39` (PR #233) | Editor genérico de atributos NGSI-LD. Single-page form, 6 secciones colapsables, ~30 schemas conocidos + data-driven, Relationships, kinematic attributes, Geometry con inputs de coordenadas. Acceso vía lista entidades (lápiz), popup mapa, y SDK. 62 claves i18n es+en. |
| Weather parcel dropdown (WTH-WIDGET) | `4804b39` (PR #233) | WeatherWidget refactorizado: dropdown de parcelas con filtro client-side, `loadWeatherByParcel()` vía `/api/weather/parcel/{id}`, fallback a municipio preservado. |
| Spatial resolution wo catalog (WTH-SPATIAL) | `4804b39` (PR #233) | Migración 074: columna `location GEOMETRY(Point, 4326)` en `weather_observations` + GIST + backfill (21,888 filas). KNN queries migradas en `urn_resolution.py`, `parcels.py`, `locations.py`. Ingesta weather-worker actualizada. Cobertura global sin INE/NUTS. |
| EntityWizard modular (E1) | `b8724f9` | 3 macro-flows: assets / sensors / fleet. WizardContext + discriminated union TS |
| Página Riesgos `/risks` (R1) | `de0f85c` | 3 tabs: Monitor (historial evaluaciones), Configurar (SmartRiskPanel), Webhooks |
| Risk severity overlay en mapa | `10e88fd` | `useRiskOverlay` hook, colores por severidad en parcelas/sensores/robots, leyenda, toggle en capas |
| Eliminación código NDVI muerto del core | `e5598db` | `parcelIndexData`, `useNdviLayer`, `ndviApi.ts` eliminados. 340 líneas menos |
| Dashboard widget de riesgos | `e439d8c` | `RiskSummaryCard` — muestra riesgos críticos/altos activos. Labels GDD pest añadidas 2026-02-26 |
| Webhooks de riesgos UI | `e439d8c` | `RiskWebhooksPanel` — alta/baja webhooks por severidad mínima |
| Trigger manual de evaluación | `e439d8c` | Botón en `/risks` → `POST /api/risks/trigger-evaluation` |
| Parcel drawing double-click (C3) | `215bf05` | `LEFT_DOUBLE_CLICK` finaliza polígono + rubber-band preview. Timestamp-based 400ms window |
| Dashboard limpio (P8) | 2026-02-25 | Eliminados: 3D Map placeholder, Activity Feed hardcoded, AlertsCard, GrafanaAccess. 4ª MetricCard "Entidades Registradas" real. RiskSummaryCard: labels legibles, severity chips, auto-refresh 5min |
| Bulk GPS import (E3) | 2026-02-26 | `BulkImportModal` (CSV+GeoJSON, preview SVG espacial, 3 pasos). `parsers.ts` (auto-detect columnas). `POST /sdm/entities/<type>/batch` → Orion-LD `entityOperations/create`. Botón "Importar" en `/entities`. Límite 500 entidades/batch |
| Traducciones I1 | 2026-02-26 | `eu/ca/fr/pt` `common.json` completados: 13→94 claves (Euskara, Català, Français, Português). Todos los `navigation.json` incluyen clave `modules`. |
| Admin panel O9 | 2026-02-26 | Grafana/Prometheus condicionales (solo si URL configurada). 14 botones migrados a claves i18n. Nuevas claves `es.json`+`en.json`: nek_codes, manage_modules, audit_logs, iot_configuration, global_assets, device_library. |
| Disk cleanup O3 | 2026-02-26 | `k8s/common/disk-cleanup-cronjob.yaml`: CronJob cada 4h, privileged nsenter, prune k3s images (>80%), journal+log rotation (>87%), webhook alert opcional. Pendiente apply en producción. |

---

## Backend Services

| Service | Replicas | Image | Notes |
|---------|----------|-------|-------|
| api-gateway | 1 | `:latest` | JWT validation, FIWARE headers, rate limiting |
| entity-manager | 1 | `:latest` | **Overhauled 2026-05-06.** 7 Blueprints (`blueprints/`), helpers (`helpers/`), main file ~272 lines. 79 routes, 164 smoke tests. See `nkz/CLAUDE.md` for structure. |
| keycloak | 1 | custom `26.4.7` | OIDC/OAuth2, RS256 JWKS |
| orion-ld | 1 | FIWARE | NGSI-LD context broker → MongoDB |
| postgresql (TimescaleDB) | 1 | `:latest` | Hypertables activas: `telemetry_data`, `risk_daily_states`, `weather_data` |
| mongodb | 1 | `:latest` | Orion-LD entity storage |
| redis | 1 | `:latest` | Cache + rate limiting + job queue |
| minio | 1 | `:latest` | `nekazari-frontend` bucket: host/, modules/ |
| weather-worker | 1 | `:latest` | `imagePullPolicy: IfNotPresent` — workaround needed on redeploy (O8) |
| telemetry-worker | 1 | `:latest` | NGSI-LD subscription → TimescaleDB. PR#86: NGSILD-Tenant header, throttle 30s. PR#87 pending: asyncpg pool + EventSink |
| iot-agent-json | 1 | `fiware/iotagent-json:3.13.0` | NGSI-LD native mode. IOTA_MONGO_URI workaround. Mosquitto ACL required |
| timeseries-reader | 1 | `:latest` | Historical data API. Telemetry v2 uses flat `payload.measurements` keys (`->>`); index **062** / `ix_telemetry_tenant_device_time` **applied prod 2026-03-28** (see § Timescale telemetry contract). |
| risk-api | 1 | `:latest` | `/api/risks/*` — catalog, states, subscriptions, webhooks, trigger |
| risk-orchestrator | 1 | `:latest` | Schedules risk evaluations, writes `riskStatus` to Orion-LD |
| risk-worker | 1 | `:latest` | Hourly batch — **6 models**: Agronomic, Robotic, Energy, SpraySuitability, WaterStress, FrostRisk, WindSpray, GDDPest×2. CronJob at :15 |
| mqtt-credentials-manager | 1 | `:latest` | Dynamic MQTT user provisioning |
| intelligence (module backend) | 1 | `:latest` | Scaled back up 2026-02-22. Monitor CPU |
| odoo (module backend) | 1 | `nekazari-module-odoo/odoo:latest` | Running. Ingress: odoo-direct only; dbfilter ^nkz_odoo_.*$; /web/database/* → 403 |
| odoo-backend (module API) | 1 | `nekazari-module-odoo/odoo-backend:latest` | Running. Uses ODOO_ADMIN_PASSWORD from secret when set |
| agrienergy (module backend) | 1 | `nekazari-module-agrienergy/agrienergy:latest` | JSON Logic algorithm engine, solar parks, MQTT notify, shadow simulation. Ingress: `agrienergy-api-frontend-host` on `nekazari.robotika.cloud` |
| cadastral (module backend) | 0 | — | CPU constraints |

---

## PAT Scope Expansion (2026-05-14) — DEPLOYED

| Topic | Detail |
|-------|--------|
| Description | PAT tokens extended to 4 scoped data categories for external API access |
| Scopes | `timeseries`, `entities`, `export`, `telemetry` — all read-only |
| Enforcement | api-gateway `enforce_pat_scopes()` validates `(HTTP method, path prefix)` per scope |
| Pagination cap | 500 entities/page (default 100), 10k rows export |
| PRs merged | #263 (jsonb fix), #265 (DATAHUB_BFF_URL), #267 (pagination cap fix) |
| Storage | `api_keys` table: `scopes JSONB`, `expires_at TIMESTAMPTZ` |
| Log sanitization | `PatSanitizingFilter` redacts `nkz_pat_*` from gateway logs |
| DataHub UI | Scope checkboxes + expiry selector in Integrations panel |
| Docs | `nkz-module-datahub/docs/API_EXTERNAL_ACCESS.md`, `PLATFORM_CONVENTIONS.md §1c` |
| Verification | 16/17 spec cases pass (1 NGSI-LD edge case: `limit` not a valid Query body field) |

---

## Timescale — `telemetry_events` payload contract (2026-03-27)

| Topic | Detail |
|-------|--------|
| Storage shape | IoT measurements under `payload.measurements` are stored as a **flat JSON object** (SDM-style camelCase keys, numeric values), enabling **O(1) key access** via the `->>` operator without scanning SDM-style measurement arrays in SQL. |
| timeseries-reader | `POST /api/timeseries/v2/query` (telemetry CTEs) and multi-device align use `locf(avg((NULLIF(trim(e.payload->'measurements'->>key), ''))::double precision))` with **whitelist-bound** keys; CTEs join with weather via **FULL OUTER JOIN … USING (bucket)** (unchanged). |
| GET columnar | `/v2/entities/.../data` still accepts **both** a flat object and a legacy **array** of `{type|name, value}` for JSON columnar assembly only. |
| Index | Migration **062** applied **production 2026-03-28**: `ix_telemetry_tenant_device_time` on `(tenant_id, device_id, observed_at DESC)`. ConfigMap `postgresql-migrations` synced via server-side apply (see `DEPLOYMENT.md`). |

---

## Module System

| Module | Bundle | State |
|--------|--------|-------|
| vegetation-health | `nekazari-frontend/modules/vegetation-health/nekazari-module.js` | Deployed. Bugs pendientes (ver PENDING P2) |
| ~~connectivity~~ | — | **Removed 2026-03-23**. Stub duplicating SDM device profiles. ArgoCD app + marketplace entry deactivated. Migration 060. |
| datahub | `nekazari-frontend/modules/datahub/nekazari-module.js` | Deployed. Weather entities visibles vía `/api/datahub/entities` |
| intelligence | `nekazari-frontend/modules/intelligence/nekazari-module.js` | Deployed |
| cadastral-sp | `nekazari-frontend/modules/catastro-sp/nekazari-module.js` | Deployed (backend a 0 réplicas) |
| agrienergy | `nekazari-frontend/modules/agrienergy/nekazari-module.js` | Deployed 2026-03-18. 61kB IIFE. TrackerDashboard, ManualControls, AlgorithmPanel, ConfigureSignals, ParkOverview, Sandbox. i18n es+en |
| robotics | bundle pendiente | Backend en desarrollo |
| carbon | sin bundle | Skeleton only — sin backend |
| lidar | sin bundle | En desarrollo |
| **vpn (Device Mgmt)** | `nekazari-frontend/modules/vpn/nekazari-module.js` | **Verificado 2026-05-02.** ZTP completo: factory register → claim → Headscale preauth key. Rate limiting Redis, RLS PostgreSQL, audit log, cuotas tenant_limits, UX PlatformAdmin cross-tenant + factory panel. Headscale v0.23 en vpn.robotika.cloud. |

---

## GitOps State

| Scope | Manager | Path |
|-------|---------|------|
| `gitops/modules/` | ArgoCD (auto-sync) | Watches GitHub `nkz-os/nkz` HEAD (migrated from `k8-benetis` 2026-03-22) |
| `gitops/core/` | ArgoCD (auto-sync) | Traefik + ArgoCD ingress config |
| `k8s/` (services) | Manual `kubectl apply` | Migration to ArgoCD in progress |

---

## Tenant & User Management Overhaul (2026-03-22) — DEPLOYED

Server deploy completed 2026-03-22. All changes live in production.

| Component | Change | Status |
|-----------|--------|--------|
| tenant-webhook | Redis Limiter fallback, role merge, `DELETE /api/admin/activations/<id>`, `@limiter.exempt` on `/health` | **Live** |
| api-gateway | Added `TENANT_USER_API_URL` env var | **Live** |
| entity-manager | Fixed `KEYCLOAK_URL` (added `/auth` suffix) | **Live** |
| tenant-user-api | `password` grant via `admin-cli`, KC admin creds + CORS, `tenant_id` attribute | **Live** |
| AdminManagement.tsx | Wired delete/revoke handlers, error handling | **Live** |
| KeycloakAuthContext.tsx | Warn when tenant_id empty | **Live** |
| i18n | 8 new admin keys in es.json + en.json | **Live** |
| K8s/gitops/scripts/docs | `k8-benetis` → `nkz-os` migration (48 files) | **Live** |
| GHCR packages | 15 images migrated to `ghcr.io/nkz-os/nkz/*` | **Live** |
| Keycloak | `tenant_id` + `tenant` registered in User Profile (KC26 requirement). Mapper on `nekazari-frontend` client. | **Live** |
| Stale namespace | `nekazari-webhook` namespace + `tenant-webhook.yaml` removed (was ArgoCD sync loop) | **Live** |

### Keycloak 26 — Critical Discovery
Custom user attributes (`tenant_id`, `tenant`) **must be registered** in the realm User Profile (`/auth/admin/realms/{realm}/users/profile`) before they can be set on users. Without registration, Keycloak 26 silently discards them on PUT. The `keycloak-setup-mappers.sh` script must be updated to also register attributes in User Profile.

---

## FIWARE Compliance & Cleanup (2026-03-23)

| Component | Change | Status |
|-----------|--------|--------|
| risk-orchestrator | Fixed uninitialized `self.context_url` — now reads `CONTEXT_URL` env var in `__init__` | Code done |
| 5 deployment YAMLs | `CONTEXT_URL` changed from external `nekazari.robotika.cloud` to internal `http://api-gateway-service:5000/ngsi-ld-context.json` | Code done |
| entity-manager | `TenantConfig` migrated from Orion-LD to PostgreSQL `admin_platform.tenant_limits` (config data, not digital twin) | Code done |
| entity-manager | Audit log instrumentation: `tenant_limits.update`, `terms.save`, `landing_mode.update` | Code done |
| tenant-webhook | Audit log instrumentation: 7 operations (activation codes, tenant CRUD, user delete) | Code done |
| SDM Integration | Removed dead endpoints: `/sdm/schemas/<type>` (mock), `/sdm/migrate` (no-op). ~100 lines removed | Code done |
| connectivity module | **Removed entirely**: ArgoCD app deleted, marketplace deactivated (migration 060), was a stub duplicating SDM device profiles | Code done |
| SDMManagement.tsx | **Deleted** — admin panel SDM tab removed (mock schema viewer, no real function) | Code done |
| Sensors.tsx | Enhanced: health summary bar (online/warning/offline), dynamic readings column, proper pagination via `NGSILD-Results-Count` header, i18n | Code done |
| api.ts | Fixed `getSDMEntityInstancesPaginated()` to use `count=true` + read `NGSILD-Results-Count` header. Removed `migrateToSDM()` | Code done |

**Deployed 2026-03-23** — merged via PR #69 (`feat/premium-modules`). Docker images built by CI. K8s manifests applied manually (`kubectl apply`). Frontend routing fixed: main `nekazari-ingress` now routes `/` to `frontend-host-service` (Docker) instead of `frontend-static-service` (MinIO). Stale ingresses removed (`frontend-host-ingress`, `agrienergy-api-frontend-host`, `connectivity-api-frontend-host`).

---

## IoT Telemetry Pipeline (2026-03-26) — OPERATIONAL

End-to-end NGSI-LD telemetry pipeline deployed and verified with live DaTaK data.

| Component | State | Details |
|-----------|-------|---------|
| IoT Agent JSON | 3.13.0 NGSI-LD native | `IOTA_CB_NGSI_VERSION=ld`, `IOTA_APPEND_MODE=true`, `IOTA_EXPLICIT_ATTRS=false`. IOTA_MONGO_URI workaround for driver 6.x bug. |
| Mosquitto | ACL configured | `iot-agent` user has `topic readwrite #` |
| NGSI-LD subscription | Active, throttle 30s | Tenant `asociacinallotarra`, entities AgriSensor/Device/AgriParcel |
| telemetry-worker | v1 live (psycopg2) | PR#87 pending: asyncpg pool + EventSink + batch inserts |
| TimescaleDB | Hypertable active | `telemetry_events` receives live data. Migration 061 pending (compression policy). |
| DaTaK integration | Live | Sensor `120786a0cf364796` → `solarRadiation`, `airTemperature`, `sensorsinsolation` |

### Architecture Decision Record (2026-03-26)
- **Phase 1 (current)**: `explicitAttrs=false` — pragmatic for DaTaK-controlled devices
- **Phase 2 (multi-tenant)**: migrate to `explicitAttrs=true` (Schema-First) for Relationship support, unitCode metadata, and data contract enforcement
- **Kafka trigger**: >2,000 devices or multiple consumers. Current: asyncpg pool + throttle handles up to ~5,000 sensors
- **TROE+Mintaka**: evaluate for Phase 2 datahub (replaces custom telemetry-worker with standard ETSI temporal API)

---

## Known Issues (active as of 2026-03-22)

| Ref | Issue | Severity |
|-----|-------|----------|
| INC-2 | lidar-frontend ImagePullBackOff — imagen no existe en GHCR | Medium |
| INC-3 | `/api/weather/municipalities/search` devuelve 502 | Low |
| INC-4 | sensor-ingestor endpoint 404 — legacy reference | Low |
| Q1 | ~690 console.log en producción | Low |
| Q6 | `CesiumPolygonDrawer` NDVI flat-color (legacy) | Low |
| — | C2 backup module: código overhauled, pendiente deploy + secrets | Medium |
| — | 2 cambios SOTA válidos sin commitear en nkz/ (unitCode HPA, AgriSensor sub). SDM @context ya incluido en cleanup FW-1. tenant normalize pendiente revisión. | Low |

---

## Risk Engine — Data Flow (2026-02-26)

```
weather-worker (hourly) → TimescaleDB weather_observations
  ├── temp_avg, humidity_avg, precip_mm
  ├── delta_t, gdd_accumulated, water_balance   ← TODOS conectados al risk-worker
  └── soil_moisture_0_10cm                       ← usado en WaterStressModel (blend 0.3)

risk-worker CronJob (:15 cada hora) — 6 modelos activos:
  ├── SpraySuitabilityModel  (parcelas — delta_t, wind, RH)
  ├── FrostRiskModel         (parcelas — temp_min, umbral configurable por tenant)
  ├── WindSprayRiskModel     (parcelas — wind_speed, AEPLA/EU 2009/128/CE)
  ├── WaterStressModel       (parcelas — water_balance + soil_moisture blend)
  ├── GDDPestRiskModel       (parcelas — GDD_PRAYS_OLEAE, olive moth, doy=1)
  └── GDDPestRiskModel       (parcelas — GDD_LOBESIA_1ST / 2ND, vine moth, doy=32)
        ↓
  risk_daily_states (TimescaleDB hypertable)
        ↓
  risk-orchestrator → Orion-LD riskStatus attribute + webhooks
        ↓
  Frontend: /risks page + CesiumMap risk overlay + RiskSummaryCard en Dashboard

risk_catalog (admin_platform): migrations 051–054
  - spray_suitability, frost_risk, wind_spray, water_stress
  - GDD_PRAYS_OLEAE (olive), GDD_LOBESIA_1ST, GDD_LOBESIA_2ND (vine)
```

---

## Bulk Entity Import (E3 — 2026-02-26)

```
BulkImportModal (frontend)
  ├── Step 1: Drag-and-drop CSV / GeoJSON
  ├── Step 2: Preview — entity type selector + SVG spatial dots + table (8 rows)
  └── Step 3: Results — created count + errors list

parsers.ts
  ├── parseCSV: auto-detect lat/lng/name cols (es/en synonyms), comma/semicolon delimiter
  └── parseGeoJSON: Point + Polygon centroid + MultiPoint centroid

api.ts: batchCreateEntities(entityType, entities[]) → POST /sdm/entities/<type>/batch

sdm-integration backend:
  POST /sdm/entities/<type>/batch
    → builds NGSI-LD array → Orion-LD entityOperations/create
    → handles 201 (all ok) / 207 (partial) / 500
    → max 500 entities/request
```
