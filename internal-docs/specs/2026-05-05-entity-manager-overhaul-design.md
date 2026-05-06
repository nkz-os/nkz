# Entity-Manager Overhaul — Design Spec

**Date:** 2026-05-05
**Author:** brainstorming session (Claude + user)
**Target service:** `nkz/services/entity-manager/`
**Status:** Approved design, pending implementation plan (`writing-plans` next)

---

## Goal

Stabilize and modernize the platform's entity-management backend in three controlled phases, without regressing production behavior. Delivers:

- **Phase A** — Fix critical bugs returning 500 in production today.
- **Phase B** — Close FIWARE compliance violations, remove zombie NDVI code (entity-manager + host + risk-worker), patch high-severity security issues.
- **Phase C** — Decompose the 8.6k-line monolithic `entity_management_api.py` into a modular Blueprint architecture, with smoke-test coverage as safety net.

Each phase deploys independently, is reversible via git tag + image rebuild, and leaves the service in a strictly better state than before. No behavioral changes to existing API contracts.

---

## Context — Audit findings (summary)

Audit conducted on `entity_management_api.py` (8,633 lines, 86 routes, 9 distinct domains).

**Active production bugs (verified against Flask 3.0.0 + auth_middleware.py):**
- `g.get('user_roles', [])` at L3435 — attribute name mismatch: middleware sets `g.roles`, not `g.user_roles`. `.get()` is valid in Flask 3.0 but returns `[]` because the key doesn't exist. `POST /api/assets` silently denies all requests (403), not 500.
- `g.username` at L7744 — does not exist (middleware sets `g.user`). Causes `AttributeError` → 500. Audit log silently never writes.
- `requested_by = (getattr(g, 'current_user', {}) or {}).get('email')` at L1164 — `g.current_user` is not set by middleware; `getattr` returns `{}`, `.get('email')` returns `None`. Field is always `NULL`, no crash.
- Tier mapping inline at L7667 (`{'basic': 0, 'premium': 1, 'pro': 1, 'enterprise': 2}`) + reverse (`{0: 'basic', 1: 'pro', 2: 'enterprise'}`) — contradicts canonical `services/common/tier_quotas.py` (`pro=1, premium=2, enterprise=3`). Premium tenants get mapped to level 1 (same as Pro), enterprise to 2 (same as canonical Premium). Reverse mapping omits Premium entirely.

**FIWARE mandate violations (subset; remaining cases out of scope):**
- `POST /api/sensors/register` writes to Postgres `sensors` *before* creating the Orion-LD entity (L2380-2545). On Orion failure, Postgres becomes the false source of truth.
- 7 NDVI endpoints (L1117-1965) maintain `ndvi_jobs`/`ndvi_results` tables in Postgres — should be `DataProcessingJob` in Orion-LD per the LiDAR precedent. **Resolved by full removal**, not migration: the vegetation module (`nkz-module-vegetation-health`, deployed 2026-05-01) replaces this functionality with its own `vegetation_*` schema. Host frontend has zero callers of the legacy NDVI methods.
- Commands written directly to `commands` table (L3141-3184) — **deferred to a separate spec** (touches IoT pipeline, out of scope here).

**Security:**
- Vercel Blob endpoint (L5379-5457) returns `BLOB_READ_WRITE_TOKEN` to any authenticated client — zombie code from before MinIO migration.
- `subprocess.run(['kubectl', ...])` at L3538 to read AEMET secret from inside the pod — requires excessive RBAC.
- `POST/DELETE /entity-types/<category>/<type_name>` (L785, L824) lack `@require_auth`.
- NGSI-LD `application/ld+json` POST/PATCH at `/instances/*` (L2090, L2152) declare the content type without injecting `@context` in the body.

**Maintainability:**
- 86 routes, 9 domains, no Blueprint separation. Refactor blocks any future intervention.
- Test coverage on routes: 0%. Existing tests cover only `tier_quotas` and `module_gating` helpers (75 lines total).
- Pattern for reading user roles duplicated 15+ times across the file.

