# Tier & Quota System Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a 4-tier subscription system (Basic / Pro / Premium / Enterprise) where module access is uniform (except 3 premium-only modules) and tier differentiation is driven by hard quotas (users, parcels, sensors, robots, hectares, entity total).

**Architecture:**
- Single source of truth `public.tenants` columns: `plan_level`, `max_users`, `max_sensors`, `max_robots`, `max_parcels` (new), `max_area_hectares`, `max_entities_total` (new — Basic only).
- `marketplace_modules.required_plan_level` is the only module gating dimension (drop `required_plan_type`, `module_type`, `pricing_tier`).
- Quota enforcement happens server-side at create endpoints; reading happens via `get_limits_for_tenant()`.
- A canonical `TIER_QUOTAS` Python constant in `services/common/tier_quotas.py` defines per-tier defaults, used by webhook, activation, and admin endpoints.

**Tech Stack:** Python 3.11 (Flask), PostgreSQL/TimescaleDB, NGSI-LD (Orion-LD), Keycloak 26, React 18 + Vite + react-i18next.

**Tier matrix (decided by user 2026-05-05):**

| Tier | level | users | sensors | robots | parcels | hectares | entities_total |
|---|---|---|---|---|---|---|---|
| Basic | 0 | 1 | 0 | 0 | 0 | 0.20 | **2** |
| Pro | 1 | 5 | 10 | 2 | 5 | 50.00 | NULL |
| Premium | 2 | 10 | 50 | 5 | 20 | 500.00 | NULL |
| Enterprise | 3 | NULL (∞) | NULL | NULL | NULL | NULL | NULL |

> **Confirmed by user (2026-05-05):**
> - `max_entities_total=2` for Basic counts **all NGSI-LD entities** under the tenant (not just parcels/sensors/robots). Implementation uses `GET /ngsi-ld/v1/entities?count=true&limit=0` against Orion-LD and reads `Fiware-Total-Count` header.
> - Per-type maxes (`max_sensors=0`, `max_robots=0`, `max_parcels=0`) for Basic are intentionally 0; the aggregate cap is the operative limit (any 2 entities of any type).
> - Public registration trial period is **45 days** (canonical across the platform). If the codebase still has 30 in any path, normalize to 45.
> - **Premium-only modules:** `odoo-erp`, `agrienergy`, `n8n-nkz` (`required_plan_level=2`). All other 21 modules: `required_plan_level=0`.

---

## Phase 1 — Schema and seed data

### Task 1: Migration 073 — schema additions and module gating reset

**Files:**
- Create: `nkz/config/timescaledb/migrations/073_tier_quota_overhaul.sql`

- [ ] **Step 1: Write the migration SQL**

```sql
-- =============================================================================
-- Migration 073: Tier & Quota System Overhaul
-- Adds max_parcels, max_entities_total to tenants.
-- Resets marketplace_modules gating to 4-tier model with single source of truth.
-- =============================================================================

BEGIN;

-- 1. Tenant quota columns
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS max_parcels INTEGER,
    ADD COLUMN IF NOT EXISTS max_entities_total INTEGER;

COMMENT ON COLUMN tenants.max_parcels IS 'Max AgriParcel count. NULL = unlimited.';
COMMENT ON COLUMN tenants.max_entities_total IS 'Aggregate cap for parcels+sensors+robots (Basic tier). NULL = no aggregate cap.';

-- 2. Tier defaults backfill — align existing tenants to canonical quotas.
--    Mapping: 0=basic, 1=pro, 2=premium, 3=enterprise.
UPDATE tenants SET
    max_users           = 1,
    max_sensors         = 0,
    max_robots          = 0,
    max_parcels         = 0,
    max_area_hectares   = 0.20,
    max_entities_total  = 2
WHERE plan_level = 0;

UPDATE tenants SET
    max_users           = 10,
    max_sensors         = 10,
    max_robots          = 2,
    max_parcels         = 5,
    max_area_hectares   = 50.00,
    max_entities_total  = NULL
WHERE plan_level = 1;

UPDATE tenants SET
    max_users           = 50,
    max_sensors         = 50,
    max_robots          = 5,
    max_parcels         = 20,
    max_area_hectares   = 500.00,
    max_entities_total  = NULL
WHERE plan_level = 2;

UPDATE tenants SET
    max_users           = NULL,
    max_sensors         = NULL,
    max_robots          = NULL,
    max_parcels         = NULL,
    max_area_hectares   = NULL,
    max_entities_total  = NULL
WHERE plan_level = 3;

-- 3. Module gating — single source of truth: required_plan_level
UPDATE marketplace_modules SET required_plan_level = 0;
UPDATE marketplace_modules SET required_plan_level = 2
    WHERE id IN ('odoo-erp', 'agrienergy', 'n8n-nkz');

-- 4. Drop redundant module columns (data migrated to required_plan_level above)
ALTER TABLE marketplace_modules
    DROP COLUMN IF EXISTS required_plan_type,
    DROP COLUMN IF EXISTS module_type,
    DROP COLUMN IF EXISTS pricing_tier;

-- 5. Activation codes: extend plan enum to support 'pro'
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'activation_plan_type') THEN
        ALTER TYPE activation_plan_type ADD VALUE IF NOT EXISTS 'pro';
    END IF;
END$$;

COMMIT;
```

