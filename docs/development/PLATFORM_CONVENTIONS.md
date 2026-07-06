# NKZ — Platform Conventions

Quick reference for developers and AI agents. Everything here reflects how the platform **actually works** in production. If your code contradicts this document, your code has a bug.

Last verified: 2026-06-04 (V1.3 closure — SyncOrionClient, POSTGRES_URL mandatory, IOTA_DEFAULT_KEY from Secret, kubectl patch eliminated, :latest pinned, RLS logging, tenant-webhook NGSI-LD purge)

---

## 1. Authentication

There is ONE auth mechanism: **httpOnly cookie `nkz_token`**.

```
Browser → POST /api/auth/session (body: {token}) → Set-Cookie: nkz_token (httpOnly, Secure, SameSite=Strict, domain=.robotika.cloud)
Browser → DELETE /api/auth/session → Clear cookie
```

### How each layer reads the token

| Layer | How it gets the JWT |
|-------|---------------------|
| **Frontend (host)** | Never reads the token directly. `credentials: 'include'` on every fetch. |
| **Frontend (IIFE modules)** | Same — `credentials: 'include'`. The SDK handles it. |
| **api-gateway** | `get_request_token()`: reads `Authorization: Bearer` header first, falls back to `nkz_token` cookie. |
| **Module backends (direct ingress)** | Must implement cookie fallback themselves. Pattern: check Bearer header → fall back to `request.cookies.get("nkz_token")`. See `agrienergy/middleware/__init__.py`. |

### Rules

- **Never** store tokens in localStorage or sessionStorage.
- **Never** pass tokens in query strings.
- **Never** expose tokens via `window.__nekazariAuthContext` (it only has `isAuthenticated`, `user`, `tenantId`, `roles` — no token).
- All fetch calls must use `credentials: 'include'`.

---

## 1b. IoT Device Provisioning (FIWARE Standard)

The platform follows the **FIWARE IoT Agent JSON** standard for connecting physical devices to the digital twin layer.

### Architecture

```text
Device/Gateway → MQTT (Mosquitto) → IoT Agent JSON 3.13.0 → Orion-LD (NGSI-LD)
                  ↑                    ↑                          ↓
           /<tenant_apikey>/<device_id>/attrs    NGSI-LD native   Subscription (throttle 30s)
           {"attr": value}                       + appendMode      ↓
                                                                  telemetry-worker → TimescaleDB
```

### Rules (MANDATORY)

| Rule | Detail |
|------|--------|
| **One apikey per tenant** | `get_or_create_service_group(tenant_id)` in `sdm_api.py` retrieves/creates a tenant-level apikey. All devices in a tenant share it. |
| **Topic format** | `/<tenant_apikey>/<device_id>/attrs` |
| **Payload format** | `{"attributeName": value}` (FIWARE IoT Agent JSON standard) |
| **IoT Agent mode** | NGSI-LD native (`IOTA_CB_NGSI_VERSION=ld`) + `IOTA_APPEND_MODE=true` + `IOTA_EXPLICIT_ATTRS=false` |
| **IoT Agent version** | 3.13.0 — uses `IOTA_MONGO_URI` (not USER/PASSWORD, driver 6.x bug) |
| **Mosquitto ACL** | `iot-agent` user MUST have `topic readwrite #` or receives ZERO messages |
| **Entity types with IoT** | `AgriSensor`, `Sensor`, `Actuator`, `WeatherStation`, `AgriculturalTractor`, `LivestockAnimal`, `AgriculturalMachine` |
| **MQTT external endpoint** | `MQTT_EXTERNAL_HOST` / `MQTT_EXTERNAL_PORT` in `nekazari-config` ConfigMap |
| **Credentials** | Shown ONCE at creation. Cannot be recovered. |

### NEVER

- Generate per-device apikeys (causes `MEASURES-004: Device not found`)
- Set `IOTA_APPEND_MODE=false` (causes `entity does not have such attribute`)
- Use `IOTA_MONGO_USER`/`IOTA_MONGO_PASSWORD` (driver 6.x auth bug — use `IOTA_MONGO_URI`)
- Mix v2 and LD entities in same tenant (type expansion conflict)
- Hardcode `MQTT_EXTERNAL_HOST` — always use ConfigMap