**NDVI dependency on risk-worker:**
- `risk-worker/risk_processor.py:305-339` reads `ndvi_results` for agronomic risk computation.
- Vegetation module does **not** write to this table — it has its own schema.
- Confirmed by user: risk-worker is being replaced shortly; the read can be removed cleanly without migration to the new source.

---

## Decisions taken during brainstorming

| # | Decision | Chosen | Reasoning |
|---|---|---|---|
| 1 | Scope | **D — phased A+B+C this week** | Bugs are bleeding now; refactor without phasing risks "a week with no entity-manager" |
| 2 | NDVI handling in B | **A — full removal** | Zero callers in host; vegetation module replaces functionality; risk-worker read removed in same phase |
| 3 | Safety net for C | **D — smoke tests + mechanical extraction** | 0% route coverage today; smoke tests are cheap (1-2 days) and catch the dominant failure modes (missing imports, lost routes) |
| 4 | Deploy cadence | **a1 — one deploy per phase** | Smaller blast radius; phase A stays in production stable while B and C iterate |
| 5 | Git flow | **b1 — one branch per phase, PR to main** | Respects branch protection (5 CI checks); each PR independently revertable |
| 6 | Coordination | **c1 — no parallel work in `entity_management_api.py`** | Confirmed by user: nothing else planned in this file this week |
| 7 | Pre-requisite | **Merge `fix/module-type-removed-query` first** | Clean baseline; avoids cross-conflicts |
| 8 | Extraction strategy in C | **B-pattern for leaves + C-pattern for coupled domains** | Approved trade-off: bigger commits where helpers are private to the domain (weather, admin, assets, entities, sync); split route-move and helper-promote where helpers are shared (modules, sensors) |

---

## Phase A — Critical bugs

**Branch:** `fix/entity-manager-critical-bugs`
**Tag pre-deploy:** `entity-manager-pre-A`
**Tag post-deploy:** `entity-manager-post-A`
**Estimated time:** 1-2 hours
**Services redeployed:** `entity-manager`

**Edits in `nkz/services/entity-manager/entity_management_api.py`:**

| # | Line | Before | After | Symptom |
|---|---|---|---|---|
| A1 | 3435 | `g.get('user_roles', [])` | `getattr(g, 'roles', None) or []` | `g.user_roles` key doesn't exist (middleware sets `g.roles`). `.get()` returns `[]` → all users denied (403). Not 500. |
| A2 | 7744 | `g.username` | `getattr(g, 'user', None) or 'unknown'` | `AttributeError` → 500. Middleware sets `g.user`, not `g.username`. |
| A3 | 1164 | `getattr(g, 'current_user', {}).get('email')` | `getattr(g, 'email', None)` | `g.current_user` not set → always `None`. No crash, but `requested_by` is always NULL. |
| A4 | 7667-7672 | inline `plan_hierarchy` dict (both directions) | `from common.tier_quotas import PLAN_LEVELS as plan_hierarchy` | Premium→1 (should be 2), enterprise→2 (should be 3), reverse omits Premium. Module gating broken for upper tiers. |

> **Note on former A2 (L5119 `g.get('roles', [])`):** verified correct in Flask 3.0.0 — both method and attribute name are valid. Removed from Phase A as it's not a bug. The `hasattr(g, 'roles')` check via `getattr` is functionally equivalent but unnecessary here.

**A3 is corrected even though NDVI is removed in B.** Phase A must remain consistent on its own — if B is delayed, A introduces no regression.

**Verification post-deploy:**
- `curl -X POST` to `/api/assets` with valid cookie returns 200/201/400 (not 403).
- After a governance change, `SELECT * FROM tenant_governance_audit ORDER BY created_at DESC LIMIT 1` shows the new row with `changed_by` ≠ `'unknown'`.
- A new NDVI job (if NDVI still exists when A ships) records `requested_by` ≠ `NULL`.
- New unit test in `tests/test_quota_enforcement.py` asserts `PLAN_LEVELS['premium'] == 2` and `PLAN_LEVELS['enterprise'] == 3`.

---

