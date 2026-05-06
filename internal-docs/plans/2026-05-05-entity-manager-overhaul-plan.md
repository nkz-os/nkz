# Entity-Manager Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize the entity-manager service in three controlled phases: fix 4 critical production bugs (A), remove NDVI zombie code + close FIWARE/security gaps (B), and decompose the 8.6k-line monolith into Flask Blueprints with smoke tests as safety net (C).

**Architecture:** Phase A applies 4 surgical fixes to `entity_management_api.py`. Phase B spans 3 repos (entity-manager, host frontend, risk-worker) to remove 7 NDVI endpoints, reorder sensor write to Orion-first, close 4 security gaps, and fix 2 NGSI-LD compliance violations. Phase C extracts 7 domain Blueprints from the monolith, guarded by ~79 smoke tests written as a prerequisite.

**Tech Stack:** Python 3.11, Flask 3.0.0, PostgreSQL (psycopg2), Orion-LD (NGSI-LD), pytest, Docker, K3s

---

## Prerequisite — Merge `fix/module-type-removed-query`

Before any phase starts, merge this branch to `main`. It fixes a SQL query referencing a dropped column (`module_type` from migration 073) and is the clean baseline for all subsequent work.

- [ ] **Step 1: Check branch state**

```bash
cd /home/g/Documents/nekazari/nkz && git log --oneline fix/module-type-removed-query --not main
```

Expected: 1 commit `a993a0a fix(entity-manager): remove module_type from get_tenant_modules query`

- [ ] **Step 2: Open PR**

```bash
cd /home/g/Documents/nekazari/nkz
gh pr create --title "fix(entity-manager): remove module_type from get_tenant_modules query" \
  --body "Fixes SQL query referencing dropped column from migration 073. Clean baseline for entity-manager overhaul." \
  --base main --head fix/module-type-removed-query
```

- [ ] **Step 3: Merge after CI passes** (5 checks: lint, type-check, test, build, security)

```bash
gh pr merge fix/module-type-removed-query --merge --delete-branch
```

- [ ] **Step 4: Pull main**

```bash
git checkout main && git pull origin main
```

---

## Phase A — Critical Bugs (4 fixes, ~1 hour)

**Branch:** `fix/entity-manager-critical-bugs`
**Target file:** `nkz/services/entity-manager/entity_management_api.py`

### Task A.1: Create branch + fix `g.get('user_roles', [])` → `getattr(g, 'roles', None) or []`

- [ ] **Step 1: Create branch**

```bash
cd /home/g/Documents/nekazari/nkz
git checkout main && git pull origin main
git checkout -b fix/entity-manager-critical-bugs
```

- [ ] **Step 2: Fix line 3435 in `POST /api/assets`**

The middleware sets `g.roles` (list), not `g.user_roles`. The `.get()` method exists in Flask 3.0 but the key `user_roles` doesn't match — returns `[]` always, denying all users with 403.

Edit `services/entity-manager/entity_management_api.py` line 3435:

```python
# Before:
        user_roles = g.get('user_roles', [])
# After:
        user_roles = getattr(g, 'roles', None) or []
```

- [ ] **Step 3: Commit A.1**

```bash
git add services/entity-manager/entity_management_api.py
git commit -m "fix(entity-manager): correct roles attribute name in create_asset (g.user_roles → g.roles)

Bug: middleware sets g.roles, not g.user_roles. g.get('user_roles', [])
always returned [] in Flask 3.0, silently denying all users (403).
Fix: use getattr(g, 'roles', None) or [] to match the actual attribute."
```

### Task A.2: Fix `g.username` → `getattr(g, 'user', None) or 'unknown'`

- [ ] **Step 1: Fix line 7744 in governance audit log**

Middleware sets `g.user` (preferred_username from JWT), not `g.username`. Accessing `g.username` raises `AttributeError` → 500 on governance changes, and the audit row is never written.

Edit `services/entity-manager/entity_management_api.py` line 7744:

```python
# Before:
                g.username,
# After:
                getattr(g, 'user', None) or 'unknown',
```

- [ ] **Step 2: Commit A.2**

```bash
git add services/entity-manager/entity_management_api.py
git commit -m "fix(entity-manager): correct g.username → g.user in governance audit log

Bug: middleware sets g.user (not g.username). AttributeError → 500
on every governance change; audit row silently never written.
Fix: getattr(g, 'user', None) or 'unknown'"
```

### Task A.3: Fix `g.current_user` → `g.email` in NDVI job creation

- [ ] **Step 1: Fix line 1164**

Middleware sets `g.email`, not `g.current_user`. `getattr(g, 'current_user', {})` returns `{}`, so `.get('email')` always returns `None` — `requested_by` is always NULL. No crash, but data is silently wrong.

Edit `services/entity-manager/entity_management_api.py` line 1164:

```python
# Before:
    requested_by = (getattr(g, 'current_user', {}) or {}).get('email')
# After:
    requested_by = getattr(g, 'email', None)
```

- [ ] **Step 2: Commit A.3**

```bash
git add services/entity-manager/entity_management_api.py
git commit -m "fix(entity-manager): correct g.current_user → g.email in NDVI requested_by

Bug: middleware sets g.email (not g.current_user). The getattr chain
always returned None, so requested_by was silently always NULL.
Fix: getattr(g, 'email', None) directly.
Note: NDVI code will be removed in Phase B; this fix ensures correctness
if Phase B is delayed."
```

### Task A.4: Replace inline tier mapping with canonical import

- [ ] **Step 1: Fix lines 7662-7672 — replace both inline dicts**