### Architecture decisions (2026-03-26)

- **Phase 1 (current)**: `explicitAttrs=false` — pragmatic for controlled DaTaK devices
- **Phase 2 (multi-tenant/third-party)**: migrate to `explicitAttrs=true` (Schema-First) for Relationship support, unitCode metadata, data contracts
- **Kafka**: not needed until >2,000 devices. asyncpg pool + NGSI-LD throttle handles up to ~5,000 sensors
- **TROE+Mintaka**: evaluate for Phase 2 datahub (standard ETSI temporal API, replaces custom worker)

---

## 1c. External API Access (PAT — Personal Access Tokens)

External applications (PowerBI, Tableau, Python, custom apps) authenticate via **Personal Access Tokens** instead of browser cookies.

### Token format

```
nkz_pat_<43 random chars>
```

Generated via `secrets.token_urlsafe(32)`. SHA-256 hash stored in `api_keys` table; raw token shown only once at creation.

### Scopes

Each PAT has one or more scopes. The api-gateway enforces `(HTTP method, path prefix)` pairs:

| Scope | Allowed routes |
|-------|---------------|
| `timeseries` | `GET/POST /api/timeseries/*` |
| `entities` | `GET /ngsi-ld/v1/entities*`, `POST /ngsi-ld/v1/entityOperations/query` |
| `export` | `POST /api/datahub/export`, `POST /api/datahub/timeseries/align` |
| `telemetry` | `GET /api/devices/*`, `GET /api/sensors*` |

All scopes are **read-only**. PATs cannot create, update, or delete entities.

### Pagination caps

- Entity queries via PAT: max **500** per page (default **100** if absent)
- Export rows via PAT: max **10,000**
- Orion-LD `Link` header is forwarded transparently for pagination

### PAT lifecycle

- **Creation:** `POST /api/tenant/api-keys` (auth: user JWT)
- **Listing:** `GET /api/tenant/api-keys` (returns metadata including `scopes`, never the raw token)
- **Validation:** `POST /internal/validate-pat` (auth: `X-Internal-Secret`, called by api-gateway)
- **Revocation:** `DELETE /api/tenant/api-keys/<id>` (soft-delete: sets `is_active=false`)
- **Expiry:** Optional `expires_at` field; rejected at creation if in the past
- **Cache:** Redis TTL 300s; revoked tokens may remain active up to 5 min

### Architecture flow

```
External App → HTTPS → api-gateway
                          ├── enforce_pat_scopes(): validates (method, path) against PAT scopes
                          ├── enforce_pat_pagination(): caps limit/max_rows
                          └── proxy to backend (Orion-LD, timeseries-reader, DataHub BFF)
                              ↑ auth: gateway service JWT + X-Delegated-Tenant-ID
```

### Rules

- PAT management UI is in **DataHub → Integrations**
- PAT scope mapping lives in `PAT_SCOPE_ROUTES` constant in `fiware_api_gateway.py`
- The `DATAHUB_BFF_URL` env var must point to `http://datahub-api-service:8000`
- Never expose raw PAT in logs — `PatSanitizingFilter` redacts `nkz_pat_*` patterns

---

## 2. Keycloak Configuration

### Admin token (for backend services)

Both `tenant-webhook` and `tenant-user-api` use the **password** grant with `admin-cli`:

```python
data = {
    'grant_type': 'password',
    'client_id': 'admin-cli',
    'username': os.getenv('KEYCLOAK_ADMIN_USER', 'admin'),
    'password': os.getenv('KEYCLOAK_ADMIN_PASSWORD', ''),
}
# POST to: http://keycloak-service:8080/auth/realms/master/protocol/openid-connect/token
```

**Never** use `client_credentials` grant. Env vars come from `keycloak-secret` K8s Secret.

### Internal Keycloak URL

All services connecting to Keycloak inside the cluster **must** use:

```
http://keycloak-service:8080/auth
```