- [ ] **Step 2: Apply locally and verify schema**

```bash
ssh g@109.123.252.120 "sudo kubectl exec -n nekazari deploy/postgresql -- psql -U postgres -d nekazari -f -" \
    < nkz/config/timescaledb/migrations/073_tier_quota_overhaul.sql

# Verify
ssh g@109.123.252.120 "sudo kubectl exec -n nekazari deploy/postgresql -- psql -U postgres -d nekazari -c \
  \"SELECT tenant_id, plan_level, max_users, max_sensors, max_robots, max_parcels, max_area_hectares, max_entities_total FROM tenants ORDER BY plan_level, tenant_id;\""

ssh g@109.123.252.120 "sudo kubectl exec -n nekazari deploy/postgresql -- psql -U postgres -d nekazari -c \
  \"SELECT id, required_plan_level FROM marketplace_modules ORDER BY required_plan_level, id;\""
```

Expected: 8 tenants with quotas matching their `plan_level`; 24 modules with `required_plan_level=0` except 3 with `required_plan_level=2`.

- [ ] **Step 3: Commit**

```bash
cd nkz && git add config/timescaledb/migrations/073_tier_quota_overhaul.sql
git commit -m "feat(db): tier quota overhaul migration (max_parcels, max_entities_total, single-source module gating)"
```

---

## Phase 2 — Canonical tier constants

### Task 2: `services/common/tier_quotas.py`

**Files:**
- Create: `nkz/services/common/tier_quotas.py`
- Test: `nkz/services/common/tests/test_tier_quotas.py`

- [ ] **Step 1: Write the failing tests**

```python
# nkz/services/common/tests/test_tier_quotas.py
import pytest
from tier_quotas import (
    TIER_QUOTAS, PLAN_LEVELS, plan_level_for, quotas_for_tier,
)

def test_plan_levels_are_canonical():
    assert PLAN_LEVELS == {"basic": 0, "pro": 1, "premium": 2, "enterprise": 3}

def test_basic_tier_quotas():
    q = quotas_for_tier("basic")
    assert q["max_users"] == 1
    assert q["max_sensors"] == 0
    assert q["max_robots"] == 0
    assert q["max_parcels"] == 0
    assert q["max_area_hectares"] == 0.20
    assert q["max_entities_total"] == 2

def test_enterprise_is_unlimited():
    q = quotas_for_tier("enterprise")
    for k in ("max_users", "max_sensors", "max_robots", "max_parcels", "max_area_hectares", "max_entities_total"):
        assert q[k] is None

def test_plan_level_for_known_and_unknown():
    assert plan_level_for("pro") == 1
    assert plan_level_for("PRO") == 1  # case-insensitive
    with pytest.raises(KeyError):
        plan_level_for("vip")

def test_quotas_for_tier_unknown_raises():
    with pytest.raises(KeyError):
        quotas_for_tier("vip")
```

- [ ] **Step 2: Run tests, expect failure**

