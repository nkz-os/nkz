"""Test quota enforcement logic."""

import os
import sys
from functools import wraps
from unittest.mock import MagicMock


# Load real tier_quotas before mocking common (used by plan level tests)
_services_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)
import common.tier_quotas as _tq_module  # noqa: E402

_TQ_PLAN_LEVELS = _tq_module.PLAN_LEVELS
_TQ_LEVEL_TO_TIER = _tq_module.LEVEL_TO_TIER


# Set env vars needed by entity_management_api module-level code
# (matches the setup in test_routes_smoke.py for compatibility when run together)
os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("ASSETS_BUCKET", "test-bucket")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "test")
os.environ.setdefault("MINIO_SECRET_KEY", "test")
os.environ.setdefault("MQTT_HOST", "localhost")
os.environ.setdefault("MQTT_PORT", "1883")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")


# Mock modules that entity_management_api depends on before importing
_common_mock = MagicMock()


# Smart auth mock: returns 401 when no auth cookie/header is present,
# otherwise sets g context with PlatformAdmin role.
def _require_auth(f=None, **kwargs):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kw):
            from flask import request, g, jsonify

            token = request.cookies.get("nkz_token") or request.headers.get(
                "Authorization", ""
            )
            if not token:
                return jsonify({"error": "Unauthorized"}), 401
            g.current_user = {
                "sub": "test-user",
                "tenant_id": "test-tenant",
                "realm_access": {"roles": ["PlatformAdmin"]},
            }
            g.roles = ["PlatformAdmin"]
            g.tenant_id = "test-tenant"
            g.tenant = "test-tenant"
            g.farmer_id = "test-farmer"
            return func(*args, **kw)

        return wrapper

    if f is not None:
        return decorator(f)
    return decorator


_common_mock.require_auth = _require_auth
_common_mock.inject_fiware_headers = lambda h, t=None, **kw: h
sys.modules["common"] = _common_mock
sys.modules["common.auth_middleware"] = _common_mock

# Mock other optional dependencies
sys.modules["parcel_sync"] = MagicMock()
sys.modules["module_metrics"] = MagicMock()
sys.modules["module_upload_service"] = MagicMock()

from helpers import _check_entity_total_limit, _check_parcel_count_limit  # noqa: E402


def test_basic_tenant_blocked_at_total_entities():
    """Basic tier: max_entities_total=2. Having 2 entities → 3rd should be denied."""
    assert not _check_entity_total_limit(current_count=2, max_total=2)
    assert _check_entity_total_limit(current_count=0, max_total=2)


def test_enterprise_unlimited_entities():
    """Enterprise: max_entities_total=None → no limit."""
    assert _check_entity_total_limit(current_count=50, max_total=None)


def test_unknown_count_allowed():
    """If Orion count returns None (failure), creation should be allowed (fail open)."""
    assert _check_entity_total_limit(current_count=None, max_total=2)


def test_negative_max_total_disables_check():
    """A negative max_entities_total should not gate (treated as disabled)."""
    assert _check_entity_total_limit(current_count=100, max_total=-1)


def test_parcel_count_limit():
    """Pro tier: max_parcels=5. 5 parcels → 6th denied."""
    assert not _check_parcel_count_limit(current_count=5, max_parcels=5)
    assert _check_parcel_count_limit(current_count=4, max_parcels=5)


def test_unlimited_parcels():
    """max_parcels=None → no limit."""
    assert _check_parcel_count_limit(current_count=100, max_parcels=None)


def test_canonical_plan_levels_match_spec():
    """PLAN_LEVELS must match the 4-tier spec: basic=0, pro=1, premium=2, enterprise=3."""
    assert _TQ_PLAN_LEVELS["basic"] == 0
    assert _TQ_PLAN_LEVELS["pro"] == 1
    assert _TQ_PLAN_LEVELS["premium"] == 2
    assert _TQ_PLAN_LEVELS["enterprise"] == 3


def test_level_to_tier_roundtrip():
    """Every tier should round-trip through PLAN_LEVELS → LEVEL_TO_TIER."""
    for tier, level in _TQ_PLAN_LEVELS.items():
        assert _TQ_LEVEL_TO_TIER[level] == tier