The `/auth` suffix is required. Without it → 403/404 on admin API calls.

### Keycloak 26 User Profile (critical)

Custom user attributes **must be registered** in the realm User Profile **before** they can be set on users:

```
PUT /auth/admin/realms/{realm}/users/profile
```

Without registration, Keycloak 26 silently discards attributes on PUT (returns 204 but ignores them). This applies to all custom attributes: `tenant_id`, `tenant`, `plan`, `max_users`, `max_robots`, `max_sensors`, `activation_code`, `created_by`, `is_owner`.

Setup script: `scripts/keycloak-setup-mappers.sh` (handles both mapper and User Profile registration).

### User attribute mapper

The `nekazari-frontend` client has a `tenant_id` User Attribute mapper:
- User Attribute: `tenant_id` → Claim: `tenant_id`
- Added to: ID token + access token + userinfo
- Type: `String` (not multivalued)

### Keycloak roles

| Role | Assigned to | Meaning |
|------|-------------|---------|
| `PlatformAdmin` | Platform operators | Full admin access, all tenants |
| `TenantAdmin` | First user of a tenant (owner) | Manage own tenant users/settings |
| `Farmer` | Additional tenant users | Standard access within tenant |

---

## 3. Tenant Resolution

Every request is scoped to a tenant. The flow:

```
JWT token contains: { tenant_id: "asociacinallotarra", ... }   ← canonical claim: tenant_id (snake_case)
           ↓
  api-gateway extracts tenant_id from JWT claims
           ↓
  normalize_tenant_id("asociacinallotarra") → "asociacinallotarra"
           ↓
  Injects headers for internal services:
    NGSILD-Tenant: asociacinallotarra   ← canonical (ETSI NGSI-LD spec)
    Fiware-Service: asociacinallotarra  ← legacy FIWARE v2 compat (KEEP — Orion-LD resolves both to same namespace)
    Fiware-ServicePath: /               ← required by FIWARE v2 convention
    X-Tenant-ID: asociacinallotarra
```

### Tenant ID normalization rules

```
Input           → Output
"My-Farm"       → "my_farm"
"Test Tenant"   → "test_tenant"
"UPPERCASE"     → "uppercase"
"a-b-c"         → "a_b_c"
```

Function: `normalize_tenant_id()` in `common/tenant_utils.py` (lowercase, hyphens→underscores, strip special chars, 3-63 chars).

### Tenant ID creation (activation flow)

When a user activates with a NEK code, the tenant ID is generated as:

```python
normalized = _normalize_tenant_slug(tenant_name)  # slugify: lowercase, remove accents/special chars
tenant_id = f"tenant-{normalized}"                 # prefix with "tenant-"
```

When a user self-registers (free trial), the tenant ID is the normalized organization name without prefix.

### Which header to use when

| Calling... | Header | Who sets it |
|------------|--------|-------------|
| Orion-LD (NGSI-LD broker) | `NGSILD-Tenant` (canonical, ETSI spec) | api-gateway (automatic) |
| Internal backend services | `X-Tenant-ID` | api-gateway (automatic) |
| Module backends (direct ingress) | Extract from `X-Tenant-ID` header (if routed via gateway) or JWT `tenant_id` claim | Module's own middleware |

> **Both headers are permanent**: All platform services and modules MUST send **both** `NGSILD-Tenant` AND `Fiware-Service` with the same normalized tenant ID. Orion-LD uses `Fiware-Service` as a fallback namespace; sending only one header causes tenant isolation failures. Always include `Fiware-ServicePath: /`. Use the canonical `inject_fiware_headers()` from `common/auth_middleware.py` or `ngsi_headers.py` for standalone modules.

### Rules

- **Always** normalize before using as DB schema name, MongoDB collection, MinIO path, or SQL identifier.
- **Never** trust tenant ID from request body or query params — always from JWT.
- Default tenant (no tenant in JWT): `"default"`.

---

## 4. Tenant Onboarding Flow

### NEK code activation