## Phase B — FIWARE compliance, security, NDVI cleanup

**Branches (3, parallel-mergeable):**
- `fix/entity-manager-fiware-and-cleanup` (entity-manager)
- `chore/host-remove-ndvi-zombie` (host frontend)
- `fix/risk-worker-remove-ndvi-read` (risk-worker)

**Tags pre-deploy:** `entity-manager-pre-B`, `host-pre-B`, `risk-worker-pre-B`
**Tags post-deploy:** `entity-manager-post-B`, `host-post-B`, `risk-worker-post-B`
**Estimated time:** 2-3 days
**Services redeployed:** `frontend-host`, `entity-manager`, `risk-worker` (in this order)

### B.1 — NDVI removal

| Repo / File | Change | Lines |
|---|---|---|
| `nkz/services/entity-manager/entity_management_api.py` | Delete `/ndvi/*` endpoints and private helpers (`_serialize_job`, `_serialize_result`, `_normalize_polygon` if NDVI-only) | ~850 (L1117-1965) |
| `nkz/services/entity-manager/module_health.py:20` | Remove `'ndvi_jobs', 'ndvi_results'` from monitored tables list | 1 line |
| `nkz/services/risk-worker/risk_processor.py` | Delete `_get_ndvi_data()` (L305-339) and call site (L494-496 that adds to `data_sources["ndvi"]`) | ~40 lines |
| `nkz/apps/host/src/services/api.ts` | Delete 7 methods (`createNdviJob`, `getNdviJobs`, `getNdviJob`, `deleteNdviJob`, `deleteNdviResult`, `cleanupNdviJobs`, `getNdviResults`) + interceptors at L204, L211 | ~220 lines |
| `nkz/apps/host/src/types/index.ts` | Delete `NDVIJob` (L340) and `NDVIResult` (L365) if orphaned | ~50 lines |

**Not touched:**
- Tables `ndvi_jobs` / `ndvi_results` (frozen historical data; retention decision deferred).
- Parcel attribute `ndviEnabled` (lives in Orion-LD, managed by host `parcelApi.ts`).
- Raster visualization via `titilerApi.ts` (`s3://ndvi-rasters` MinIO bucket).
- Cadastral endpoint `/api/cadastral-api/parcels/<id>/request-ndvi` (separate module).

**Pre-merge gate:** `kubectl logs deploy/api-gateway -n nekazari --since=72h | grep "/api/ndvi/"` must return empty.

### B.2 — Sensor write order (Orion-first)

`POST /api/sensors/register` (L2380-2545):
1. Create entity in Orion-LD (source of truth).
2. On 201, INSERT into Postgres `sensors` (local cache + non-NGSI metadata).
3. On Orion failure, return error without touching Postgres.
4. On Postgres failure after Orion-OK, attempt best-effort entity deletion in Orion + log inconsistency.

**No data migration:** existing sensors remain as-is. Change applies to future creations only.

### B.3 — Security

| # | Change | Lines |
|---|---|---|
| B3a | Delete Vercel Blob endpoint (`/api/upload/authorize`, returned `BLOB_READ_WRITE_TOKEN`) | L5379-5457 |
| B3b | Replace `subprocess.run(['kubectl', ...])` with `os.getenv('AEMET_API_KEY')`. Add env var to deployment YAML referencing existing K8s secret | L3538-3548 + manifest |
| B3c | Add `@require_auth` to `POST/DELETE /entity-types/<category>/<type_name>` | L785, L824 |
| B3d | Extract `_get_user_roles()` helper, replace 15+ duplications | top of file |

### B.4 — NGSI-LD compliance

| # | Change | Lines |
|---|---|---|
| B4a | `POST /instances/<entity_type>` — inject `@context` into body before POST | L2090-2096 |
| B4b | `PATCH /instances/<entity_type>/<entity_id>` — same fix | L2152-2158 |
| B4c | `Accept: application/json` GET — add `Link` header via existing `inject_fiware_headers` | L647 |

### Deploy order

`frontend-host` → `entity-manager` → `risk-worker`. All orderings are functionally safe (no active flow), but this order minimizes inconsistency windows.

