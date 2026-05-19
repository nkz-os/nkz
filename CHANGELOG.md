# Changelog

## v1.0.0 (unreleased)

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