```bash
cd nkz/services/common && python -m pytest tests/test_tier_quotas.py -v
# Expected: ImportError / ModuleNotFoundError
```

- [ ] **Step 3: Implement `tier_quotas.py`**

```python
# nkz/services/common/tier_quotas.py
"""Canonical tier → plan_level and tier → quota mapping.

Single source of truth for the 4-tier subscription model.
Imported by tenant-webhook, tenant-user-api, and entity-manager.
"""
from decimal import Decimal
from typing import Dict, Any

PLAN_LEVELS: Dict[str, int] = {
    "basic": 0,
    "pro": 1,
    "premium": 2,
    "enterprise": 3,
}

LEVEL_TO_TIER: Dict[int, str] = {v: k for k, v in PLAN_LEVELS.items()}

TIER_QUOTAS: Dict[str, Dict[str, Any]] = {
    "basic": {
        "max_users": 1,
        "max_sensors": 0,
        "max_robots": 0,
        "max_parcels": 0,
        "max_area_hectares": Decimal("0.20"),
        "max_entities_total": 2,
    },
    "pro": {
        "max_users": 10,
        "max_sensors": 10,
        "max_robots": 2,
        "max_parcels": 5,
        "max_area_hectares": Decimal("50.00"),
        "max_entities_total": None,
    },
    "premium": {
        "max_users": 50,
        "max_sensors": 50,
        "max_robots": 5,
        "max_parcels": 20,
        "max_area_hectares": Decimal("500.00"),
        "max_entities_total": None,
    },
    "enterprise": {
        "max_users": None,
        "max_sensors": None,
        "max_robots": None,
        "max_parcels": None,
        "max_area_hectares": None,
        "max_entities_total": None,
    },
}


def plan_level_for(tier: str) -> int:
    """Map tier name → plan_level int. Raises KeyError on unknown tier."""
    key = (tier or "").strip().lower()
    if key not in PLAN_LEVELS:
        raise KeyError(f"Unknown tier: {tier!r}")
    return PLAN_LEVELS[key]


def quotas_for_tier(tier: str) -> Dict[str, Any]:
    """Return quota defaults for a given tier. Raises KeyError on unknown tier."""
    key = (tier or "").strip().lower()
    if key not in TIER_QUOTAS:
        raise KeyError(f"Unknown tier: {tier!r}")
    # Return a copy so callers can't mutate the canonical dict
    return dict(TIER_QUOTAS[key])


def quotas_for_level(level: int) -> Dict[str, Any]:
    """Return quotas by plan_level int."""
    if level not in LEVEL_TO_TIER:
        raise KeyError(f"Unknown plan_level: {level!r}")
    return quotas_for_tier(LEVEL_TO_TIER[level])
```

Casefold case-insensitive: tests above expect `plan_level_for("PRO")` to return 1. The implementation uses `.strip().lower()`.

- [ ] **Step 4: Run tests, expect pass**

```bash
cd nkz/services/common && python -m pytest tests/test_tier_quotas.py -v
# Expected: 5 passed
```

- [ ] **Step 5: Commit**

```bash
cd nkz && git add services/common/tier_quotas.py services/common/tests/test_tier_quotas.py
git commit -m "feat(common): canonical tier_quotas constants and helpers"
```

---

## Phase 3 — Module gating fix (entity-manager)

### Task 3: Replace inline `plan_hierarchy` dict with `tier_quotas` import

**Files:**
- Modify: `nkz/services/entity-manager/entity_management_api.py:6181-6206` (toggle endpoint), `:6491-6513` (can-install endpoint)

- [ ] **Step 1: Write a regression test**

Create `nkz/services/entity-manager/tests/test_module_gating.py`:

```python
from unittest.mock import patch, MagicMock
import pytest

# This test exercises the gating decision in isolation by extracting
# the comparison logic into a helper function.

from entity_manager_gating import can_tenant_install_module


@pytest.mark.parametrize("tenant_level,required_level,expected", [
    (0, 0, True),    # basic → basic OK
    (1, 0, True),    # pro → basic OK (the original bug)
    (1, 1, True),    # pro → pro OK
    (1, 2, False),   # pro → premium denied
    (2, 2, True),    # premium → premium OK
    (2, 3, False),   # premium → enterprise denied
    (3, 3, True),    # enterprise → enterprise OK
])
def test_can_tenant_install_module(tenant_level, required_level, expected):
    assert can_tenant_install_module(tenant_level, required_level) is expected
```