---

## Phase C — Blueprint refactor

**Branch:** `refactor/entity-manager-blueprints`
**Tag pre-deploy:** `entity-manager-pre-C`
**Tag post-deploy:** `entity-manager-post-C`
**Prerequisite:** smoke tests merged via `test/entity-manager-routes-smoke` PR before C starts.
**Estimated time:** 3-4 days
**Services redeployed:** `entity-manager`

### Target structure

```
nkz/services/entity-manager/
├── entity_management_api.py        # ~250 lines — app, CORS, metrics, /health, /version, blueprint registration
├── blueprints/
│   ├── __init__.py
│   ├── weather.py                  # ~900 — /api/weather/*
│   ├── admin.py                    # ~500 — /api/admin/*, governance, settings
│   ├── assets.py                   # ~650 — /api/assets/*, /api/upload/*, MinIO
│   ├── entities.py                 # ~300 — /instances/*, /api/entities/*, /api/robots/*
│   ├── sync.py                     # ~400 — /api/core/sync/vectorial (WatermelonDB)
│   ├── modules.py                  # ~900 — /api/modules/*, marketplace, deploy
│   └── sensors.py                  # ~750 — /api/sensors/*, /api/devices/*, /api/heartbeat/*
├── helpers/
│   ├── __init__.py
│   ├── auth_helpers.py             # _get_user_roles(), role checks
│   ├── orion_client.py             # NGSI-LD GET/POST/PATCH wrappers
│   └── serialization.py            # entity → dict converters used by ≥2 blueprints
├── auth_middleware.py              # UNCHANGED
├── orion_writer.py                 # UNCHANGED
├── module_upload_service.py        # UNCHANGED
├── module_health.py                # already touched in B
├── parcel_sync.py                  # UNCHANGED
├── entity_manager_gating.py        # UNCHANGED
├── geo_utils.py                    # UNCHANGED
├── tests/
│   ├── test_module_gating.py
│   ├── test_quota_enforcement.py
│   └── test_routes_smoke.py        # NEW (added before C starts)
└── Dockerfile, requirements.txt    # UNCHANGED — entrypoint stays `entity_management_api:app`
```

### Blueprint pattern (template)

```python
# blueprints/weather.py
from flask import Blueprint, request, jsonify, g
from common.auth_middleware import require_auth, inject_fiware_headers

weather_bp = Blueprint('weather', __name__)

@weather_bp.route('/api/weather/parcels/<parcel_id>/forecast', methods=['GET'])
@require_auth
def get_parcel_forecast(parcel_id):
    # ... body identical to current implementation, no functional changes ...
```

In `entity_management_api.py`:
```python
from blueprints.weather import weather_bp
from blueprints.admin import admin_bp
# ...
app.register_blueprint(weather_bp)
app.register_blueprint(admin_bp)
# ...
```

**Strict rules during C:**
- URLs unchanged.
- Function signatures unchanged.
- No logic changes. Bugs found during refactor are tagged `# TODO(post-refactor):` and left in place. Exception: import/scoping errors that prevent the moved code from running.
- Private helpers travel with their blueprint; shared helpers (proven by ≥2 consumers) move to `helpers/`.

### Shared state across blueprints

| Shared item | Access pattern |
|---|---|
| `flask.g.tenant`, `g.user`, `g.roles` | Set by `auth_middleware`, accessed via `from flask import g` |
| Constants (`ORION_URL`, `CONTEXT_URL`, DB env vars) | Imported from main: `from entity_management_api import ORION_URL` |
| `get_db_connection_with_tenant()` | Stays in main initially; promoted to `helpers/db.py` only if used by ≥2 blueprints after extraction |
| Caches (`_limits_cache`) | Travel with primary consumer (`modules.py` likely); promoted to `helpers/cache.py` if shared |

**Circular import risk** — avoided by registering blueprints *after* app initialization and constants in main.

### Extraction order and commit plan