The inline `mapping` dicts contradict `services/common/tier_quotas.py:PLAN_LEVELS`:
- Inline: `{'basic': 0, 'premium': 1, 'pro': 1, 'enterprise': 2}` — premium mapped to 1 (same as pro), enterprise to 2
- Canonical: `{'basic': 0, 'pro': 1, 'premium': 2, 'enterprise': 3}`
- Reverse inline: `{0: 'basic', 1: 'pro', 2: 'enterprise'}` — omits premium entirely

Edit `services/entity-manager/entity_management_api.py` lines 7662-7672:

```python
# Before:
        if plan_type and plan_level is None:
            # Map string to level
            mapping = {'basic': 0, 'premium': 1, 'pro': 1, 'enterprise': 2}
            plan_level = mapping.get(plan_type, 0)
        elif plan_level is not None and not plan_type:
            # Map level to string
            mapping = {0: 'basic', 1: 'pro', 2: 'enterprise'}
            plan_type = mapping.get(plan_level, 'basic')

# After:
        from common.tier_quotas import PLAN_LEVELS as plan_hierarchy, LEVEL_TO_TIER
        if plan_type and plan_level is None:
            plan_level = plan_hierarchy.get(plan_type, 0)
        elif plan_level is not None and not plan_type:
            plan_type = LEVEL_TO_TIER.get(plan_level, 'basic')
```

- [ ] **Step 2: Add test asserting canonical plan level values**

Append to `services/entity-manager/tests/test_quota_enforcement.py`:

```python
def test_canonical_plan_levels_match_spec():
    """PLAN_LEVELS must match the 4-tier spec: basic=0, pro=1, premium=2, enterprise=3."""
    from common.tier_quotas import PLAN_LEVELS
    assert PLAN_LEVELS['basic'] == 0
    assert PLAN_LEVELS['pro'] == 1
    assert PLAN_LEVELS['premium'] == 2
    assert PLAN_LEVELS['enterprise'] == 3


def test_level_to_tier_roundtrip():
    """Every tier should round-trip through PLAN_LEVELS → LEVEL_TO_TIER."""
    from common.tier_quotas import PLAN_LEVELS, LEVEL_TO_TIER
    for tier, level in PLAN_LEVELS.items():
        assert LEVEL_TO_TIER[level] == tier
```

- [ ] **Step 3: Run tests**

```bash
cd /home/g/Documents/nekazari/nkz/services/entity-manager
python -m pytest tests/test_quota_enforcement.py -v
```

Expected: 7 tests PASS (5 existing + 2 new)

- [ ] **Step 4: Commit A.4**

```bash
git add services/entity-manager/entity_management_api.py services/entity-manager/tests/test_quota_enforcement.py
git commit -m "fix(entity-manager): use canonical PLAN_LEVELS from tier_quotas instead of inline mapping

Bug: inline tier mapping had premium=1 (should be 2), enterprise=2 (should be 3),
and the reverse mapping omitted premium entirely. Module gating was broken for
upper tiers — premium tenants could not access premium-gated modules.
Fix: import PLAN_LEVELS and LEVEL_TO_TIER from common.tier_quotas."
```

### Task A.5: Tag, PR, deploy

- [ ] **Step 1: Tag pre-deploy**

```bash
cd /home/g/Documents/nekazari/nkz
git tag entity-manager-pre-A
git push --tags origin fix/entity-manager-critical-bugs
```

- [ ] **Step 2: Run full test suite locally**

```bash
cd /home/g/Documents/nekazari/nkz/services/entity-manager
python -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 3: Open PR**

```bash
cd /home/g/Documents/nekazari/nkz
gh pr create --title "fix(entity-manager): 4 critical bug fixes (Phase A)" \
  --body "$(cat <<'EOF'
## Summary
- **A1:** Fix `g.user_roles` → `g.roles` in create_asset (silent 403 denial)
- **A2:** Fix `g.username` → `g.user` in governance audit (AttributeError → 500)
- **A3:** Fix `g.current_user` → `g.email` in NDVI requested_by (silent NULL)
- **A4:** Replace inline tier mapping with canonical `tier_quotas.PLAN_LEVELS`

## Test plan
- [x] Unit tests pass (test_quota_enforcement.py, 7 tests including 2 new canonical-tier tests)
- [ ] CI checks pass (5 required)
- [ ] Post-deploy: `curl -X POST /api/assets` with valid cookie returns 200/201/400 (not 403)
- [ ] Post-deploy: governance change writes audit row with valid changed_by
EOF
)" --base main --head fix/entity-manager-critical-bugs
```

- [ ] **Step 4: Merge after CI + deploy**

```bash
gh pr merge fix/entity-manager-critical-bugs --merge --delete-branch
```

- [ ] **Step 5: Build & deploy on server**

```bash
ssh g@109.123.252.120
cd /home/g/Documents/nekazari/nkz
git pull origin main
sudo docker build --network=host --no-cache -t ghcr.io/nkz-os/nkz/entity-manager:latest -f services/entity-manager/Dockerfile .
sudo docker push ghcr.io/nkz-os/nkz/entity-manager:latest
sudo kubectl rollout restart deployment/entity-manager -n nekazari
sudo kubectl rollout status deployment/entity-manager -n nekazari
```

- [ ] **Step 6: Verify critical paths**

```bash
# On server:
# A1: create_asset should not return 403 for authenticated users
sudo kubectl exec deploy/entity-manager -n nekazari -- curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:5000/api/assets \
  -H "Content-Type: application/json" \
  -H "X-Tenant: test" \
  -d '{}'

# Check audit log after any governance change (A2 fix)
sudo kubectl exec deploy/api-gateway -n nekazari -- curl -s \
  http://entity-manager-service:5000/api/admin/tenant-config \
  -H "X-Tenant: test"