- [ ] **Step 2: Run, expect ImportError**

```bash
cd nkz/services/entity-manager && python -m pytest tests/test_module_gating.py -v
# Expected: ModuleNotFoundError
```

- [ ] **Step 3: Extract `entity_manager_gating.py`**

Create `nkz/services/entity-manager/entity_manager_gating.py`:

```python
"""Module install gating helpers. Imported by entity_management_api.py."""
from typing import Optional


def can_tenant_install_module(tenant_level: Optional[int], required_level: Optional[int]) -> bool:
    """Return True if a tenant at `tenant_level` can install a module that requires `required_level`.

    NULL/None values are treated as 0 (basic) for both sides.
    """
    t = tenant_level if tenant_level is not None else 0
    r = required_level if required_level is not None else 0
    return t >= r
```

- [ ] **Step 4: Tests pass**

```bash
cd nkz/services/entity-manager && python -m pytest tests/test_module_gating.py -v
# Expected: 7 passed
```

- [ ] **Step 5: Replace gating logic in `/toggle` (lines 6162-6206)**

In `entity_management_api.py`, change the validation block inside `toggle_module_installation` from the broken string hierarchy to:

```python
# Replace lines 6162-6206 (the entire `if is_enabled:` … 403 return block) with:
if is_enabled:
    is_platform_admin = 'PlatformAdmin' in user_roles
    if not is_platform_admin:
        # Read tenant plan_level (single source of truth)
        cur.execute("SELECT plan_level FROM tenants WHERE tenant_id = %s", (tenant_id,))
        tenant_row = cur.fetchone()
        tenant_level = (tenant_row or {}).get('plan_level', 0) or 0

        required_level = module.get('required_plan_level') or 0

        from entity_manager_gating import can_tenant_install_module
        if not can_tenant_install_module(tenant_level, required_level):
            cur.close()
            from tier_quotas import LEVEL_TO_TIER
            tenant_tier = LEVEL_TO_TIER.get(tenant_level, 'basic')
            required_tier = LEVEL_TO_TIER.get(required_level, 'basic')
            return jsonify({
                'error': 'Plan insuficiente para instalar este módulo',
                'error_en': 'Insufficient plan to install this module',
                'reason': f'El módulo requiere plan {required_tier}, el tenant tiene plan {tenant_tier}',
                'required_plan': required_tier,
                'current_plan': tenant_tier,
                'action_required': 'upgrade_plan',
            }), 403
```

Also update the SELECT in the `cur.execute` immediately above (originally lines 6145-6150) to drop columns we removed:

```python
cur.execute("""
    SELECT id, name, display_name, required_plan_level, is_active
    FROM marketplace_modules
    WHERE id = %s
""", (module_id,))
```

- [ ] **Step 6: Apply the same simplification to `/can-install` (lines 6414-6527)**

Replace the gating block (lines 6480-6513) with:

```python
is_platform_admin = 'PlatformAdmin' in user_roles
if is_platform_admin:
    return jsonify({'can_install': True, 'reason': 'PlatformAdmin', 'module': dict(module)}), 200

cur.execute("SELECT plan_level FROM tenants WHERE tenant_id = %s", (tenant_id,))
tenant_row = cur.fetchone()
tenant_level = (tenant_row or {}).get('plan_level', 0) or 0
required_level = module.get('required_plan_level') or 0

from entity_manager_gating import can_tenant_install_module
from tier_quotas import LEVEL_TO_TIER

if not can_tenant_install_module(tenant_level, required_level):
    return jsonify({
        'can_install': False,
        'reason': f'Module requires {LEVEL_TO_TIER[required_level]} plan',
        'module': dict(module),
        'tenant_plan': LEVEL_TO_TIER.get(tenant_level, 'basic'),
        'required_plan': LEVEL_TO_TIER[required_level],
        'action_required': 'upgrade_plan',
    }), 200

return jsonify({
    'can_install': True,
    'reason': 'Module can be installed',
    'module': dict(module),
    'tenant_plan': LEVEL_TO_TIER.get(tenant_level, 'basic'),
}), 200
```