| # | Blueprint | Strategy | Commits |
|---|---|---|---|
| 1 | `weather` | B (single commit) | 1 |
| 2 | `admin` | B | 1 |
| 3 | `assets` | B | 1 |
| 4 | `entities` | B | 1 |
| 5 | `sync` | B | 1 |
| 6 | `modules` | C (route-move, then helper-promote) | 2 |
| 7 | `sensors` | C | 2 |
| 8 | Final cleanup of shared helpers → `helpers/` | — | 1 |

Total: ~10 commits in one branch, one PR.

**B-pattern (single commit per blueprint):** rationale — when I am the executor, the cost of inspecting a 1000-line diff is mine. Smoke tests detect missing routes and broken imports immediately. Reviewer overhead drops without sacrificing safety.

**C-pattern (split route-move from helper-promote):** reserved for `modules` and `sensors` because their helpers (`_get_user_roles`, `inject_fiware_headers`, gating logic) are referenced from multiple domains. Splitting prevents the "moved a helper that another domain still uses" trap.

---

## Smoke testing strategy

**File:** `nkz/services/entity-manager/tests/test_routes_smoke.py` (new)
**Branch:** `test/entity-manager-routes-smoke` — merged before C starts.
**Coverage:** 79 routes (86 original − 7 NDVI removed in B).

**Per route, two assertions:**
1. **Auth gate** — request without cookie → 401.
2. **Happy path** — request with mocked auth + mocked dependencies → expected status (200/201/204) and basic JSON shape (top-level keys).

Existing pattern from `tests/test_quota_enforcement.py` is reused: module-level mocks for `common`, `common.auth_middleware`, `parcel_sync`, `module_metrics` set up before importing `entity_management_api`. External dependencies (`requests`, `psycopg2`, MinIO `boto3`) mocked per-test via fixtures.

**Tests grouped by domain** (`TestWeatherRoutes`, `TestAdminRoutes`, etc.) so each blueprint's tests can travel with it after C if desired.

**Out of scope for smoke tests:**
- Real JWT validation (would need integration tests).
- Race conditions and concurrency.
- Full NGSI-LD schema validation.
- Existing `test_module_gating.py` and `test_quota_enforcement.py` — left untouched.

**Estimated effort:** 1-2 days (~150-200 lines of test code).

---

## Deploy and rollback

### Standard 6-step procedure per phase

1. Tag pre-deploy: `git tag entity-manager-pre-{A|B|C} && git push --tags`.
2. Local smoke: `pytest tests/ -v` green.
3. Build image on server: `sudo docker build --network=host --no-cache -t ghcr.io/nkz-os/nkz/entity-manager:latest -f services/entity-manager/Dockerfile .`.
4. Deploy: `sudo kubectl rollout restart deployment/entity-manager -n nekazari`.
5. Verify: `kubectl rollout status` + manual `curl` against critical paths.
6. Watch 30-60 min: `kubectl logs -f` + basic metrics.

For B, `frontend-host` and `risk-worker` follow analogous procedures (host via CI auto-build on push to `main`; risk-worker via manual build with manifest at `nkz/k8s/addons/analytics/risk/risk-worker-deployment.yaml`).

### Rollback playbook

| Scenario | Action |
|---|---|
| Pod CrashLoopBackOff post-deploy | `sudo kubectl rollout undo deployment/<service> -n nekazari`. If image purged by K3s (Keycloak incident pattern): `git checkout <pre-tag> -- <path>`, rebuild, redeploy |
| Pod up but wrong behavior | Same git-tag rollback path |
| Auth broken for all tenants | Immediate rollback (do not wait for diagnosis) |
| Specific endpoint 500 | Triage logs first if non-critical (admin/governance); immediate rollback if critical (sensors, sync) |
| Risk-worker loops post-B | `sudo kubectl rollout undo deployment/risk-worker -n nekazari`. ~40-line revert |

**Target rollback time:** under 5 minutes from detection to restored service.

### Tagging convention

- Pre-deploy: `entity-manager-pre-{A|B|C}`, `host-pre-B`, `risk-worker-pre-B`.
- Post-deploy: `entity-manager-post-{A|B|C}`, `host-post-B`, `risk-worker-post-B`.

