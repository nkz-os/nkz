# Module System Redesign — Frictionless Module Development

**Status**: Design approved, ready for implementation planning
**Date**: 2026-05-12
**Author**: Design session with platform owner
**Supersedes**: Phase 1 of [2026-05-11 SOTA audit](../../../nekazari/2026-05-11-102928-analiza-nkz-y-el-sistema-de-mdulos-y-dime-si-el.txt) — refines the goals and adjusts execution

## 1. Context

### Problem

Phase 1 of the 2026-05-11 redesign declared `@nekazari/module-kit` complete and deployed, but the execution exposed several defects:

- 11 of 18 modules carry a dead `import { defineModule } from "@nekazari/module-kit"` that never calls the function (cargo-cult migration).
- `toNKZRegistration()` is unusable as designed: hardcodes `version: '0.1.0'`, no `main` field. No real module uses it.
- `@nekazari/module-kit@0.1.0` was published with no `dist/` directory in the tarball, breaking every consumer (CI red across 3+ modules on 2026-05-12; root cause analysed in [`design-system-publishing` memory](../../../memory/design-system-publishing.md) and [`module-system-audit-2026-05-12` memory](../../../memory/module-system-audit-2026-05-12.md)).
- The official template `nkz-module-template` does not use `module-kit` at all — disagreeing with `CLAUDE.md` which documents `module-kit` as canonical.
- Three patterns coexist for registering `viewerSlots`: object map (`{ 'context-panel': [...] }`), array-of-tuples (`[{ slot, component }]`), and inline at registration. No coherence.
- Per-module DevOps boilerplate: every module hand-writes `vite.config.ts`, `manifest.json`, `moduleEntry.ts`, `Dockerfile`, K8s YAMLs, ArgoCD apps, and CI workflows. Each diverges slightly. `connectivity` was using `npm ci` while others used `pnpm`.

The result: a developer cannot create a Nekazari module without studying the platform internals first. The promise of "frictionless module creation" is unmet.

### Target audience

NKZ-OS is open source and self-hostable. The module developer is one of:

- **Integrator / freelancer** deploying NKZ-OS for a cooperative, university, or research lab and writing a tailored module for that customer.
- **Student / researcher** with a final-year project or thesis who needs a 3D + digital twin environment to test their algorithms without rebuilding the platform.
- **Module author** writing a module for Nekazari to include in its marketplace (monetisation, vertical not yet covered, …).

Third parties do **not** push to the Nekazari production server directly. They either deploy NKZ-OS themselves, or submit a module that Nekazari reviews before inclusion. Sandboxing for untrusted third-party code is out of scope for this design — the compensating control is human review + sovereign control of the operator.

### Success criteria

- A new developer can run `nkz init my-module && cd my-module && nkz dev` and see a working widget in the host shell **in under 60 seconds**, with no external dependencies.
- The 80% of modules that are presentation + data consumption need **no backend code** — only TypeScript + React.
- For the 20% of modules with backend, the developer writes endpoints (Python/FastAPI) — never Dockerfiles, K8s manifests, or ArgoCD apps.
- `defineModule({...})` is the single source of truth for the module's identity, routing, slots, permissions, i18n, and data needs. Manifest, entry, vite config, and host registration are all derived.
- Existing modules keep working during migration (compatibility layer in `module-builder`).

## 2. Architectural overview

Three clean layers, each with one job:

```
┌─────────────────────────────────────────────────────────┐
│ Layer 3: Developer's module                             │
│   src/Module.tsx                                        │
│     → export default defineModule({...})                │
│   src/components/                                       │
│   api/main.py             (optional, 20% of modules)    │
│   locales/{lang}.json                                   │
└─────────────────────────────────────────────────────────┘
                         depends on
┌─────────────────────────────────────────────────────────┐
│ Layer 2: Platform SDK + tooling                         │
│   @nekazari/module-kit       (defineModule, hooks)      │
│   @nekazari/module-builder   (vite preset + codegen)    │
│   @nekazari/sdk              (types, i18n, runtime)     │
│   @nekazari/ui-kit           (design system components) │
│   nkz-platform-sdk           (Python: ModuleApp, ...)   │
│   nkz (CLI)                  (init/dev/build/publish)   │
└─────────────────────────────────────────────────────────┘
                         runs on
┌─────────────────────────────────────────────────────────┐
│ Layer 1: Platform runtime                               │
│   api-gateway       (auth + tenant headers + CSP data)  │
│   host (React app)  (loads IIFE + provides window.__NKZ__)│
│   Orion-LD, Timescale, Keycloak, MinIO, Mosquitto       │
└─────────────────────────────────────────────────────────┘
```