```

- [ ] **Step 7: Tag post-deploy**

```bash
cd /home/g/Documents/nekazari/nkz
git tag entity-manager-post-A
git push --tags
```

**Gate for Phase B:** All 4 fixes verified in production, tag `entity-manager-post-A` applied.

---

## Phase B — FIWARE Compliance, Security, NDVI Cleanup

**Estimated time:** 2-3 days
**Repos touched:** `nkz/` (entity-manager, host), `nkz-services-risk-worker/` (risk-worker)
**Deploy order:** frontend-host → entity-manager → risk-worker

### Pre-flight: Verify zero NDVI traffic

- [ ] **Step 1: Check gateway logs for NDVI calls**

```bash
ssh g@109.123.252.120
sudo kubectl logs deploy/api-gateway -n nekazari --since=72h 2>/dev/null | grep "/api/ndvi/" | head -5
```

Expected: empty (no lines). If any lines appear, report to user before proceeding.

---

### B.1 — NDVI Removal (entity-manager)

**Working in `nkz/` repo, branch `fix/entity-manager-fiware-and-cleanup`**

- [ ] **Step B.1.1: Create branch**

```bash
cd /home/g/Documents/nekazari/nkz
git checkout main && git pull origin main
git checkout -b fix/entity-manager-fiware-and-cleanup
```

- [ ] **Step B.1.2: Delete NDVI routes and helpers (~850 lines, L1117-1965)**

Delete the following functions from `services/entity-manager/entity_management_api.py`:
- `create_ndvi_job()` (L1119)
- `list_ndvi_jobs()` (L1329)
- `get_ndvi_job(job_id)` (L1385)
- `delete_ndvi_job(job_id)` (L1450)
- `get_ndvi_results()` (L1510) — plus any other NDVI route handlers
- Private helpers: `_serialize_job()`, `_serialize_result()`, `_normalize_polygon()` (if NDVI-only)

```bash
cd /home/g/Documents/nekazari/nkz
# Confirm the exact line range covering all NDVI endpoints
grep -n 'def.*ndvi\|@app.route.*ndvi' services/entity-manager/entity_management_api.py
```

Then delete lines 1117-1965 (adjust range after confirming exact boundaries with grep).

- [ ] **Step B.1.3: Remove NDVI metric counters**

Delete lines referencing NDVI metrics at ~L345 and ~L349 in the metrics block:

```python
# Delete these two lines:
    'entity_manager_ndvi_job_created_total',
    'entity_manager_ndvi_job_failed_total',
```

- [ ] **Step B.1.4: Remove NDVI tables from module_health.py**

Edit `services/entity-manager/module_health.py` line ~20:

```python
# Before:
MONITORED_TABLES = ['ndvi_jobs', 'ndvi_results', 'sensors', ...]
# After (remove ndvi_jobs and ndvi_results):
MONITORED_TABLES = ['sensors', ...]
```

- [ ] **Step B.1.5: Commit NDVI removal**

```bash
git add services/entity-manager/entity_management_api.py services/entity-manager/module_health.py
git commit -m "chore(entity-manager): remove NDVI endpoints and helpers (~850 lines)

Vegetation module (nkz-module-vegetation-health, deployed 2026-05-01) replaces
this functionality. Zero callers remain in host frontend. Tables ndvi_jobs/
ndvi_results frozen as historical data (DROP deferred)."
```

---

### B.1 — NDVI Removal (host frontend)

- [ ] **Step B.1.6: Delete NDVI methods from api.ts**

Edit `apps/host/src/services/api.ts`:
- Delete lines 1195-1320 (7 methods: `createNdviJob`, `getNdviJobs`, `getNdviJob`, `deleteNdviJob`, `deleteNdviResult`, `cleanupNdviJobs`, `getNdviResults`)
- Delete NDVI-specific interceptors at L204-205 and L210-212

```typescript
// Delete these blocks:
        // L204-205
        if (response.config.url?.includes('/api/ndvi/')) {
          logger.debug(`[API] NDVI request successful: ...`);
        }
        
        // L210-212
        if (error.config?.url?.includes('/api/ndvi/')) {
          logger.error(`[API] NDVI request error: ...`, {...});
        }
```

- [ ] **Step B.1.7: Delete NDVI types from types/index.ts**

Edit `apps/host/src/types/index.ts`:
- Delete `NDVIJob` interface (L340) if orphaned (check: `grep -r "NDVIJob" apps/host/src/` returns 0 results outside `api.ts`)
- Delete `NDVIResult` interface (L365) if orphaned
- Remove `NDVIJob, NDVIResult` from the import at L14-15 in `api.ts`

- [ ] **Step B.1.8: Verify no remaining NDVI references in host**

```bash
cd /home/g/Documents/nekazari/nkz
grep -r "ndvi\|Ndvi\|NDVI" apps/host/src/ --ignore-case | grep -v node_modules | grep -v '.git'
```

Expected: empty (or only references to `ndviEnabled` parcel attr and titiler — which are intentionally kept)

- [ ] **Step B.1.9: Commit host NDVI removal**

```bash
cd /home/g/Documents/nekazari/nkz
git add apps/host/src/services/api.ts apps/host/src/types/index.ts
git commit -m "chore(host): remove NDVI API methods and types (~270 lines)