---

## Risks and mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| K3s purges previous image after failed deploy | Medium | High (rollback blocked) | Rebuild from git tag if `kubectl rollout undo` fails |
| Helper moved to wrong blueprint during C | Medium | Medium (silent breakage of another route) | Smoke tests detect immediately; revert and promote to `helpers/` before retrying |
| Semantic bug in B sensor reorder | Low | High (sensor provisioning broken) | Pre-merge validation: scale Orion to 0 deliberately, attempt sensor creation, confirm Postgres untouched |
| Hotfix to `main` during C creates merge conflict | Medium | Medium (rebase work) | C lasts 3-4 days; rebase as needed. If irresolvable, replan affected commit |
| Stale browser cache holding NDVI methods after host deploy | Low | Low (silent 404, no functional impact) | Acceptable; methods unused; cache expires |
| Forgotten zombie endpoint not caught | Medium | Low (debt) | Post-C audit: `grep -r "@.*\.route\|@app.route" services/entity-manager/` against production traffic |

---

## Out of scope (deferred)

| Item | Reason | Tracked in |
|---|---|---|
| Migrate `commands` to NGSI-LD | Touches IoT pipeline (MQTT, IoT Agent, downlink) | `PENDING.md` — new entry |
| Migrate risk-worker to read from vegetation module / Orion-LD | Risk-worker being replaced shortly per user | `PENDING.md` — update existing entry |
| `DROP TABLE ndvi_jobs/ndvi_results` | Data retention decision pending | `PENDING.md` — new entry |
| Migrate NDVI to `DataProcessingJob` SDM | Already replaced by vegetation module's own model | N/A |
| Pin entity-manager image to git SHA (instead of `:latest`) | CI/CD refactor, separate scope | `PENDING.md` — new entry |
| Real integration tests (Orion + Postgres in docker-compose) | High cost, smoke is sufficient for this intervention | None — future project decision |
| Extract globals (`ORION_URL`, etc.) to `config.py` | YAGNI — not blocking the refactor | None |
| Performance fixes (`_gather_usage_for_tenant` cache, etc.) | `_serialize_job` disappears with NDVI; rest is low-priority debt | `PENDING.md` — new entry |

---

## Definition of done

| Phase | Done when |
|---|---|
| A | PR merged + entity-manager redeployed + `curl` confirms A1 not 403 + audit log writes a row with valid `changed_by` after governance change + tag `entity-manager-post-A` applied |
| B | 3 PRs merged + 3 services redeployed + zero traffic to `/api/ndvi/*` over 24h + sensor creation with Orion-down test passes + tags `*-post-B` applied |
| C | PR merged + entity-manager redeployed + 79 routes pass smoke tests + `/health` returns 200 + `entity_management_api.py` is ~250 lines + tag `entity-manager-post-C` applied |

---

## Documentation updates

After each phase:

| Document | Update |
|---|---|
| `PENDING.md` | Close completed entries, add new "deferred" entries from out-of-scope list |
| `nkz/.ai/CURRENT_STATE.md` | Note current state of entity-manager per phase |
| `nkz/CLAUDE.md` | After C: document new blueprint structure (where routes live, where to add new ones) |
| `AGENTS.md` (workspace root) | Update routing if structure change affects task delegation |
| Memory (`/home/g/.claude/projects/.../memory/`) | New entry `entity-manager-overhaul-<final-deploy-date>.md` summarizing changes; update `MEMORY.md` index |

---

## Communication during execution

- Confirmation from user before each phase ("procedo con A").
- Status report after each deploy (changes shipped, verification passed, what is being watched).
- Circuit Breaker Protocol (CLAUDE.md): if anything unexpected appears, stop, report, decide jointly before continuing.
- If smoke tests fail after a blueprint extraction in C: stop, report, do not proceed until diagnosed.

---

## Open questions

None at design time. All scope, ordering, and trade-off decisions confirmed during brainstorming.

---

## Next step

Invoke `superpowers:writing-plans` to produce a step-by-step implementation plan derived from this design.