## 3. `defineModule()` — single source of truth

Replaces `moduleEntry.ts`, `manifest.json`, and most of `vite.config.ts`.

```ts
import { defineModule } from '@nekazari/module-kit';
import { lazy } from 'react';

export default defineModule({
  // Identity
  id: 'soil-health',                 // kebab-case, unique, matches marketplace_modules.id
  displayName: 'Soil Health',
  version: '1.0.0',                  // optional; defaults to package.json#version
  hostApiVersion: '^2.0.0',          // semver range the host must satisfy
  description: 'Soil analysis for agricultural parcels',

  // UI
  accent: { base: '#A16207', soft: '#FEF3C7', strong: '#713F12' },
  icon: 'sprout',                    // lucide-react icon name
  main: lazy(() => import('./pages/SoilDashboard')),

  // Host integration
  route: '/soil-health',
  navigation: {
    section: 'modules',              // 'modules' | 'admin' | 'tools'
    priority: 60,
    label: { es: 'Suelo', en: 'Soil' },
  },

  // Viewer slots
  slots: {
    'context-panel': [
      { id: 'soil-context', component: lazy(() => import('./slots/SoilContextPanel')), priority: 10 },
    ],
    'map-layer': [
      { id: 'soil-layer', component: lazy(() => import('./slots/SoilLayer')) },
    ],
  },

  // Backend (only if the module has one)
  api: { basePath: '/api/soil-health' },

  // Permissions
  requiredRoles: ['Farmer', 'TenantAdmin'],
  requiredPlan: 'basic',             // 'basic' | 'pro' | 'premium' | 'enterprise'

  // i18n
  i18n: {
    es: () => import('./locales/es.json'),
    en: () => import('./locales/en.json'),
  },

  // Declarative data dependencies (used for CSP-of-data validation)
  data: {
    entities: ['AgriParcel', 'AgriSoil'],        // NGSI-LD types this module reads/writes
    timeseries: ['soil_observations'],           // Timescale hypertables this module reads
  },
});
```

### What is generated from this

- `manifest.json` — at `nkz build` time. Includes only public fields (id, displayName, route, navigation, requiredRoles, requiredPlan, hostApiVersion, slots metadata, api.basePath). The host reads this from MinIO when discovering modules.
- `moduleEntry.gen.ts` — generated into `node_modules/.nkz/moduleEntry.gen.ts` with a banner comment and source maps pointing back to `Module.tsx`. The IIFE bundle is built from this.
- `vite.config.ts` — single line: `export default defineConfig(nkzModulePreset())`. The preset reads `Module.tsx` and configures externals, output format, MinIO upload path, etc.

### Backward compatibility

`@nekazari/module-builder` v1.1 supports both paths during the migration:

1. **New path (preferred)**: project exports `defineModule()` from `src/Module.tsx`. Preset generates everything.
2. **Legacy path**: project has hand-written `src/moduleEntry.ts` that calls `window.__NKZ__.register({...})`. Preset detects it and skips codegen. No changes required to keep building.

Migration is module-by-module on its own schedule.

## 4. Frontend hooks — the developer's API to the platform

Provided by `@nekazari/module-kit`. Each hook returns a typed object scoped to the current tenant + user from gateway headers. The developer never writes `fetch`, never handles JWT cookies, never sees `Fiware-Service` headers.