```
Admin panel (PlatformAdmin)
  → POST /api/admin/activations  (email, plan)
  → Generates NEK-XXXX-XXXX-XXXX code, sends email
           ↓
User opens /activate
  → Enters: code, email, tenant_name, password
  → POST /webhook/activate
           ↓
tenant-webhook:
  1. validate_activation_code(code, email) — checks public.activation_codes
  2. create_tenant_resources(tenant_id, plan_info) — K8s namespace (optional)
  3. ensure_tenant_record() — INSERT into public.tenants
  4. INSERT into public.farmers (owner)
  5. create_keycloak_user() — sets attributes: tenant_id, plan, max_*, is_owner=true
  6. Assigns TenantAdmin role + tenant group in KC
           ↓
User logs in → JWT contains tenant_id claim → dashboard loads
```

### Self-registration (free trial)

```
User opens /register
  → POST /webhook/register  (email, organization_name, password)
  → Same flow but no NEK code, plan=basic, 30-day trial
```

### Key tables

| Table | Schema | Purpose |
|-------|--------|---------|
| `activation_codes` | `public` | NEK codes (pending/used/revoked) |
| `tenants` | `public` | Tenant records (plan, limits, status) |
| `farmers` | `public` | User records (email, tenant_id) |

---

## 5. NGSI-LD Requests to Orion-LD

Two valid patterns. Choose based on Content-Type:

### Pattern A: `application/json` + Link header

```http
POST /ngsi-ld/v1/entities HTTP/1.1
Content-Type: application/json
Link: <http://api-gateway-service:5000/ngsi-ld-context.json>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"
NGSILD-Tenant: my_farm
Fiware-Service: my_farm
Fiware-ServicePath: /

{"id": "urn:ngsi-ld:AgriParcel:my_farm:001", "type": "AgriParcel", "name": {"type": "Property", "value": "North field"}}
```

### Pattern B: `application/ld+json` + @context in body

```http
POST /ngsi-ld/v1/entities HTTP/1.1
Content-Type: application/ld+json
NGSILD-Tenant: my_farm
Fiware-Service: my_farm
Fiware-ServicePath: /

{"@context": "http://api-gateway-service:5000/ngsi-ld-context.json", "id": "urn:ngsi-ld:AgriParcel:my_farm:001", "type": "AgriParcel", "name": {"type": "Property", "value": "North field"}}
```

### Rules

- **Never** mix: if `@context` is in the body, do NOT send the Link header (Orion rejects it).
- **Never** use external context URLs in production (`https://raw.githubusercontent.com/smart-data-models/...`). Always use the local gateway context: `http://api-gateway-service:5000/ngsi-ld-context.json`.
- **Services inside `nkz/services/`**: Prefer `SyncOrionClient(tenant_id)` from `nkz-platform-sdk` (sync) or `OrionClient(tenant_id)` (async). Both enforce NGSILD-Tenant + Fiware-Service + @context/Link automatically. For services that cannot import the SDK, use `inject_fiware_headers()` from `common/auth_middleware.py`. It handles tenant normalization, both headers, Content-Type, Link, and @context mutual exclusivity.
- **Standalone modules (separate repos)**: Copy `ngsi_headers.py` into `backend/app/common/` and use `inject_fiware_headers()` from there. See `nkz-module-carbon/backend/app/common/` or the module template for reference.
- The api-gateway handles headers automatically for proxied requests (through `/ngsi-ld/*` and `/api/*` routes).
- For direct Orion calls from backend services (bypassing the gateway), you MUST use `inject_fiware_headers()`.
- Frontend consumption: use `options=keyValues` for simple JSON responses.

---

## 6. Entity Types (Smart Data Models)

Use SDM vocabulary. This table is the canonical list of types used in NKZ:

### Entities you create via wizard / SDM Integration

