# Compatibility Matrix

Canonical version contracts between the Nekazari host and its packages/modules.
Breaking thesemver ranges should block CI.

## Current versions (2026-08-31)

| Package | Version | npm |
|---------|---------|-----|
| `@nekazari/sdk` | `1.2.0` | [npm](https://www.npmjs.com/package/@nekazari/sdk) |
| `@nekazari/module-kit` | `0.8.0` | [npm](https://www.npmjs.com/package/@nekazari/module-kit) |
| `@nekazari/module-builder` | `2.1.1` | [npm](https://www.npmjs.com/package/@nekazari/module-builder) |
| `@nekazari/ui-kit` | `1.0.2-alpha.4` | [npm](https://www.npmjs.com/package/@nekazari/ui-kit) |
| `@nekazari/viewer-kit` | `0.2.0-alpha.0` | [npm](https://www.npmjs.com/package/@nekazari/viewer-kit) |
| `@nekazari/design-tokens` | `0.1.0-alpha.3` | [npm](https://www.npmjs.com/package/@nekazari/design-tokens) |

## Host ↔ Package contracts

### host `v1.0.4`

> In this monorepo the host consumes the packages via `workspace:*`; the ranges
> below are the contract that external module repos should declare.

| Dependency | Required range | Notes |
|-----------|---------------|-------|
| `@nekazari/sdk` | `>=1.2.0 <2` | i18n singleton, auth hooks, NGSI-LD client |
| `@nekazari/module-kit` | `>=0.8.0 <1` | `defineModule()`, `withModuleProvider()`, hooks |
| `@nekazari/module-builder` | `>=2.1.0 <3` | Vite preset `nkzModulePreset()` |
| `@nekazari/ui-kit` | `>=1.0.0 <2` | SlotShell, Card, design system components |
| `@nekazari/viewer-kit` | `>=0.2.0 <1` | SlotShellCompact, viewer utilities |
| `@nekazari/design-tokens` | `>=0.1.0 <1` | CSS custom properties |

### React & tooling shared across all packages

| Dependency | Required range | Notes |
|-----------|---------------|-------|
| `react` | `^18.3.1` | Singleton via MF 2.0 |
| `react-dom` | `^18.3.1` | Singleton via MF 2.0 |
| `react-router-dom` | `^6.0.0` | Singleton via MF 2.0 |
| `vite` | `>=6.0.0` | Build tool; module-builder peer (host on 6.x) |
| `@vitejs/plugin-react` | `^4.0.0` | JSX transform; pin to 4.x (6.x incompatible) |
| `@module-federation/vite` | `1.16.8` (PINNED) | **DO NOT BUMP** — see below |

## Module ↔ Host contract (`hostApiVersion`)

Every module declares `hostApiVersion` in its `defineModule()` call. The host
**should** validate this at registration time (not yet enforced — tracked as P1.6
implementation).

| `hostApiVersion` | Meaning |
|------------------|---------|
| `^2.0.0` | Module Federation 2.0 runtime: `loadRemote()`, dynamic remotes, singleton sharing |
| `^1.0.0` | IIFE legacy: `window.__NKZ__.register()` — **deprecated** |

The host's federation runtime version is determined by the `@module-federation/vite`
version in `module-builder` and the host's own MF runtime (`@module-federation/runtime`).

> **⚠️ `@module-federation/vite` is PINNED to `1.16.8`** in both the host and
> `module-builder`. `1.16.9`/`1.16.10` statically inline React's `loadShare` proxy,
> creating a circular `react ⇄ @nekazari/design-tokens` import that hits React's
> binding in TDZ (`can't access 'y' before initialization`) and leaves the host stuck
> on the splash screen (full outage, incident 2026-06-24). Any bump must be deliberate
> and smoke-tested.

## How to bump a package

1. **SDK / module-kit / module-builder**: bump in `packages/<name>/package.json`, run `pnpm install` to update lockfile, commit, push to main. Trusted Publishing CI auto-publishes to npm.
2. **ui-kit / viewer-kit / design-tokens**: same flow.
3. **After publish**: run `pnpm update <pkg>` in `apps/host/` and in affected module repos. Update this matrix.

## Validation (CI)

The host CI (`test.yml`) should verify:

```bash
# Host has compatible SDK version
node -e "const p = require('./apps/host/package.json'); const d = p.dependencies['@nekazari/sdk']; console.log(d); process.exit(require('semver').satisfies('1.2.0', d) ? 0 : 1)"
```

Module CI (in each module repo) should verify:

```bash
# Module declares a valid hostApiVersion
HOST_API=$(node -e "const m = require('./dist/manifest.json'); console.log(m.hostApiVersion)")
node -e "process.exit(require('semver').satisfies('2.0.0', '$HOST_API') ? 0 : 1)"
```

> **Note**: CI enforcement is not yet implemented. This document is the first step — it makes the contract explicit. Adding CI checks is tracked as part of P1.6 follow-up.

## What happens on mismatch

| Mismatch | Symptom | Fix |
|----------|---------|-----|
| SDK < 1.2.0 | `useTranslation` not found, `i18n` undefined | Bump `@nekazari/sdk` in host |
| module-kit < 0.8.0 | `defineModule` schema mismatch, slots rejected | Bump `@nekazari/module-kit` in module |
| module-builder < 2.1.0 | IIFE output instead of MF 2.0 | Bump `@nekazari/module-builder` in module |
| `@module-federation/vite` != 1.16.8 | Host stuck on splash (TDZ `can't access 'y'`) | Pin to `1.16.8` in host + module-builder |
| `@vitejs/plugin-react` 6.x | Build errors about `jsx-runtime` | Pin to `^4.0.0` in module |
| `hostApiVersion: ^1.0.0` | `window.__NKZ__` undefined | Module needs MF 2.0 migration |