```ts
// Auth
const { user, tenantId, roles, hasRole, hasPlan } = useAuth();

// NGSI-LD entities (CRUD + subscriptions)
const { useEntity, useEntities, createEntity, updateEntity, deleteEntity, subscribeToType } = useOrion();
const { data: parcels, isLoading, error } = useEntities('AgriParcel', { q: 'category=="vineyard"' });

// Timescale time series
const { useTimeseries, useAggregate } = useTimeseries();
const { data } = useTimeseries('soil_observations', { entityId, attr: 'moisture', from, to });

// i18n
const { t, lang, setLang } = useI18n();

// File storage (MinIO via presigned URLs)
const { upload, getUrl, list } = useFiles();

// Cross-module event bus (namespaced per module — see §7)
const { emit, on } = usePlatformEvents();
emit('analysis-complete', { parcelId });          // becomes 'module:soil-health:analysis-complete'
on('parcel:selected', ({ parcelId }) => { /* … */ });   // platform-level event, no namespace

// Module's own backend (if the module has api.basePath)
const { useGet, usePost } = useModuleAPI();       // baseUrl pre-wired to api.basePath
```

All hooks have **mock implementations** in `@nekazari/module-kit/mock` used by `nkz dev`. The mocks share schemas with real implementations (see §10).

## 5. Backend SDK — for the 20% of modules with custom server-side code

### `nkz-platform-sdk` Python — `ModuleApp` factory

```python
from nkz_platform_sdk import ModuleApp, OrionClient, TimescaleClient, EventBus

app = ModuleApp(
    id="soil-health",
    description="Soil Health backend",
)

@app.get("/parcels/{parcel_id}/analysis")
async def get_analysis(parcel_id: str, ctx = app.context()):
    orion = OrionClient(ctx.tenant_id)
    timescale = TimescaleClient(ctx.tenant_id)
    parcel = await orion.get_entity(parcel_id, entity_type="AgriParcel")
    series = await timescale.query("soil_observations", entity_id=parcel_id)
    return {"parcel": parcel, "series": series}

@app.on_event("module:install")
async def install(tenant_id: str):
    # First-run setup per tenant
    pass
```

`ModuleApp` pre-wires automatically:

- `require_auth()` middleware on every endpoint (reads `X-Tenant-ID`, `X-User-ID`, `X-User-Roles` from gateway).
- `/health` and `/ready` endpoints, exempted from rate limit and auth.
- CORS from `ALLOWED_ORIGINS` env var.
- Prometheus metrics at `/metrics`.
- OpenAPI at `/openapi.json` with full type schemas.
- Structured JSON logs with `tenant_id`, `user_id`, `module_id`, `trace_id`.
- `Fiware-Service` and `Fiware-ServicePath` auto-injected when constructing `OrionClient(ctx.tenant_id)`.

### Base Docker image

Published as `ghcr.io/nkz-os/module-base:python-3.12-alpine`. Already bundles Python 3.12, uvicorn, and the SDK. Developer's Dockerfile is 3 lines:

```dockerfile
FROM ghcr.io/nkz-os/module-base:python-3.12-alpine
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api/ ./
```

`nkz init` writes this automatically when the developer chooses the backend template.

## 6. DevOps automation — zero K8s YAML for the developer

### Helm chart base

Single chart `nekazari-module-backend` in `nkz/charts/nekazari-module-backend/` covers: Deployment, Service, Ingress, ConfigMap, Secrets, HPA, NetworkPolicy. Parametrised by `values.yaml`.

### Per-module values

Each module with a backend gets one folder in `nkz/gitops/modules-marketplace/<id>/`:

```yaml
# nkz/gitops/modules-marketplace/soil-health/values.yaml
module:
  id: soil-health
  image: ghcr.io/<owner>/<repo>/backend:1.0.0

backend:
  enabled: true
  replicas: 2
  resources:
    cpu: "200m"
    memory: "256Mi"

ingress:
  enabled: true
  path: /api/soil-health

secrets:
  - name: ORION_PASSWORD
    from: orion-credentials
```

### ArgoCD App-of-Apps

A single ArgoCD application `nekazari-module-marketplace` watches `gitops/modules-marketplace/` and templates the base Helm chart per `values.yaml` found. Adding a new backend module = adding one folder + opening a PR to nkz.

For frontend-only modules: no folder needed. The IIFE is pushed to MinIO and discovered via the `marketplace_modules` PostgreSQL table.

### Reusable CI workflow

`nkz-os/.github/workflows/module-build.yml` — every module's `.github/workflows/build.yml` becomes 5 lines:

```yaml
on: [push, pull_request]
jobs:
  build:
    uses: nkz-os/.github/.github/workflows/module-build.yml@main
    secrets: inherit
```