| Category | Type | Notes |
|----------|------|-------|
| **Parcelas** | `AgriParcel` | Primary land unit. Has `location` (GeoJSON polygon), `area` (hectares) |
| **Sensores** | `AgriSensor` | IoT sensor. Replaces legacy `Device` type. Gets MQTT credentials on creation |
| **Tractores** | `AgriculturalTractor` | Farm machinery with J1939/ISOBUS |
| **Implementos** | `AgriculturalImplement` | Attachments (plough, sprayer, etc.) |
| **Edificios** | `Building` | Farm buildings |
| **Agua** | `WaterSource`, `Well`, `Spring`, `Pond`, `IrrigationOutlet`, `IrrigationSystem` | Water infrastructure |
| **Energía** | `PhotovoltaicInstallation`, `EnergyStorageSystem` | Solar + batteries |
| **Ganadería** | `LivestockAnimal`, `LivestockGroup`, `LivestockFarm` | Animals + farms |
| **Robots** | `AgriculturalRobot` | Autonomous machines |
| **Legacy** | `Device` | Generic IoT device. **Prefer `AgriSensor`** for new entities. Kept for backwards compatibility |

### Entities created by backend services (not via wizard)

| Type | Created by | Notes |
|------|------------|-------|
| `WeatherObserved` | weather-worker | Hourly weather data per parcel |
| `AgriParcelRecord` | telemetry-worker | Sensor measurements linked to parcels |

### Entity ID format

```
urn:ngsi-ld:{Type}:{tenant_id}:{uuid|custom_id}
```

Generated by `_build_ngsild_urn()` in `entity-manager/blueprints/entities.py`.
The tenant segment is mandatory for multi-tenant isolation. UUIDs are v4 by default; custom IDs are sanitized (colons→hyphens, spaces→underscores).

Examples: `urn:ngsi-ld:AgriParcel:my_farm:a1b2c3d4e5f67890`, `urn:ngsi-ld:AgriSensor:allotarra:montiko-sensor-01`

> Existing entity IDs are immutable — never rename entities already in Orion.

### Entity Display Name

Use the canonical function to extract a display name from an NGSI-LD entity:

- **Python**: `get_entity_display_name(entity)` from `common/entity_utils.py`
- **TypeScript**: `getEntityDisplayName(entity)` from `@nekazari/sdk` (`ngsi/helpers.ts`)

Logic: `entity.name` (string) > `entity.name.value` (Property format) > `entity.id` (fallback).

### Context URL

One canonical env var: **`CONTEXT_URL`**. Default: `http://api-gateway-service:5000/ngsi-ld-context.json`.

Every service must use: `CONTEXT_URL = os.getenv("CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json")`

### Rules

