# Changelog

## v1.2.0

### Features

- **Module parcel activation** — per-parcel module activation flow:
  entity-manager endpoints (`/api/entities/parcels/{id}/modules/{module}/activate`)
  with tier-quota enforcement (fail-open), parcel-ownership and
  installed-module checks, honest `setup_status` state (pending/ok/error,
  idempotent re-POST as retry), and internal dispatch to module backends via
  the `setup_parcel_url` contract (#529, #530). New table
  `tenant_parcel_modules` (migration 080).
- **nkz-platform-sdk 0.4.0** — `SubscriptionRegistrar` (idempotent per-tenant
  Orion-LD subscription management with periodic heal) and `ModuleActivation`
  (idempotent placeholder entity lifecycle); subscription methods added to
  `OrionClient`. All Orion I/O delegates to `OrionClient` for NGSI-LD header
  compliance (#529).
- **Weather SOTA pack** — terrain aspect/slope via elevation-api 5-point Horn
  method, hourly wind with unit conversion, 3-day water balance with
  telemetry fallback and dedup, forecast cache bypass, AEMET alerts migrated
  to Orion-LD, international weather support (migration 079), per-tenant
  weather discovery (#436–#446, #462–#472).
- **Module publish pipeline** — versioned publicPath for MF2 remotes, pnpm v10
  support, `package_json_file` input in the reusable publish workflow
  (#449–#461).
- **Carbon module proxy** — `/api/carbon` route through api-gateway (#447).

### Security

- **Public-repo audit** — canonical AGPL-3.0 license text (GitHub now detects
  the license correctly), Dependabot version updates configured, production
  IP removed from K8s templates, explicit main-branch review policy
  documented (#473). Repo-level secret scanning, push protection and
  Dependabot security updates enabled.
- **Dependency security wave** — 35+ security updates across all Python
  services and the frontend (PyJWT 2.12, cryptography 46, gunicorn 22,
  Flask 3.1, flask-cors 6, requests 2.33, axios 1.16, vitest 3); open
  security alerts reduced from 227 to under 70. Semver-major updates of
  Module Federation shared singletons excluded by policy (#490, #519).

### Fixes

- **Slot preload retry** — a transient `loadRemote` failure no longer leaves a
  module without viewer slots for the whole session (layers missing in
  Entities until visiting the module page) (#534).
- **i18n locale recovery** — repaired broken JSON in en/es `common.json`
  (missing comma silently disabled the whole namespace) (#480). The osm
  base-layer default that rode along in #480 was unrequested and is reverted:
  PNOA remains the default (#531).
- **agro-status Orion fallback** chain hardened (#443–#446);
  timeseries-reader telemetry fallback (#462); EOProduct added to the
  platform @context (#464); host terrain module priority (#449); frontend
  polish p3 (#434, #442); stale ui-kit shadows removed (#437).
- **Core services SHA-pinned** — remaining core images pinned by digest
  (#430–#435).

## v1.1.0

### Features

- **Admin panel master-detail layout** — full tenant/user management with
  create/edit/delete operations (#344)
- **Admin panel design tokens + dark mode** — migrated admin panel to
  `@nekazari/design-tokens`, added dark mode support, fixed horizontal overflow (#346)
- **User registration wizard** — repaired user registration flow with new
  multi-step wizard UI (#345)
- **NetworkPolicies phase 2** — applied restrictive network policies across
  core services, moved to gitops-config for declarative management

### Fixes

- **Image SHA pinning** — all core NKZ images pinned by SHA256 digest or
  `:sha-<commit>` tag in Helm chart and production manifests. Never `:latest`.
  Incident 2026-05-26: `:latest` on frontend-host caused complete landing page
  outage due to cross-pod hashed asset 404s during rolling update.
- **api-gateway OOM** — raised memory limit from 256Mi to 1Gi to stop gunicorn
  worker OOM kills under load (#342)
- **Keycloak native registration disabled** — removed native Keycloak
  registration form, added register link redirecting to platform wizard
- **CI workflow_dispatch** — allowed manual trigger for Docker image pushes

### Helm Chart

- All NKZ images pinned: api-gateway (`:sha-548550f`), entity-manager
  (`:sha-548550f`), frontend-host (`@sha256:54c80401`), keycloak
  (`@sha256:18f442d6`)
- MinIO pinned to `RELEASE.2025-09-07T16-13-09Z`
- Templates support both `:tag` and `@sha256:` digest formats
- Default `imagePullPolicy: IfNotPresent` on all deployments
- `UPGRADE.md` added with v1.0.0 → v1.1.0 migration steps

### Documentation

- Base manifest `frontend-host-deployment.yaml` now warns against `:latest`
- CLAUDE.md image pinning rule strengthened with incident reference
- Helm chart README documents image pinning policy

## v1.0.0

### Developer Experience

- **docker-compose aligned with Module Federation 2.0** — local stack boots empty,
  developer publishes their own module. Removed IIFE-era seed data and added
  `/modules/` proxy to MinIO.
- **QUICKSTART.md** — 7-step walkthrough from zero to a live module in ~30 min.
- **Auto-deploy endpoint** `POST /api/modules/<id>/dist` — upload a built `dist/`
  and the entity-manager registers it in marketplace_modules + MinIO in one call.
- **CLI generator** `pnpm create @nekazari/module my-module` — interactive
  prompts replace manual template find-and-replace.
- **Compatibility matrix** (`COMPATIBILITY.md`) — version contracts between host
  and all `@nekazari/*` packages.

### Architecture

- **initI18n moved from SDK to host** — eliminates the defensive `unwrapI18nPlugin`
  that compensated for Module Federation CJS interop. Net -130 lines from SDK.
- **`withModuleProvider()` helper** in `@nekazari/module-kit@0.6.1` — replaces
  17-line duplicated slot-wrapping pattern in vegetation-prime, lidar, odoo-erp.
- **Federation runtime health** `GET /api/admin/modules/health` — HEADs every
  active module's `mf-manifest.json` and validates `publicPath`. Returns 200/207.

### Operations

- **Helm chart** `charts/nekazari/` — umbrella chart with 9 subcharts (postgresql,
  mongodb, redis, minio, keycloak, orion-ld, api-gateway, entity-manager, frontend).
  26 rendered resources. `helm install nekazari ./charts/nekazari -n nekazari`.
- **Playwright E2E suite** — docker-compose-based CI workflow that boots the full
  stack, logs in via Keycloak, deploys a test module, and verifies zero Federation
  console errors.

### Packages published

| Package | Version |
|---------|---------|
| `@nekazari/sdk` | `1.1.3` |
| `@nekazari/module-kit` | `0.6.2` |
| `@nekazari/module-builder` | `2.0.2` |
| `@nekazari/design-tokens` | `0.1.0-alpha.3` |
| `@nekazari/ui-kit` | `1.0.2-alpha.4` |
| `@nekazari/viewer-kit` | `0.1.1-alpha.6` |
| `@nekazari/create-module` | `0.1.0` |
| `nkz-platform-sdk` (PyPI) | `0.3.0` |

All npm packages published with SLSA provenance via Trusted Publishing OIDC.

### Fixes

- Module Federation 2.0 migration complete (16 modules)
- `@vitejs/plugin-react` peer pinned to `^4.0.0` (6.x incompatible with Vite 5)
- `python:3.11-alpine3.20` pin (Alpine 3.21 ships broken gcc)
- `--no-install-recommends` in entity-manager Dockerfile
- 8 `fix/tenant-headers` branches merged to main in module repos
- Keycloak realm: `directAccessGrantsEnabled` for password grant

### Known gaps (post-v1.0.0)

- No multi-tenant module marketplace UI (modules are global)
- `@remix-run/router` Vite/Rollup resolution bug in host build (worked around in CI)
- Legacy `catastro-spain` module still on `window.__NKZ__.register()` API
- IIFE bundles still in MinIO buckets (~2 MB each, not served)

---

## v1.0.0-rc.1

Initial release candidate. See [GitHub Release](https://github.com/nkz-os/nkz/releases/tag/v1.0.0-rc.1).