The reusable workflow handles: pnpm install, typecheck, validate manifest, build IIFE, upload to MinIO (on main push), build & push backend image (if Dockerfile present), open PR to nkz gitops repo with updated image tag (if backend changed).

## 7. CLI `nkz`

```bash
nkz init <name> [--with-backend]   # scaffold from template
nkz dev                            # host shell with mocks + HMR
nkz dev --platform <url>           # connect to a real NKZ-OS instance (token from env or `nkz login`)
nkz build                          # IIFE + manifest + (if backend) Docker image
nkz validate                       # manifest schema + i18n parity + NGSI-LD types + hooks usage
nkz publish [--target <url>]       # frontend → MinIO upload; backend → image push + PR to gitops
nkz publish --dry-run              # show plan without executing
nkz logs <tenant>                  # tail pods of this module on a tenant
nkz why <file>                     # explain how a generated file (manifest.json, moduleEntry.gen.ts) was derived
nkz login                          # store auth token for `nkz dev --platform` and `nkz publish`
```

## 8. Security model

### Trust boundaries

Modules run **inside the host's origin**, sharing DOM, `window`, and cookies. This is by design for the current target audience (modules reviewed by Nekazari before marketplace inclusion; or deployed by the operator that trusts them).

For the **open marketplace with untrusted third-party modules**, additional isolation (iframe + CSP per module, signed bundles) is in Phase E (future work).

### Controls included in this design