Update the SELECT (originally line 6447-6452) to drop removed columns:

```python
cur.execute("""
    SELECT id, name, display_name, required_plan_level, is_active, category
    FROM marketplace_modules
    WHERE id = %s
""", (module_id,))
```

- [ ] **Step 7: Update `/api/modules/marketplace` SELECT (lines 6315-6333) to remove dropped columns**

Replace both query strings with the single column set:

```python
query = """
    SELECT id, name, display_name, description, version, author,
           category, icon_url, is_active, required_roles, metadata,
           required_plan_level, created_at, updated_at
    FROM marketplace_modules
"""
if not is_platform_admin:
    query += " WHERE is_active = true "
query += " ORDER BY display_name"
cur.execute(query)
```

- [ ] **Step 8: Commit**

```bash
cd nkz && git add services/entity-manager/entity_manager_gating.py \
    services/entity-manager/tests/test_module_gating.py \
    services/entity-manager/entity_management_api.py
git commit -m "fix(entity-manager): module gating uses required_plan_level (4-tier), drops broken plan_type dict"
```

---

## Phase 4 — Webhook plan-level alignment

### Task 4: tenant-webhook uses `tier_quotas` instead of inline maps

**Files:**
- Modify: `nkz/services/tenant-webhook/enhanced-tenant-webhook.py:495-496` (legacy `_ensure_tenants_record`)
- Modify: `nkz/services/tenant-webhook/enhanced-tenant-webhook.py:2313-2318` (`_BILLING_PLAN_LEVELS`)
- Modify: `nkz/services/tenant-webhook/enhanced-tenant-webhook.py:1851-1855` (admin code generator quota matrix)
- Modify: `nkz/services/tenant-webhook/enhanced-tenant-webhook.py:4904, 4917` (public register defaults)
- Test: `nkz/services/tenant-webhook/tests/test_billing_plan_levels.py`

- [ ] **Step 1: Add common path to PYTHONPATH for the service**

Confirm by reading `services/tenant-webhook/Dockerfile` that `services/common/` is included in the image. If not, add a COPY directive.

- [ ] **Step 2: Write tests**

```python
# tests/test_billing_plan_levels.py
import pytest
from tier_quotas import PLAN_LEVELS, quotas_for_tier

def test_billing_accepts_all_four_tiers():
    for tier in ("basic", "pro", "premium", "enterprise"):
        assert PLAN_LEVELS[tier] in (0, 1, 2, 3)

def test_pro_and_premium_are_distinct_levels():
    assert PLAN_LEVELS["pro"] != PLAN_LEVELS["premium"]

def test_activation_quotas_for_pro():
    q = quotas_for_tier("pro")
    assert q["max_sensors"] == 10
    assert q["max_robots"] == 2
    assert q["max_parcels"] == 5
```

- [ ] **Step 3: Replace `_BILLING_PLAN_LEVELS` (line 2313)**

```python
# OLD
_BILLING_PLAN_LEVELS = {
    "basic": 0,
    "pro": 1,
    "premium": 1,   # BUG: aliased to pro
    "enterprise": 2,
}

# NEW — import from common
from tier_quotas import PLAN_LEVELS as _BILLING_PLAN_LEVELS
```

- [ ] **Step 4: Replace inline map at `_ensure_tenants_record` (line 495)**

```python
# OLD
plan_hierarchy = {"basic": 0, "pro": 1, "premium": 2, "enterprise": 3}
plan_level = plan_hierarchy.get(plan.lower(), 0)

# NEW
from tier_quotas import plan_level_for
try:
    plan_level = plan_level_for(plan)
except KeyError:
    plan_level = 0
```

- [ ] **Step 5: Replace admin code generator matrix (lines 1851-1855)**

Find the dict that maps plan → quotas (currently `basic`, `premium`, `enterprise`) and replace with:

