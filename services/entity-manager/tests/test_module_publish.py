"""Tests for /deploy route disambiguation.

Verifies that:
- POST /api/modules/<id>/deploy  hits the versioned handler (expects {version})
- POST /api/modules/<id>/deploy-upload  hits the legacy upload handler (expects {upload_id})
"""

import os
import sys
from functools import wraps
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Module-level mocks — must be set BEFORE importing entity_management_api
# ---------------------------------------------------------------------------

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("ASSETS_BUCKET", "test-bucket")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "test")
os.environ.setdefault("MINIO_SECRET_KEY", "test")
os.environ.setdefault("MQTT_HOST", "localhost")
os.environ.setdefault("MQTT_PORT", "1883")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")

_services_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)

_common_mock = MagicMock()


def _require_auth(f=None, **kwargs):
    """Smart mock: returns 401 when no auth cookie/header is present."""

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
sys.modules["common.ngsi_headers"] = _common_mock
sys.modules["common.config_manager"] = MagicMock()
# parcel_activation.py imports common.tier_quotas at module level; the parent
# "common" stub is a plain MagicMock (not a package), so the submodule must be
# pre-seeded for `from common.tier_quotas import ...` to resolve.
_tier_quotas_mock = MagicMock()
_tier_quotas_mock.LEVEL_TO_TIER = {0: "free"}
_tier_quotas_mock.quotas_for_tier = lambda tier: {"max_parcels": 0}
sys.modules["common.tier_quotas"] = _tier_quotas_mock

import importlib.util

_api_errors_path = os.path.join(_services_dir, "common", "api_errors.py")
_api_spec = importlib.util.spec_from_file_location("common.api_errors", _api_errors_path)
_api_errors_mod = importlib.util.module_from_spec(_api_spec)
assert _api_spec.loader is not None
_api_spec.loader.exec_module(_api_errors_mod)
sys.modules["common.api_errors"] = _api_errors_mod

# Heavy / infrastructure dependencies
sys.modules["db_helper"] = MagicMock()
sys.modules["orion_writer"] = MagicMock()
sys.modules["module_upload_service"] = MagicMock()
sys.modules["parcel_sync"] = MagicMock()
sys.modules["module_metrics"] = MagicMock()

# Mock geo_utils to avoid shapely import error
_geo_utils_mock = MagicMock()
_geo_utils_mock.get_parcel_location.return_value = {"lat": 42.0, "lon": -2.0}
sys.modules["geo_utils"] = _geo_utils_mock

import pytest  # noqa: E402
from entity_management_api import app  # noqa: E402


@pytest.fixture
def client():
    """Test client with Authorization header."""
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_versioned_deploy_is_reachable(client):
    # versioned handler validates {version}; bad version => 400 mentioning "version"
    r = client.post(
        "/api/modules/demo/deploy",
        headers={"Authorization": "Bearer x"},
        json={"version": "ZZZ"},
    )
    assert r.status_code == 400
    assert "version" in r.get_json()["error"].lower()


def test_legacy_upload_deploy_moved(client):
    r = client.post(
        "/api/modules/demo/deploy-upload",
        headers={"Authorization": "Bearer x"},
        json={},
    )
    assert r.status_code == 400
    assert "upload_id" in r.get_json()["error"].lower()


# ---------------------------------------------------------------------------
# Internal publish endpoint tests
# ---------------------------------------------------------------------------

from io import BytesIO  # noqa: E402

_MANIFEST = (
    b'{"id":"demo","version":"1.0.0","hostApiVersion":"1.0",'
    b'"name":"demo","display_name":"Demo"}'
)


def _dist_form():
    return {
        "version_hash": "abc1234",
        "file": [
            (BytesIO(_MANIFEST), "manifest.json"),
            (BytesIO(b"{}"), "mf-manifest.json"),
        ],
    }


def test_publish_rejected_without_secret(client):
    r = client.post(
        "/api/internal/modules/demo/publish",
        data=_dist_form(),
        content_type="multipart/form-data",
    )
    assert r.status_code == 401


def test_publish_rejected_wrong_secret(client):
    r = client.post(
        "/api/internal/modules/demo/publish",
        headers={"X-Internal-Service-Secret": "nope"},
        data=_dist_form(),
        content_type="multipart/form-data",
    )
    assert r.status_code == 401


def test_publish_ok_with_secret(client, monkeypatch):
    import blueprints.modules as m

    monkeypatch.setattr(
        m,
        "_upload_dist_and_activate",
        lambda mid, files, manifest, vh: (201, {"success": True, "version_hash": vh}),
    )
    r = client.post(
        "/api/internal/modules/demo/publish",
        headers={"X-Internal-Service-Secret": "test-secret"},
        data=_dist_form(),
        content_type="multipart/form-data",
    )
    assert r.status_code == 201
    assert r.get_json()["version_hash"] == "abc1234"


# ---------------------------------------------------------------------------
# FIWARE publish gate (_fiware_publish_gate)
# ---------------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402
import blueprints.modules as _mod  # noqa: E402


def _gate_conn(fiware, deployed_version):
    cur = MagicMock()
    cur.fetchone.return_value = (fiware, deployed_version)
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_fiware_gate_stamps_compliant_and_proceeds():
    """Publish stamps compliant and proceeds (OIDC chain auth)."""
    conn, cur = _gate_conn({"status": "pending"}, None)
    with patch.object(_mod, "get_db_connection_simple", return_value=conn), \
         patch.object(_mod, "return_db_connection"):
        result = _mod._fiware_publish_gate("demo")
    assert result is None  # proceed
    assert any("UPDATE marketplace_modules" in str(c.args[0])
               for c in cur.execute.call_args_list), "should stamp compliant"
    conn.commit.assert_called_once()


def test_fiware_gate_returns_404_if_module_not_found():
    """No marketplace_modules row => 404."""
    cur = MagicMock()
    cur.fetchone.return_value = None
    conn = MagicMock()
    conn.cursor.return_value = cur
    with app.app_context(), \
         patch.object(_mod, "get_db_connection_simple", return_value=conn), \
         patch.object(_mod, "return_db_connection"):
        result = _mod._fiware_publish_gate("nonexistent")
    assert result is not None
    assert result[1] == 404