1. **CSP of data via `X-Module-Id` header**
   The api-gateway injects an `X-Module-Id` header on every request originating from a loaded module (host wires this when calling the module's IIFE). The gateway validates that the requested `type=` (for NGSI-LD) and the requested hypertable (for Timescale) appear in the `data.entities` / `data.timeseries` of the module's manifest. A module declared with `data.entities: ['AgriParcel']` cannot read `Person` even if it crafts the request manually.

2. **Event bus namespacing**
   `usePlatformEvents().emit('foo', payload)` is internally translated to `emit('module:<id>:foo', payload)` by the SDK. Subscribers can listen to specific module namespaces or to platform-level events (`auth:*`, `tenant:*`) which only the host can emit. A module cannot impersonate the host event channel.

3. **Schema-derived mocks**
   The mock layer used by `nkz dev` is generated from the same TypeScript types and OpenAPI schemas that the real SDK uses. A CI test in `module-kit` asserts that for every hook, the mock implementation conforms to the same return type as the real one. This prevents the "works in dev, broken in prod" class of bugs.

4. **Auth never touched by developer code**
   The host injects the JWT cookie. The gateway validates it and injects tenant/user/roles headers. The SDK reads only those headers. The developer never sees or handles tokens — eliminating the "I logged the token by mistake" risk.

### Risks accepted

- Modules can read each other's `window` and DOM. Mitigated by human review + operator sovereignty.
- A buggy module can spam the event bus. Mitigated by gateway rate limit (Phase E) and host's bus implementation throttling per module.
- A module can call `fetch('/api/v1/orion-ld/...')` directly, bypassing the SDK. Mitigated by the `X-Module-Id` header being mandatory at the gateway — a module cannot forge it from the browser because the gateway derives it from the calling IIFE's origin URL pattern (`/modules/<id>/nkz-module.js`).

## 9. Migration strategy

### Compatibility layer

`@nekazari/module-builder` v1.1 supports both the new and legacy paths. Existing modules continue to build and deploy without any change. Migration is opt-in per module.

### Migration order

1. Foundation: Fase A delivered. Template uses `defineModule()`. Docs reflect reality.
2. New modules: from this point, every new module is created with `nkz init` → starts on the new path.
3. Sweep existing modules: categorise by complexity (see §10 table). Within each category, start with the modules most actively under development today — their developers benefit immediately from the new tooling.

### Per-module migration checklist (used by Fase D)

For each existing module:

- [ ] Replace `src/moduleEntry.ts` with `src/Module.tsx` exporting `defineModule({...})`.
- [ ] Delete hand-written `manifest.json` (generated from `defineModule`).
- [ ] Simplify `vite.config.ts` to a one-liner using `nkzModulePreset()`.
- [ ] Replace ad-hoc `fetch('/api/...')` with the corresponding hook (`useOrion`, `useTimeseries`, `useModuleAPI`).
- [ ] If backend exists: replace hand-written FastAPI app with `ModuleApp` from `nkz-platform-sdk`, replace hand-written Dockerfile with the 3-line one based on `nkz-os/module-base`.
- [ ] If gitops: move K8s YAMLs to a `values.yaml` in `nkz/gitops/modules-marketplace/<id>/`. Delete per-module ArgoCD apps.
- [ ] If CI: replace workflow with the 5-line reusable workflow.
- [ ] Run `nkz validate` and `nkz build`. Smoke test in `nkz dev`. Then deploy.

## 10. Roadmap by phases

Each fase delivers usable value on its own. None requires the next to be useful.

| Fase | Content | Effort | Outcome |
|------|---------|--------|---------|
| A | Foundation frontend-first | 1.5-2 weeks | New developer can create + run + see a frontend-only module locally in 60 seconds |
| B | Backend SDK (`ModuleApp`, `OrionClient` v2, `TimescaleClient`, base Docker image) | 1-1.5 weeks | Modules with backend become 50 lines of Python + 3 lines of Dockerfile |
| C | DevOps automation (Helm chart base, App-of-Apps, reusable CI, `nkz publish`) | 1-1.5 weeks | Zero K8s YAML for the developer. `nkz publish` does the right thing |
| D | Migration sweep of existing 18 modules | 2-3 weeks (parallelisable) | Everything in production runs on the new model |
| E (future) | Sandboxing (iframe + CSP per module), signed bundles, rate limit per module, audit log | Triggered by opening marketplace to untrusted third parties |

### Per-module effort estimates for Fase D

| Category | Modules | Effort each |
|---|---|---|
| Frontend simple | agrienergy, backup, cadastral-spain, carbon, connectivity, eu-elevation, lidar, soil | 0.5 day |
| Frontend medium | cue, gis-routing, odoo, robotics, vpn, zulip | 1 day |
| Frontend + backend | crop-health, datahub, vegetation-health, intelligence | 2 days |
| Backend complex | bioorchestrator, n8n | 3 days |

Total: ~25 dev-days. With 2 engineers in parallel: 2-3 weeks.

## 11. Open questions and future work

- **Module versioning vs host versioning**: today modules pin `hostApiVersion` and the host checks before loading. What happens when a host upgrade breaks a module? Today: host refuses to load and shows an error. Future: per-tenant pinning of module versions so an upgrade can roll out gradually.
- **Module dependencies on other modules**: should `n8n-nkz` be able to declare `requires: ['intelligence']`? Today, no. Possibly add `dependencies` field in `defineModule` later if real cases appear.
- **Per-tenant configuration UI**: `ModuleConfig` (encrypted per tenant) exists in the Python SDK. There's no admin UI yet to set values. Future work — surface in the marketplace UI.
- **Module marketplace UI for self-hosted operators**: today modules are added via direct PostgreSQL insert or admin API. Future: a UI in the host where the operator browses available modules from a public registry and installs them.
- **Generic `Property` entities in `data.entities`**: some modules legitimately work with the generic NGSI-LD `Property` entity type or unknown types discovered at runtime. The CSP-of-data check needs an escape hatch — likely `data.entities: ['*']` declared in the manifest, which the operator must opt-in when installing such a module. To be specified in Fase A.

## 12. References

- Inspiration / prior art: [Backstage.io plugin model](https://backstage.io/docs/plugins/create-a-plugin), [Sanity Studio plugin pattern](https://www.sanity.io/docs/plugins), [WordPress Gutenberg `registerBlockType`](https://developer.wordpress.org/block-editor/reference-guides/block-api/), [Shopify App UI Extensions](https://shopify.dev/docs/api/admin-extensions).
- Internal:
  - [2026-05-11 SOTA audit](../../../nekazari/2026-05-11-102928-analiza-nkz-y-el-sistema-de-mdulos-y-dime-si-el.txt)
  - [`module-system-audit-2026-05-12`](../../../memory/module-system-audit-2026-05-12.md)
  - [`design-system-publishing`](../../../memory/design-system-publishing.md)
  - `CLAUDE.md` (will be updated after Fase A to reflect this design)