```python
from tier_quotas import quotas_for_tier, PLAN_LEVELS

if plan_lower not in PLAN_LEVELS:
    return jsonify({"error": f"Invalid plan: {plan}. Allowed: {list(PLAN_LEVELS)}"}), 400

q = quotas_for_tier(plan_lower)
max_users = q["max_users"]
max_sensors = q["max_sensors"]
max_robots = q["max_robots"]
# IMPORTANT: store NULL where unlimited; activation_codes table accepts NULL
```

Confirm `activation_codes` table allows NULL on those columns; if not, add another migration step.

- [ ] **Step 6: Public register defaults (line 4904, 4917)**

```python
# Line 4904: keep default plan = "pro" (30-day trial). User confirmed Basic is invitation-only.
plan = data.get("plan", "pro").lower()
if plan not in ("pro", "premium", "enterprise"):
    return jsonify({"error": "Public registration restricted to pro/premium/enterprise. Basic requires NEK invitation code."}), 400

# Line 4917: replace hardcoded limits with tier_quotas lookup
from tier_quotas import quotas_for_tier
q = quotas_for_tier(plan)
max_users = q["max_users"]
max_robots = q["max_robots"]
max_sensors = q["max_sensors"]
```

Apply the rest of the fields (`max_parcels`, `max_area_hectares`, `max_entities_total`) in the INSERT/UPDATE on `tenants`.

- [ ] **Step 7: Tests pass**

```bash
cd nkz/services/tenant-webhook && python -m pytest tests/test_billing_plan_levels.py -v
```

- [ ] **Step 8: Commit**

```bash
cd nkz && git add services/tenant-webhook/enhanced-tenant-webhook.py \
    services/tenant-webhook/tests/test_billing_plan_levels.py
git commit -m "fix(tenant-webhook): unify plan-level via tier_quotas, basic gated to invite-only"
```

---

## Phase 5 — Quota enforcement gaps

### Task 5: Enforce `max_parcels` count (entity-manager)

**Files:**
- Modify: `nkz/services/entity-manager/entity_management_api.py:2009-2020`
- Test: `nkz/services/entity-manager/tests/test_quota_parcels.py`

- [ ] **Step 1: Write test**

```python
def test_create_parcel_exceeds_count_limit_returns_403(client, mock_orion):
    mock_orion.set_count("AgriParcel", 5)  # tenant pro has max_parcels=5
    response = client.post('/instances/AgriParcel', json={'id': 'AgriParcel:6', 'area': 1.0}, headers=PRO_TENANT_HEADERS)
    assert response.status_code == 403
    assert 'parcel count' in response.get_json()['error'].lower()
```

- [ ] **Step 2: Insert count check above the area check (around line 2009)**

```python
# Add BEFORE the existing area check
if entity_type in PARCEL_ENTITY_TYPES:
    max_parcels = limits.get('maxParcels')
    if max_parcels is not None and int(max_parcels) >= 0:
        parcels_total = 0
        for ptype in PARCEL_ENTITY_TYPES:
            count = _count_entities_by_type(ptype, tenant)
            if count is not None:
                parcels_total += count
        if parcels_total >= int(max_parcels):
            return jsonify({
                'error': 'Parcel count limit exceeded',
                'limit': int(max_parcels),
                'current': parcels_total,
            }), 403
```

- [ ] **Step 3: Verify via integration test**

```bash
pytest tests/test_quota_parcels.py -v
```

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(entity-manager): enforce max_parcels count quota"
```

### Task 6: Enforce `max_entities_total` (Basic tier aggregate)

**Files:**
- Modify: `nkz/services/entity-manager/entity_management_api.py` (same `create_instance` function)

- [ ] **Step 1: Test**

```python
def test_basic_tenant_blocked_at_2_total_entities(client, mock_orion):
    # basic: max_entities_total=2; sensors=0, robots=0, parcels=0 (per-type), aggregate caps the total
    mock_orion.set_count("AgriParcel", 1)
    mock_orion.set_count("AgriSensor", 1)
    response = client.post('/instances/AgriParcel', json={'id': 'AgriParcel:2', 'area': 0.05}, headers=BASIC_TENANT_HEADERS)
    assert response.status_code == 403
    assert 'entities total' in response.get_json()['error'].lower()