All 7 NDVI client methods, 2 interceptors, and 2 TypeScript interfaces removed.
Vegetation module replaces this functionality. Zero callers remain."
```

---

### B.1 — NDVI Removal (risk-worker)

**Working in risk-worker repo**

- [ ] **Step B.1.10: Locate risk-worker repo**

```bash
ls /home/g/Documents/nekazari/nkz-services-risk-worker/services/risk-worker/risk_processor.py
```

- [ ] **Step B.1.11: Delete `_get_ndvi_data()` method**

In `risk_processor.py`, delete lines 305-339 (the entire `_get_ndvi_data` method):

```python
# Delete:
    def _get_ndvi_data(
            ...
        """Get latest NDVI data for tenant/parcel"""
        ...
            logger.error(f"Failed to get NDVI data: {e}")
```

- [ ] **Step B.1.12: Delete NDVI call site**

Delete lines 483-496 (or wherever `"ndvi" in required_sources` block appears):

```python
# Delete:
        # Get NDVI data if needed
        if "ndvi" in required_sources:
            ...
            ndvi_data = self._get_ndvi_data(tenant_id, parcel_id)
            if ndvi_data:
                data_sources["ndvi"] = ndvi_data
```

- [ ] **Step B.1.13: Remove "ndvi" from any `required_sources` default lists**

```bash
grep -n "required_sources" services/risk-worker/risk_processor.py
```

If `"ndvi"` appears in a default list, remove it.

- [ ] **Step B.1.14: Commit risk-worker NDVI removal**

```bash
cd /home/g/Documents/nekazari/nkz-services-risk-worker
git checkout main && git pull origin main
git checkout -b fix/risk-worker-remove-ndvi-read
git add services/risk-worker/risk_processor.py
git commit -m "chore(risk-worker): remove NDVI data read (~40 lines)

Vegetation module does not write to ndvi_results table. Risk-worker
replacement is pending; this read is dead code."
```

---

### B.2 — Sensor Write Order (Orion-first)

**Back in `nkz/` repo, branch `fix/entity-manager-fiware-and-cleanup`**

- [ ] **Step B.2.1: Swap write order in `register_sensor()`**

Current flow (L2380-2545): INSERT into Postgres → commit → try Orion-LD create (best-effort, errors logged but ignored)

New flow: create Orion-LD entity → on success, INSERT into Postgres → on Postgres failure, best-effort delete Orion entity + log inconsistency.

Replace the sensor registration logic. The key change: move the Orion-LD creation BEFORE the Postgres INSERT, and make Postgres conditional on Orion success.

```python
# In register_sensor(), replace from the INSERT comment to the Orion creation:

            # ── NEW: Orion-LD FIRST (source of truth) ──
            orion_entity_created = False
            orion_entity_id = f"urn:ngsi-ld:{sdm_entity_type}:{tenant_id}:{external_id}"
            
            orion_entity = {
                'id': orion_entity_id,
                'type': sdm_entity_type,
                'name': {'type': 'Property', 'value': name},
                'location': {
                    'type': 'GeoProperty',
                    'value': {'type': 'Point', 'coordinates': [lon, lat]}
                },
                'externalId': {'type': 'Property', 'value': external_id},
                'sensorType': {'type': 'Property', 'value': profile_code}
            }
            if metadata:
                orion_entity['metadata'] = {'type': 'Property', 'value': metadata}
            if data.get('is_under_canopy'):
                orion_entity['isUnderCanopy'] = {'type': 'Property', 'value': True}
            if data.get('station_id'):
                orion_entity['stationId'] = {'type': 'Property', 'value': data['station_id']}
            
            orion_headers = {
                'Content-Type': 'application/ld+json',
                'Fiware-Service': tenant_id,
                'Fiware-ServicePath': '/'
            }
            orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
            
            try:
                response = requests.post(orion_url, json=orion_entity, headers=orion_headers, timeout=10)
                if response.status_code in [200, 201, 409]:
                    orion_entity_created = True
                else:
                    logger.error(f"Orion-LD entity creation failed: {response.status_code} - {response.text}")
                    return jsonify({
                        'error': f'Failed to create entity in Orion-LD: {response.status_code}'
                    }), 502
            except Exception as orion_error:
                logger.error(f"Error creating Orion-LD entity: {orion_error}")
                return jsonify({'error': 'Orion-LD unavailable'}), 502
            
            # ── THEN: Postgres INSERT (cache + non-NGSI metadata) ──
            try:
                cur.execute("""
                    INSERT INTO sensors (
                        tenant_id, external_id, profile_id, name,
                        installation_location, is_under_canopy, metadata
                    ) VALUES (
                        %s, %s, %s, %s,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                        %s, %s::jsonb
                    ) RETURNING id, external_id, name, created_at
                """, (tenant_id, external_id, profile_id, name,
                      lon, lat, data.get('is_under_canopy', False), metadata_json))
                sensor_row = cur.fetchone()
                conn.commit()
            except Exception as pg_error:
                logger.error(f"Postgres INSERT failed after Orion-LD entity created: {pg_error}")
                # Best-effort cleanup of Orion-LD entity
                try:
                    requests.delete(
                        f"{ORION_URL}/ngsi-ld/v1/entities/{orion_entity_id}",
                        headers=orion_headers, timeout=5
                    )
                except Exception:
                    logger.critical(
                        f"INCONSISTENCY: Orion-LD entity {orion_entity_id} exists "
                        f"but Postgres INSERT failed and cleanup also failed"
                    )
                return jsonify({'error': 'Database error after entity creation'}), 500
```

- [ ] **Step B.2.2: Commit sensor write order fix**

```bash
git add services/entity-manager/entity_management_api.py
git commit -m "fix(entity-manager): sensor registration writes Orion-LD first, then Postgres

FIWARE compliance: Orion-LD is the source of truth. Sensor entity is created
in Orion-LD first; Postgres INSERT follows only on success. On Postgres failure
after Orion success, best-effort Orion deletion prevents orphan entities."
```

---

### B.3 — Security Fixes

- [ ] **Step B.3.1: Delete Vercel Blob endpoint (L5379-5457)**

Delete the entire `/api/upload/authorize` route and its handler function. This endpoint returns `BLOB_READ_WRITE_TOKEN` to any authenticated client — zombie code from before the MinIO migration.

```bash
# Delete lines 5376-5460 (from "# Vercel Blob Upload Authorization" comment through the end of the route)
```

- [ ] **Step B.3.2: Replace kubectl subprocess with env var**

Replace lines 3535-3548:

```python
# Before:
    # Try to get from Kubernetes secret (if running in K8s)
    try:
        import subprocess
        result = subprocess.run(
            ['kubectl', 'get', 'secret', 'aemet-secret', '-n', 'nekazari', '-o', 'jsonpath={.data.api-key}'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout:
            import base64
            return base64.b64decode(result.stdout).decode('utf-8')
    except Exception:
        pass
    return None

# After:
    return os.getenv('AEMET_API_KEY')
```

- [ ] **Step B.3.3: Add AEMET_API_KEY to deployment manifest**

In `nkz/k8s/core/services/entity-manager-deployment.yaml`, add the env var referencing the existing secret:

```yaml
        - name: AEMET_API_KEY
          valueFrom:
            secretKeyRef:
              name: aemet-secret
              key: api-key
```

- [ ] **Step B.3.4: Add @require_auth to unprotected entity-type routes**

Edit `services/entity-manager/entity_management_api.py`:

```python
# L785 — add decorator
@app.route('/entity-types/<category>/<type_name>', methods=['POST'])
@require_auth          # <-- ADD THIS
def create_entity_type(category, type_name):

# L824 — add decorator
@app.route('/entity-types/<category>/<type_name>', methods=['DELETE'])
@require_auth          # <-- ADD THIS
def delete_entity_type(category, type_name):
```

- [ ] **Step B.3.5: Extract `_get_user_roles()` helper**

At the top of `entity_management_api.py` (after imports, before route definitions), add:

```python
def _get_user_roles():
    """Return user roles from Flask g context, or empty list if not set."""
    return getattr(g, 'roles', None) or []
```

Replace all 15+ duplication sites. The pattern to find:
```bash
grep -n "user_roles\s*=\|g\.get.*roles\|getattr.*roles\|g\.roles" services/entity-manager/entity_management_api.py
```

Each instance of `user_roles = getattr(g, 'roles', None) or []` or `g.get('roles', [])` becomes:
```python
        user_roles = _get_user_roles()
```

- [ ] **Step B.3.6: Commit security fixes**

```bash
git add services/entity-manager/entity_management_api.py
git commit -m "fix(entity-manager): 4 security fixes (B.3)

- Delete Vercel Blob endpoint (leaked BLOB_READ_WRITE_TOKEN)
- Replace kubectl subprocess with AEMET_API_KEY env var
- Add @require_auth to POST/DELETE /entity-types/<category>/<type_name>
- Extract _get_user_roles() helper, deduplicate 15+ usages"
```

---

### B.4 — NGSI-LD Compliance

- [ ] **Step B.4.1: Inject @context in POST /instances/<entity_type> body**

At line ~2090, the route sends `Content-Type: application/ld+json` but doesn't embed `@context` in the body. Orion-LD requires `@context` in the body when using `application/ld+json`.

```python
# Before:
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
        headers = {
            'Content-Type': 'application/ld+json'
        }
        headers = inject_fiware_headers(headers, g.tenant)
        
        response = requests.post(orion_url, json=entity_data, headers=headers)

# After:
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
        headers = {
            'Content-Type': 'application/ld+json'
        }
        headers = inject_fiware_headers(headers, g.tenant)
        
        # Inject @context required for application/ld+json
        if '@context' not in entity_data:
            entity_data['@context'] = [
                "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld"
            ]
        
        response = requests.post(orion_url, json=entity_data, headers=headers)
```

- [ ] **Step B.4.2: Same fix for PATCH /instances/<entity_type>/<entity_id>**

At line ~2152, same pattern — inject `@context` before the PATCH:

```python
# Before:
        response = requests.patch(orion_url, json=data, headers=headers)

# After:
        if '@context' not in data:
            data['@context'] = [
                "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld"
            ]
        response = requests.patch(orion_url, json=data, headers=headers)
```

- [ ] **Step B.4.3: Add Link header for application/json GET at L647**

The function `_count_all_entities` uses `Accept: application/json` without a `Link` header (required by NGSI-LD spec for non-ld+json requests). The `inject_fiware_headers` helper should add it — verify:

```bash
grep -n "def inject_fiware_headers" services/entity-manager/*.py services/common/*.py
```

If `inject_fiware_headers` already handles the `Link` header for `application/json`, no change needed. If not, add inside `inject_fiware_headers`:

```python
def inject_fiware_headers(headers, tenant=None, service_path='/'):
    headers['Fiware-Service'] = tenant or 'default'
    headers['Fiware-ServicePath'] = service_path or '/'
    if headers.get('Accept') == 'application/json' or headers.get('Content-Type') == 'application/json':
        if 'Link' not in headers:
            headers['Link'] = f'<{CONTEXT_URL}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'
    return headers
```

- [ ] **Step B.4.4: Commit NGSI-LD compliance fixes**

```bash
git add services/entity-manager/entity_management_api.py
git commit -m "fix(entity-manager): NGSI-LD compliance — inject @context in ld+json bodies

- POST /instances/<type>: inject @context before sending to Orion-LD
- PATCH /instances/<type>/<id>: same fix
- Ensure Link header present for application/json requests via inject_fiware_headers"
```

---

### B.5 — Deploy Phase B

- [ ] **Step B.5.1: Tag pre-deploy**

```bash
cd /home/g/Documents/nekazari/nkz
git tag entity-manager-pre-B
git push --tags origin fix/entity-manager-fiware-and-cleanup
```

- [ ] **Step B.5.2: Open entity-manager PR**

```bash
cd /home/g/Documents/nekazari/nkz
gh pr create --title "fix(entity-manager): FIWARE compliance, security, NDVI removal (Phase B)" \
  --body "Phase B of entity-manager overhaul. NDVI removal, sensor Orion-first, 4 security fixes, NGSI-LD compliance." \
  --base main --head fix/entity-manager-fiware-and-cleanup
```

- [ ] **Step B.5.3: Open host PR**

```bash
# Commit host NDVI removal is already in the entity-manager branch.
# If host changes are in a separate branch, push and create PR:
git push origin fix/entity-manager-fiware-and-cleanup
# CI auto-builds host on push to main; PR merge triggers the deploy
```

- [ ] **Step B.5.4: Open risk-worker PR**

```bash
cd /home/g/Documents/nekazari/nkz-services-risk-worker
git push origin fix/risk-worker-remove-ndvi-read
gh pr create --title "chore(risk-worker): remove NDVI data read" \
  --body "Removes dead NDVI read path. Vegetation module does not write to ndvi_results." \
  --base main --head fix/risk-worker-remove-ndvi-read
```

- [ ] **Step B.5.5: Deploy in order: frontend-host → entity-manager → risk-worker**

```bash
ssh g@109.123.252.120

# 1. Host (CI auto-deploys on merge to main)
# 2. Entity-manager
cd /home/g/Documents/nekazari/nkz && git pull origin main
sudo docker build --network=host --no-cache -t ghcr.io/nkz-os/nkz/entity-manager:latest -f services/entity-manager/Dockerfile .
sudo docker push ghcr.io/nkz-os/nkz/entity-manager:latest
sudo kubectl rollout restart deployment/entity-manager -n nekazari
sudo kubectl rollout status deployment/entity-manager -n nekazari

# 3. Risk-worker
cd /home/g/Documents/nekazari/nkz-services-risk-worker && git pull origin main
sudo docker build --network=host --no-cache -t ghcr.io/nkz-os/nkz/risk-worker:latest -f services/risk-worker/Dockerfile .
sudo docker push ghcr.io/nkz-os/nkz/risk-worker:latest
sudo kubectl rollout restart deployment/risk-worker -n nekazari
sudo kubectl rollout status deployment/risk-worker -n nekazari
```

- [ ] **Step B.5.6: Verify NDVI endpoints gone after 24h**

```bash
ssh g@109.123.252.120
sudo kubectl logs deploy/api-gateway -n nekazari --since=24h | grep "/api/ndvi/" | wc -l
```

Expected: 0

- [ ] **Step B.5.7: Tag post-deploy**

```bash
cd /home/g/Documents/nekazari/nkz && git tag entity-manager-post-B && git push --tags
cd /home/g/Documents/nekazari/nkz && git tag host-post-B && git push --tags
cd /home/g/Documents/nekazari/nkz-services-risk-worker && git tag risk-worker-post-B && git push --tags
```

---

## Phase C — Blueprint Refactor

**Estimated time:** 3-4 days
**Branch:** `refactor/entity-manager-blueprints`
**Prerequisite:** Smoke tests merged via `test/entity-manager-routes-smoke` before C starts.

### Pre-C: Smoke Tests

- [ ] **Step C.0.1: Create smoke test branch**

```bash
cd /home/g/Documents/nekazari/nkz
git checkout main && git pull origin main
git checkout -b test/entity-manager-routes-smoke
```

- [ ] **Step C.0.2: Write smoke test file structure**

Create `services/entity-manager/tests/test_routes_smoke.py` with module-level mocks (reusing pattern from `test_quota_enforcement.py`) and test classes grouped by domain.

The smoke test covers 79 routes (86 original - 7 NDVI removed in B). Each test:
1. Auth gate: request without cookie → 401
2. Happy path: mocked auth + mocked dependencies → expected status + JSON shape

```python
"""Smoke tests for all entity-manager routes. Guards the Phase C Blueprint refactor."""
import json
import sys
from unittest.mock import MagicMock, patch, ANY

# ── Module-level mocks (before importing entity_management_api) ──
_common_mock = MagicMock()

def _require_auth(f=None, **kwargs):
    if f is not None:
        return f
    return lambda g: g

_common_mock.require_auth = _require_auth
_common_mock.inject_fiware_headers = lambda h, t=None, **kw: h
sys.modules['common'] = _common_mock
sys.modules['common.auth_middleware'] = _common_mock
sys.modules['parcel_sync'] = MagicMock()
sys.modules['module_metrics'] = MagicMock()

import pytest
from entity_management_api import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def mock_g_context(monkeypatch):
    """Simulate g.tenant, g.roles, g.user, g.email for all tests."""
    import flask
    monkeypatch.setattr(flask, 'g', MagicMock())
    flask.g.tenant = 'test-tenant'
    flask.g.roles = ['PlatformAdmin']
    flask.g.user = 'testuser'
    flask.g.email = 'test@example.com'


# ── Weather routes ──

class TestWeatherRoutes:
    def test_forecast_requires_auth(self, client):
        """GET /api/weather/parcels/<id>/forecast without auth → 401"""
        r = client.get('/api/weather/parcels/test-parcel/forecast')
        assert r.status_code == 401

    @patch('entity_management_api.requests.get')
    def test_forecast_ok(self, mock_get, client):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {'forecast': []}
        r = client.get('/api/weather/parcels/test-parcel/forecast')
        assert r.status_code == 200
        assert 'forecast' in r.get_json()


# ── Admin routes ──

class TestAdminRoutes:
    def test_tenant_config_requires_auth(self, client):
        r = client.get('/api/admin/tenant-config')
        assert r.status_code == 401

    def test_tenant_limits_requires_auth(self, client):
        r = client.patch('/api/admin/tenant-limits', json={})
        assert r.status_code == 401

    def test_terms_requires_auth(self, client):
        r = client.post('/api/admin/terms/es', json={'content': 'test'})
        assert r.status_code == 401


# ── Assets routes ──

class TestAssetsRoutes:
    def test_create_asset_requires_auth(self, client):
        r = client.post('/api/assets', json={})
        assert r.status_code == 401

    def test_list_assets_requires_auth(self, client):
        r = client.get('/api/assets')
        assert r.status_code == 401


# ── Entity instances routes (NGSI-LD) ──

class TestEntityRoutes:
    def test_create_instance_requires_auth(self, client):
        r = client.post('/instances/AgriParcel', json={})
        assert r.status_code == 401

    def test_list_instances_requires_auth(self, client):
        r = client.get('/instances/AgriParcel')
        assert r.status_code == 401


# ── Sensor routes ──

class TestSensorRoutes:
    def test_register_sensor_requires_auth(self, client):
        r = client.post('/api/sensors/register', json={})
        assert r.status_code == 401

    def test_list_sensors_requires_auth(self, client):
        r = client.get('/api/sensors')
        assert r.status_code == 401


# ── Module routes ──

class TestModuleRoutes:
    def test_list_modules_requires_auth(self, client):
        r = client.get('/api/modules')
        assert r.status_code == 401

    def test_install_module_requires_auth(self, client):
        r = client.post('/api/modules/test-module/install', json={})
        assert r.status_code == 401


# ── Health check (exempt from auth) ──

class TestHealthRoute:
    def test_health_ok(self, client):
        r = client.get('/health')
        assert r.status_code == 200
        data = r.get_json()
        assert data['status'] == 'ok'
```

> **Note:** The above is a representative sample. The full smoke test covers all 79 routes with the 2-assertion pattern. The complete file (~200 lines) should be written covering every route listed in `grep -n "@app\.route\|@.*\.route" entity_management_api.py` excluding the 7 NDVI routes removed in Phase B.

- [ ] **Step C.0.3: Run smoke tests before refactor**

```bash
cd /home/g/Documents/nekazari/nkz/services/entity-manager
python -m pytest tests/test_routes_smoke.py -v
```

Expected: all tests PASS

- [ ] **Step C.0.4: Commit smoke tests**

```bash
git add services/entity-manager/tests/test_routes_smoke.py
git commit -m "test(entity-manager): add route smoke tests for all 79 routes

2 assertions per route: auth gate (401) and happy path (200/201/204 + JSON shape).
Grouped by domain to match upcoming Blueprint structure. Guards Phase C refactor."
```

- [ ] **Step C.0.5: Merge smoke test PR before C starts**

```bash
git push origin test/entity-manager-routes-smoke
gh pr create --title "test(entity-manager): route smoke tests for Blueprint refactor guard" \
  --body "79 routes, 2 assertions each. Safety net for Phase C decomposition." \
  --base main --head test/entity-manager-routes-smoke
gh pr merge test/entity-manager-routes-smoke --merge --delete-branch
git checkout main && git pull origin main
```

---

### C.1 — Create refactor branch + scaffold structure

- [ ] **Step C.1.1: Create branch**

```bash
cd /home/g/Documents/nekazari/nkz
git checkout main && git pull origin main
git checkout -b refactor/entity-manager-blueprints
```

- [ ] **Step C.1.2: Create directory structure**

```bash
mkdir -p services/entity-manager/blueprints
mkdir -p services/entity-manager/helpers
touch services/entity-manager/blueprints/__init__.py
touch services/entity-manager/helpers/__init__.py
```

- [ ] **Step C.1.3: Commit scaffold**

```bash
git add services/entity-manager/blueprints/ services/entity-manager/helpers/
git commit -m "refactor(entity-manager): scaffold blueprints/ and helpers/ directories"
```

---

### C.2 — Extract Blueprints (order: weather, admin, assets, entities, sync, modules, sensors)

**Strict rules during extraction:**
- URLs unchanged
- Function signatures unchanged
- No logic changes — bugs found are tagged `# TODO(post-refactor):` and left in place
- Private helpers travel with their blueprint
- Shared helpers (≥2 consumers) move to `helpers/`
- After each extraction: run smoke tests, commit

#### C.2.1: Extract `weather` blueprint (B-pattern, 1 commit)

- [ ] **Step 1: Create `blueprints/weather.py`**

Move all `/api/weather/*` routes and their private helpers from `entity_management_api.py` into `blueprints/weather.py`. Template:

```python
"""Weather routes — forecasts, observations, and agronomic weather data."""
from flask import Blueprint, request, jsonify, g
from common.auth_middleware import require_auth
from common import inject_fiware_headers

weather_bp = Blueprint('weather', __name__)

# ... all weather routes copied verbatim, replacing @app.route with @weather_bp.route ...
```

- [ ] **Step 2: Register blueprint in main**

Add to `entity_management_api.py` near the top (after app creation, before `if __name__`):

```python
from blueprints.weather import weather_bp
app.register_blueprint(weather_bp)
```

- [ ] **Step 3: Delete weather routes from main file**

- [ ] **Step 4: Run smoke tests**

```bash
cd /home/g/Documents/nekazari/nkz/services/entity-manager
python -m pytest tests/test_routes_smoke.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add services/entity-manager/blueprints/weather.py services/entity-manager/entity_management_api.py
git commit -m "refactor(entity-manager): extract weather routes to blueprints/weather.py"
```

#### C.2.2: Extract `admin` blueprint (B-pattern, 1 commit)

Same pattern as C.2.1 for `/api/admin/*` routes (tenant config, governance, terms, settings, usage).

- [ ] Create `blueprints/admin.py`, register, delete from main, run smoke, commit.

#### C.2.3: Extract `assets` blueprint (B-pattern, 1 commit)

Same pattern for `/api/assets/*`, `/api/upload/*` (MinIO upload), and related helpers.

- [ ] Create `blueprints/assets.py`, register, delete from main, run smoke, commit.

#### C.2.4: Extract `entities` blueprint (B-pattern, 1 commit)

Same pattern for `/instances/*`, `/api/entities/*`, `/api/robots/*`.

- [ ] Create `blueprints/entities.py`, register, delete from main, run smoke, commit.

#### C.2.5: Extract `sync` blueprint (B-pattern, 1 commit)

Same pattern for `/api/core/sync/vectorial` (WatermelonDB sync).

- [ ] Create `blueprints/sync.py`, register, delete from main, run smoke, commit.

#### C.2.6: Extract `modules` blueprint (C-pattern, 2 commits)

Split into 2 commits because module helpers are referenced from other domains.

- [ ] **Commit 1: Move routes only** — copy routes to `blueprints/modules.py`, register, delete from main. Keep shared helpers in main for now.

- [ ] **Commit 2: Promote shared helpers** — identify helpers used by ≥2 blueprints (e.g., module gating, marketplace deploy logic), move to `helpers/`, update imports.

#### C.2.7: Extract `sensors` blueprint (C-pattern, 2 commits)

Same 2-commit split: route-move first, then helper-promote for cross-domain helpers.

---

### C.3 — Final Cleanup

- [ ] **Step C.3.1: Promote shared helpers to `helpers/`**

Create helper modules for items used across ≥2 blueprints:

```bash
# helpers/auth_helpers.py — _get_user_roles(), role-check utilities
# helpers/orion_client.py — NGSI-LD GET/POST/PATCH wrappers
# helpers/serialization.py — entity→dict converters shared by ≥2 blueprints
```

- [ ] **Step C.3.2: Clean `entity_management_api.py` to ~250 lines**

Main file should contain only:
- App creation, CORS, metrics setup
- `/health` and `/version` routes
- Blueprint registration (7 lines)
- Shared constants (`ORION_URL`, `CONTEXT_URL`, DB env vars)
- `if __name__ == '__main__'`

- [ ] **Step C.3.3: Run full test suite**

```bash
cd /home/g/Documents/nekazari/nkz/services/entity-manager
python -m pytest tests/ -v
```

Expected: all tests PASS (smoke + quota_enforcement + module_gating)

- [ ] **Step C.3.4: Verify `entity_management_api.py` line count**

```bash
wc -l services/entity-manager/entity_management_api.py
```

Expected: ~250 lines

- [ ] **Step C.3.5: Commit final cleanup**

```bash
git add -A services/entity-manager/
git commit -m "refactor(entity-manager): promote shared helpers, finalize Blueprint structure

entity_management_api.py now ~250 lines (app setup, health, blueprint registration).
7 domain blueprints in blueprints/, shared helpers in helpers/."
```

---

### C.4 — Deploy Phase C

- [ ] **Step C.4.1: Tag pre-deploy**

```bash
cd /home/g/Documents/nekazari/nkz
git tag entity-manager-pre-C
git push --tags origin refactor/entity-manager-blueprints
```

- [ ] **Step C.4.2: Open PR**

```bash
gh pr create --title "refactor(entity-manager): decompose monolith into Blueprints (Phase C)" \
  --body "Extracts 7 domain Blueprints from entity_management_api.py (8633→~250 lines). 79 smoke tests guard the refactor. No behavioral changes." \
  --base main --head refactor/entity-manager-blueprints
```

- [ ] **Step C.4.3: Merge + deploy**

```bash
gh pr merge refactor/entity-manager-blueprints --merge --delete-branch
```

```bash
ssh g@109.123.252.120
cd /home/g/Documents/nekazari/nkz && git pull origin main
sudo docker build --network=host --no-cache -t ghcr.io/nkz-os/nkz/entity-manager:latest -f services/entity-manager/Dockerfile .
sudo docker push ghcr.io/nkz-os/nkz/entity-manager:latest
sudo kubectl rollout restart deployment/entity-manager -n nekazari
sudo kubectl rollout status deployment/entity-manager -n nekazari
```

- [ ] **Step C.4.4: Verify**

```bash
# Health check
curl -s https://nkz.robotika.cloud/api/health | jq .

# All 79 smoke tests pass
sudo kubectl exec deploy/entity-manager -n nekazari -- python -m pytest tests/ -v
```

- [ ] **Step C.4.5: Tag post-deploy**

```bash
git tag entity-manager-post-C && git push --tags
```

---

## Rollback Playbook (per phase)

| Scenario | Action |
|---|---|
| Pod CrashLoopBackOff | `sudo kubectl rollout undo deployment/<service> -n nekazari` |
| Image purged by K3s | `git checkout <pre-tag> -- <path>`, rebuild, redeploy |
| Auth broken | Immediate rollback — do NOT wait for diagnosis |
| Specific endpoint 500 | Triage if non-critical; immediate rollback if critical (sensors, sync) |
| Risk-worker loops post-B | `sudo kubectl rollout undo deployment/risk-worker -n nekazari` |

---

## Post-Implementation Documentation

After each phase, update these files:

| Document | Update |
|---|---|
| `PENDING.md` | Close completed entries, add deferred items from out-of-scope list |
| `nkz/.ai/CURRENT_STATE.md` | Note entity-manager state per phase |
| `nkz/CLAUDE.md` | After C: document Blueprint structure (where routes live) |
| `AGENTS.md` | Update routing if structure changes affect task delegation |
| Memory | New entry `entity-manager-overhaul-2026-05-05.md` summarizing changes |

---

## Definition of Done

| Phase | Done when |
|---|---|
| A | PR merged + entity-manager redeployed + `curl` confirms A1 not 403 + audit log writes valid `changed_by` + tag `entity-manager-post-A` |
| B | 3 PRs merged + 3 services redeployed + zero `/api/ndvi/*` traffic over 24h + sensor Orion-first works + tags `*-post-B` |
| C | PR merged + entity-manager redeployed + 79 smoke tests pass + `/health` 200 + `entity_management_api.py` ~250 lines + tag `entity-manager-post-C` |