- **Never** invent new types if an SDM type exists (e.g., don't use `Parcel`, `Sensor`, `Robot`).
- The SDM catalog in `sdm-integration/sdm_api.py` defines all available types. To add a new type, add it there.
- IoT types (`AgriSensor`, `Sensor`, `Actuator`, `WeatherStation`, `AgriculturalTractor`, `LivestockAnimal`, `AgriculturalMachine`) automatically get MQTT credentials provisioned on creation.
- **Never** hardcode context URLs — always use the `CONTEXT_URL` env var.

### Relationship Naming (NGSI-LD `type: "Relationship"`)

All Relationship-type attributes in NGSI-LD entities MUST follow these rules:

1. **Use FIWARE Smart Data Model standard names** when the relationship exists in an SDM:
   - `hasAgriParcel` — AgriParcelOperation → AgriParcel
   - `hasAgriCrop` — AgriParcel → AgriCrop
   - `hasDevice` — DeviceModel → Device (SAREF)

2. **Use descriptive verb+noun** for custom relationships without an SDM equivalent:
   - `locatedAt` — e.g., WeatherObserved → AgriParcel
   - `belongsTo` — e.g., DeviceProfile → Tenant
   - `observedBy` — e.g., Measurement → WeatherStation
   - `hasDeviceProfile` — e.g., AgriSensor → DeviceProfile
   - `hasTrialSite` — e.g., VarietyTrial → TrialSite
   - `hasArticleSource` — e.g., VarietyTrial → ArticleSource

3. **NEVER use `ref<Type>` for new entities.**
   - This was a legacy (pre-audit) convention that deviates from FIWARE standards.
   - Existing `ref*` attributes in Orion-LD are backward-compatible via `@context` aliases in `nkz/config/ngsi-ld-context.json`.
   - Both old (`refAgriParcel`) and new (`hasAgriParcel`) names resolve to the same URI.

4. **Backward compatibility in code:** When reading entities, use fallback patterns:
   ```python
   ref = entity.get("locatedAt") or entity.get("refParcel")  # new first, legacy fallback
   ```
   This ensures code works with both new entities and old ones still in Orion-LD.

---

## 7. Units of Measurement (unitCode)

Numeric properties must include `unitCode` using **UN/CEFACT Common Codes**:

| Measurement | unitCode | Wrong |
|-------------|----------|-------|
| Temperature (°C) | `CEL` | `"ºC"`, `"celsius"` |
| Pressure (hPa) | `HPA` | `A97`, `"hPa"` |
| Area (hectares) | `HAR` | `"ha"`, `"hectareas"` |
| Percentage | `P1` | `"%"`, `"percent"` |
| Wind speed (m/s) | `MTS` | `"m/s"`, `"Km/h"` |
| Precipitation (mm) | `MMT` | `"mm"` |
| Irradiance (W/m²) | `D54` | `"W/m2"` |

Example:
```json
{
  "atmosphericPressure": {
    "type": "Property",
    "value": 1013.25,
    "unitCode": "HPA"
  }
}
```

---

## 8. API Routing

### SOTA routing model (2026-07-06)

The platform routing contract is:

```
Browser -> Traefik
        -> /api catch-all -> api-gateway
        -> gateway auto_proxy_module() (registry from marketplace_modules.metadata)
        -> module backend service
```

This is the default for module APIs. New modules must be routable without adding new gateway `@app.route()` handlers or new per-module `/api/<module>` Ingress paths.

### Module metadata contract (`marketplace_modules.metadata`)

Each routable module must expose these keys in metadata:

| Key | Required | Description |
|-----|----------|-------------|
| `api_prefix` | yes | Public API prefix, for example `/api/weather-map`. |
| `backend_service` | yes | Internal service base URL, for example `http://weather-map-backend:8080`. |
| `backend_mount` | yes | Backend mount prefix after strip, for example `/api/weather-map` or `/v1/soil`. |
| `requires_auth` | yes | `true` for protected APIs, `false` only for documented public endpoints. |

`api-gateway` keeps a route registry cache (TTL 300s). After publish/metadata updates, invalidate `routes` cache to avoid waiting for TTL.

### Permanent direct-routing exceptions (do not auto-proxy)

| Path | Reason |
|------|--------|
| `/api/elevation/ws` | WebSocket upgrade stays direct. |
| `/api/graph/*`, `/api/capability/*` | BioOrchestrator direct ingress + own auth middleware. |
| `/api/modules/cue/*` | CUE direct ingress contract. |
| `odoo.robotika.cloud` | Odoo UI host only. |

Some core APIs also remain explicit in the gateway (for example intelligence, datahub export/align, vegetation tiles, routing/tiles, n8n tenant proxy, zulip-specific handlers).

### Operational rules

- Keep `/api` catch-all in ingress and route module APIs through `api-gateway`.
- Do not add per-module `/api/<module>` Ingress rules unless the path is an approved permanent exception.
- Module publishes must preserve metadata keys above; verify `metadata->>'api_prefix'` is not `NULL`.
- Frontend API base remains `https://nkz.robotika.cloud` (no `/api` suffix).
- `/health` endpoints **must** have `@limiter.exempt` to avoid probe-triggered rate-limit failures.

---

## 9. GeoJSON

- Coordinate order: **`[longitude, latitude]`** (NOT `[lat, lon]`).
- CRS: WGS84 (EPSG:4326). Always.
- Location property in NGSI-LD:

```json
{
  "location": {
    "type": "GeoProperty",
    "value": {
      "type": "Point",
      "coordinates": [-2.6189, 42.8467]
    }
  }
}
```

---

## 10. Frontend i18n (host)

There is **one** shared **i18next** instance for the host. Do not add a second loader or duplicate JSON trees.

### Stack (actual code)

| Piece | Role |
|--------|------|
| `NekazariI18nProvider` (`@nekazari/sdk`) | Async `initI18n` + `I18nextProvider`; must wrap the tree before any `useTranslation`. Config: `apps/host/src/config/hostI18nConfig.ts` (`loadPath: /locales/{{lng}}/{{ns}}.json`, `ns`: `common`, `navigation`, `layout`). |
| `I18nProvider` (`apps/host/src/context/I18nContext.tsx`) | **Compatibility only**: `useI18n().t(...)` delegates to the **same** `i18n` via `i18n.t(realKey, { ns })`. It does **not** load flat `/locales/{lang}.json`. |
| `useTranslation(ns?)` | From **`@nekazari/sdk`** in host code (same React context as the provider). Avoid importing `react-i18next` directly in the host to prevent split-brain context. |

### Where strings live

- **Source of truth**: `nkz/apps/host/public/locales/{lang}/{namespace}.json`
- **Languages**: `es`, `en`, `ca`, `eu`, `fr`, `pt`
- **Namespaces loaded at init**: `common`, `navigation`, `layout` (must stay in sync with `hostI18nConfig.namespaces` and with `knownNamespaces` in `I18nContext.tsx` if you extend them).

### `useI18n().t('…')` key rules

- If the first dot segment is `common`, `navigation`, or `layout`, that segment is treated as the **namespace** and the rest is the key inside that file (e.g. `navigation.dashboard` → namespace `navigation`, key `dashboard`).
- Any other first segment (e.g. `dashboard.title`, `wizard.sdm_guide.help_button`) is looked up in **`common`** with the **full** key string.

So: add nested keys under the correct `*.json` namespace file; do **not** rely on removed flat `public/locales/{lang}.json` files.

### `useTranslation` in host

- Prefer `useTranslation('common')` (or `navigation` / `layout`) and keys **without** a fake namespace prefix.
- Default namespace is `common` if you call `useTranslation()` with no argument.

### IIFE modules

Bundle translations in the module, then register against the shared instance, e.g.:

```ts
i18n.addResourceBundle(lang, 'common', translations, true, true);
```

### Rules

- Minimum languages: `es` + `en` for every new key.
- New namespaces require: JSON files per language, `hostI18nConfig.namespaces`, and (if used via `useI18n`) `knownNamespaces` in `I18nContext.tsx`.
- After changing i18n init or providers, rebuild the host Docker image (SDK is built inside the Dockerfile before Vite).

---

## 11. IIFE Module Build

- Output: single `nekazari-module.js` file (module-builder default is `nkz-module.js` but all production modules use `nekazari-module.js`).
- JSX: `"jsx": "react"` (classic transform). **Never** `"react-jsx"`.
- Externals: `react→React`, `react-dom→ReactDOM`, `react-router-dom→ReactRouterDOM`, `@nekazari/sdk→__NKZ_SDK__`, `@nekazari/ui-kit→__NKZ_UI__`.
- Entry: `src/moduleEntry.ts` → `window.__NKZ__.register({ id, viewerSlots, version })`.
- Deploy: upload to MinIO `nekazari-frontend/modules/{moduleId}/nekazari-module.js`.
- Module `id` must match `marketplace_modules.id` in the database exactly.

---

## 12. Billing & subscription roles (Keycloak)

Canonical **realm role names** for Stripe ↔ Keycloak orchestration (billing module + api-gateway):

| Role | Meaning |
|------|---------|
| `role_pro_trial` | Subscription in trial |
| `role_pro_active` | Paid / active access |
| `role_pro_expired` | Terminal non-payment or cancellation; **read-only** platform lock (gateway mutating API → 403) |

### Rules

- **Never** use legacy names such as `role_locked` in new code, docs, or Keycloak configuration — use **`role_pro_expired`**.
- Checkout trial length is configured with **`STRIPE_TRIAL_PERIOD_DAYS`** (default `45` in billing module settings); keep product copy and Stripe dashboard aligned.
- Billing admin HTTP routes accept **`PlatformAdmin`** or **`TenantAdmin`** JWT roles; tenant/user context always comes from JWT claims, never from the request body.