```

- [ ] **Step 2: Add aggregate check (run BEFORE per-type checks)**

```python
# Add at top of enforcement block, after limits load
max_entities_total = limits.get('maxEntitiesTotal')
if max_entities_total is not None and entity_type in (ROBOT_ENTITY_TYPES + SENSOR_ENTITY_TYPES + PARCEL_ENTITY_TYPES):
    total = 0
    for type_set in (ROBOT_ENTITY_TYPES, SENSOR_ENTITY_TYPES, PARCEL_ENTITY_TYPES):
        for t in type_set:
            c = _count_entities_by_type(t, tenant)
            if c is not None:
                total += c
    if total >= int(max_entities_total):
        return jsonify({
            'error': 'Entities total limit exceeded',
            'limit': int(max_entities_total),
            'current': total,
            'message': 'Tu plan Basic permite un máximo de N entidades. Actualiza a Pro para aumentar el límite.',
        }), 403
```

- [ ] **Step 3: Update `_get_limits_from_db` and `get_limits_for_tenant` to expose the new fields**

Add `maxParcels` and `maxEntitiesTotal` to whichever query/dict reads the tenants row. Reuse existing camelCase convention (e.g., `maxRobots`, `maxSensors`).

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(entity-manager): enforce max_entities_total aggregate (Basic tier)"
```

### Task 7: Enforce `max_users` (tenant-user-api)

**Files:**
- Modify: `nkz/services/tenant-user-api/tenant_user_api.py:276-297` (start of `create_user`)
- Test: `nkz/services/tenant-user-api/tests/test_quota_users.py`

- [ ] **Step 1: Test**

```python
def test_create_user_blocked_at_max_users(client, mock_keycloak):
    mock_keycloak.set_user_count(10)  # pro tenant max_users=10
    resp = client.post('/api/tenant/users', json=VALID_PAYLOAD, headers=PRO_TENANT_HEADERS)
    assert resp.status_code == 403
    assert 'user limit' in resp.get_json()['error'].lower()
```

- [ ] **Step 2: Insert quota check before Keycloak create (immediately after line 290)**

```python
# Quota check
with get_db_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT max_users FROM tenants WHERE tenant_id = %s", (tenant,))
    row = cur.fetchone()
    max_users = (row or [None])[0]

if max_users is not None:
    current_count = keycloak.count_users_for_tenant(tenant)
    if current_count >= int(max_users):
        return jsonify({
            'error': 'User limit exceeded',
            'limit': int(max_users),
            'current': current_count,
            'message': 'Has alcanzado el máximo de usuarios para tu plan. Actualiza el plan para añadir más.',
        }), 403
```

If `keycloak.count_users_for_tenant()` does not exist yet, add it: a Keycloak admin search by `tenant_id` attribute.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(tenant-user-api): enforce max_users quota at user creation"
```

---

## Phase 6 — Frontend

### Task 8: `LimitsManagement.tsx` add new fields

**Files:**
- Modify: `nkz/apps/host/src/components/LimitsManagement.tsx`

- [ ] **Step 1: Add `maxParcels` and `maxEntitiesTotal` to the editor**

Add two `Input` fields, wire them to the same PATCH `/api/admin/tenant-limits` endpoint. Make `maxEntitiesTotal` only editable when `planType === 'basic'`; otherwise show "—".

- [ ] **Step 2: Display tier name as 4-tier label, not 3**

Where `planType` is rendered, use a label map:

```typescript
const TIER_LABEL: Record<string,string> = {
  basic: t('settings.tier.basic'),
  pro: t('settings.tier.pro'),
  premium: t('settings.tier.premium'),
  enterprise: t('settings.tier.enterprise'),
};
```

- [ ] **Step 3: Commit**

### Task 9: i18n keys for new tier names + quota copy

**Files:**
- Modify: `nkz/apps/host/public/locales/es/common.json`, `en/common.json`

- [ ] Add keys: `settings.tier.basic`, `…pro`, `…premium`, `…enterprise`; `quota.parcel_count_exceeded`, `quota.entities_total_exceeded`, `quota.user_limit_exceeded`. Provide `es` and `en` values; copy English to other locales as fallback to keep key parity.

- [ ] **Commit**

### Task 10: Public registration UI — Basic guarded

**Files:**
- Read: `nkz/apps/host/src/pages/Activation.tsx`

- [ ] **Step 1: Confirm the no-code path does not allow `plan="basic"`** (today defaults to `pro` server-side; verify there is no UI selector that sends `basic`).

- [ ] **Step 2: If a tier picker exists, hide `basic` from the public flow**. Basic only appears when the user has supplied a NEK code that resolves to plan=basic.

---

## Phase 7 — Production rollout & verification

### Task 11: Apply migration 073 to production

- [ ] **Step 1: Backup**

```bash
ssh g@109.123.252.120 "sudo kubectl exec -n nekazari deploy/postgresql -- pg_dump -U postgres -d nekazari -t public.tenants -t public.marketplace_modules" > tier-overhaul-pre-073.sql
```

- [ ] **Step 2: Apply**

```bash
ssh g@109.123.252.120 "sudo kubectl exec -i -n nekazari deploy/postgresql -- psql -U postgres -d nekazari" \
    < nkz/config/timescaledb/migrations/073_tier_quota_overhaul.sql
```

- [ ] **Step 3: Spot-check**

```bash
ssh g@109.123.252.120 "sudo kubectl exec -n nekazari deploy/postgresql -- psql -U postgres -d nekazari -c \
  \"SELECT tenant_id, plan_level, max_users, max_sensors, max_robots, max_parcels, max_area_hectares, max_entities_total FROM tenants ORDER BY plan_level;\""
```

Confirm 8 tenants quotas align with `TIER_QUOTAS`.

### Task 12: Deploy services in order

- [ ] entity-manager (rebuild + rollout) → wait for ready → smoke test `/api/modules/marketplace`.
- [ ] tenant-webhook → smoke test `/health`.
- [ ] tenant-user-api → smoke test `/health`.
- [ ] frontend-host (CI builds on main push automatically).

### Task 13: E2E acceptance tests against production

Run from a Pro test tenant (`asociacinallotarra2`):
- [ ] `POST /api/modules/agrienergy/toggle` → expected **403** (premium-only).
- [ ] `POST /api/modules/zulip/toggle` → expected **200** (universal addon, the original bug is now fixed).
- [ ] Create 6 parcels via `POST /instances/AgriParcel` → 6th returns **403** with `Parcel count limit exceeded`.
- [ ] Create 11 sensors → 11th returns **403** with `Sensor limit exceeded`.

Run from a Basic test tenant (created via NEK code):
- [ ] Create 1 parcel + 1 sensor → both **201**. 3rd entity (any type) → **403** with `Entities total limit exceeded`.

### Task 14: Update documentation & memory

- [ ] Update `nkz/docs/development/PLATFORM_CONVENTIONS.md` with the canonical tier matrix.
- [ ] Update `CLAUDE.md` "Critical Pitfalls" with a new entry pointing to `tier_quotas.py` as single source of truth.
- [ ] Update `PENDING.md` to mark this task done and record the migration number.

---

## Self-Review Checklist (executed before kicking off)

- ✅ Spec coverage: all 4 tiers, 6 quotas, 3 premium-only modules, public registration block.
- ⚠️ ASSUMPTIONS to confirm:
  - `max_users=10` for Pro and `max_users=50` for Premium.
  - `max_users=1` for Basic.
  - `max_entities_total=2` interpretation: aggregate across parcels+sensors+robots only (not other entity types like Vineyard).
- ✅ Type consistency: tier names lowercase strings everywhere; `plan_level` ints 0-3; column names snake_case in SQL, camelCase in JSON.
- ✅ No placeholders.

## Rollback plan

If migration 073 causes issues:

```sql
BEGIN;
ALTER TABLE tenants
    DROP COLUMN IF EXISTS max_parcels,
    DROP COLUMN IF EXISTS max_entities_total;

ALTER TABLE marketplace_modules
    ADD COLUMN required_plan_type VARCHAR(50),
    ADD COLUMN module_type VARCHAR(50),
    ADD COLUMN pricing_tier VARCHAR(50);

-- Restore from tier-overhaul-pre-073.sql backup
COMMIT;
```
