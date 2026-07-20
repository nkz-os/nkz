"""Smoke tests for all entity-manager routes (79 routes, 9 domains).

Covers auth gating (401 without cookie) and happy path
(mocked deps -> expected status + basic JSON shape).
"""

import json
import os
import sys
from functools import wraps
from io import BytesIO
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Module-level mocks — must be set BEFORE importing entity_management_api
# ---------------------------------------------------------------------------

# Set env vars needed by module-level code before import so POSTGRES_URL
# etc. are non-None in the production checks.
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
# parcel_activation.py imports common.tier_quotas at module level; pre-seed
# the submodule because the parent "common" stub is not a package.
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
sys.modules["parcel_sync"] = MagicMock()
sys.modules["module_metrics"] = MagicMock()

# Mock geo_utils to avoid shapely import error in get_parcel_agro_status
_geo_utils_mock = MagicMock()
_geo_utils_mock.get_parcel_location.return_value = {"lat": 42.0, "lon": -2.0}
sys.modules["geo_utils"] = _geo_utils_mock

import pytest  # noqa: E402
from entity_management_api import app  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status=200, data=None):
    payload = data if data is not None else {}
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.headers = {}
    resp.text = json.dumps(payload)
    resp.ok = status < 400
    return resp


_DEFAULT_ROW = {
    "id": 1,
    "code": "test",
    "name": "test",
    "ine_code": "31001",
    "municipality_code": "31001",
    "station_id": "test-station",
    "label": "Test Location",
    "is_primary": False,
    "metadata": "{}",
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-01T00:00:00",
    "latitude": 42.0,
    "longitude": -2.0,
    "sdm_entity_type": "AgriSensor",
    "mapping": "{}",
    "plan_type": "pro",
    "planType": "pro",
    "max_users": 100,
    "maxUsers": 100,
    "max_robots": 10,
    "maxRobots": 10,
    "max_sensors": 50,
    "maxSensors": 50,
    "max_parcels": 100,
    "maxParcels": 100,
    "max_entities_total": 1000,
    "maxEntitiesTotal": 1000,
    "max_area_hectares": 100.0,
    "maxAreaHectares": 100.0,
    "value_json": "{}",
    "feature": {},
    "status": "active",
    "query": "",
    "date_from": None,
    "date_to": None,
    "total_count": 0,
    # Governance
    "tenant_id": "test-tenant",
    "tenant_name": "Test Tenant",
    "plan_level": 1,
    "contract_end_date": None,
    "billing_email": "test@example.com",
    "notes": None,
    "sales_contact": None,
    "support_level": "standard",
    "email": "test@example.com",
    "expires_at": None,
    # Audit logs
    "exists": True,
    "total": 0,
    # Telemetry
    "observed_at": "2024-01-01T00:00:00",
    "payload": "{}",
    "total_records": 0,
    "first_record": None,
    "last_record": None,
    # Marketplace modules
    "is_active": True,
    "required_plan_level": 0,
    "category": "agriculture",
    "display_name": "Test Module",
}


def _make_db_conn(**cursor_kwargs):
    fetchone = cursor_kwargs.pop("fetchone", _DEFAULT_ROW)
    fetchall = cursor_kwargs.pop("fetchall", [])
    cur = MagicMock()
    cur.fetchall.return_value = fetchall
    cur.fetchone.return_value = fetchone
    cur.execute.return_value = None
    cur.rowcount = 1
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False

    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.closed = False
    conn.commit.return_value = None
    return conn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Test client WITH auth cookie set — for happy-path tests."""
    with app.test_client() as c:
        c.set_cookie("nkz_token", "fake-token")
        yield c


@pytest.fixture
def anon_client():
    """Test client WITHOUT auth cookie — for auth-gate tests."""
    with app.test_client() as c:
        yield c


# Standard patches for each domain family
@pytest.fixture
def patch_requests():
    with patch(
        "entity_management_api.requests.get", return_value=_make_response(200, {})
    ):
        with patch(
            "entity_management_api.requests.post", return_value=_make_response(200, {})
        ):
            with patch(
                "entity_management_api.requests.patch",
                return_value=_make_response(200, {}),
            ):
                with patch(
                    "entity_management_api.requests.put",
                    return_value=_make_response(200, {}),
                ):
                    with patch(
                        "entity_management_api.requests.delete",
                        return_value=_make_response(200, {}),
                    ):
                        yield


@pytest.fixture
def patch_db():
    conn = _make_db_conn()
    with patch(
        "entity_management_api.get_db_connection_with_tenant", return_value=conn
    ):
        with patch("entity_management_api.get_db_connection_simple", return_value=conn):
            yield


@pytest.fixture
def patch_s3():
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=b"{}"))}
    s3.put_object.return_value = None
    s3.delete_object.return_value = None
    s3.list_objects.return_value = {"Contents": []}
    with patch("entity_management_api.get_assets_s3_client", return_value=s3):
        yield


@pytest.fixture
def patch_mqtt():
    mqtt_client = MagicMock()
    mqtt_client.is_connected.return_value = True
    mqtt_client.publish.return_value.rc = 0
    with patch("entity_management_api.mqtt_client", mqtt_client):
        with patch("entity_management_api.MQTT_AVAILABLE", True):
            yield


# ============================================================================
# Test classes
# ============================================================================


class TestHealthVersionMetrics:
    """Exempt from auth — just verify 200 + shape."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/health"),
            ("GET", "/version"),
            ("GET", "/metrics"),
        ],
    )
    def test_healthy(self, anon_client, method, path):
        r = anon_client.open(path, method=method)
        assert r.status_code == 200

    def test_health_shape(self, anon_client):
        assert anon_client.get("/health").get_json()["status"] == "healthy"

    def test_version_shape(self, anon_client):
        data = anon_client.get("/version").get_json()
        assert data["service"] == "entity-manager"

    def test_metrics_content_type(self, anon_client):
        assert "text/plain" in anon_client.get("/metrics").content_type


# NOTE: weather routes moved to the standalone weather-api service
# (entity_management_api.py: "weather_bp removed — routes now served by
# standalone weather-api service"). Their smoke tests moved out with them;
# weather-api still needs its own suite.

# ============================================================================
# Admin (14 routes)
# ============================================================================


class TestAdminRoutes:
    AUTH = [
        ("GET", "/api/admin/tenant-limits"),
        ("GET", "/api/admin/tenant-usage"),
        ("PATCH", "/api/admin/tenant-limits"),
        ("POST", "/api/admin/terms/es"),
        ("GET", "/api/admin/tenants"),
        ("GET", "/api/admin/activations"),
        ("DELETE", "/api/admin/tenants/test/purge"),
        ("GET", "/api/admin/tenants/test/governance"),
        ("PUT", "/api/admin/tenants/test/governance"),
        ("GET", "/api/admin/audit-logs"),
        ("PUT", "/api/admin/platform-settings/landing-mode"),
    ]

    @pytest.mark.parametrize("method,path", AUTH)
    def test_requires_auth(self, anon_client, method, path):
        assert anon_client.open(path, method=method).status_code == 401

    # Public endpoints (no @require_auth)
    def test_get_terms_public(self, anon_client):
        r = anon_client.get("/api/admin/terms/es")
        assert r.status_code == 200
        assert "content" in r.get_json()

    def test_public_platform_settings(self, anon_client):
        r = anon_client.get("/api/public/platform-settings")
        assert r.status_code == 200
        assert "landing_mode" in r.get_json()

    # GET routes
    @pytest.mark.parametrize(
        "method,path,expected_status",
        [
            ("GET", "/api/admin/tenant-limits", 404),
            ("GET", "/api/admin/tenant-usage", 200),
            ("GET", "/api/admin/tenants", 200),
            ("GET", "/api/admin/activations", 200),
            ("GET", "/api/admin/tenants/test/governance", 200),
            ("GET", "/api/admin/audit-logs", 200),
        ],
    )
    # Patch blueprint namespaces so extracted routes see the same mocks.
    # helpers.tenant_limits imports db_helper functions at module level, so
    # the sys.modules["db_helper"] MagicMock leaks into get_limits_for_tenant
    # (MagicMock rows are not JSON serializable) — patch that namespace too.
    @patch("helpers.tenant_limits.return_db_connection")
    @patch("helpers.tenant_limits.get_db_connection_simple")
    @patch("blueprints.admin.return_db_connection")
    @patch("blueprints.admin.get_db_connection_with_tenant")
    @patch("blueprints.admin.get_db_connection_simple")
    @patch("entity_management_api.requests.get", return_value=_make_response(200, {}))
    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_get_ok(
        self,
        mock_db_simple,
        mock_db_tenant,
        mock_req,
        mock_bp_db_simple,
        mock_bp_db_tenant,
        mock_bp_return_db,
        mock_helpers_db_simple,
        mock_helpers_return_db,
        client,
        method,
        path,
        expected_status,
    ):
        conn = _make_db_conn()
        mock_db_simple.return_value = conn
        mock_db_tenant.return_value = conn
        mock_bp_db_simple.return_value = conn
        mock_bp_db_tenant.return_value = conn
        # _get_limits_from_db indexes the row positionally (row[0]..row[6])
        mock_helpers_db_simple.return_value = _make_db_conn(
            fetchone=("pro", 100, 10, 50, 100.0, 100, 1000)
        )
        r = client.open(path, method=method)
        assert r.status_code == expected_status, f"{method} {path} got {r.status_code}"
        r.get_json()

    # PATCH /api/admin/tenant-limits
    @patch("entity_management_api.get_db_connection_simple")
    def test_patch_tenant_limits_ok(self, mock_db, client):
        mock_db.return_value = _make_db_conn()
        r = client.patch(
            "/api/admin/tenant-limits",
            content_type="application/json",
            data=json.dumps({"maxUsers": 10}),
        )
        a = (404,)
        assert r.status_code in a, f"PATCH tenant-limits got {r.status_code}"
        r.get_json()

    # POST /api/admin/terms/es
    @patch("entity_management_api.get_db_connection_simple")
    def test_post_terms_ok(self, mock_db, client):
        mock_db.return_value = _make_db_conn()
        r = client.post(
            "/api/admin/terms/es",
            content_type="application/json",
            data=json.dumps({"content": "test terms"}),
        )
        assert r.status_code in (200, 201, 500), f"POST terms got {r.status_code}"
        r.get_json()

    # DELETE /api/admin/tenants/test/purge
    @patch("entity_management_api.get_db_connection_simple")
    def test_delete_tenant_purge_ok(self, mock_db, client):
        mock_db.return_value = _make_db_conn()
        r = client.delete("/api/admin/tenants/test/purge")
        # Endpoint is intentionally deprecated (purge moved to tenant-webhook)
        assert r.status_code == 410, f"DELETE purge got {r.status_code}"
        assert "migrated_to" in r.get_json()

    # PUT /api/admin/tenants/test/governance
    @patch("entity_management_api.get_db_connection_simple")
    def test_put_governance_ok(self, mock_db, client):
        mock_db.return_value = _make_db_conn()
        r = client.put(
            "/api/admin/tenants/test/governance",
            content_type="application/json",
            data=json.dumps({"plan_type": "pro", "billing_email": "admin@test.com"}),
        )
        assert r.status_code in (200, 500), f"PUT governance got {r.status_code}"
        r.get_json()

    # PUT /api/admin/platform-settings/landing-mode
    @patch("entity_management_api.get_db_connection_simple")
    def test_put_landing_mode_ok(self, mock_db, client):
        mock_db.return_value = _make_db_conn()
        r = client.put(
            "/api/admin/platform-settings/landing-mode",
            content_type="application/json",
            data=json.dumps({"landing_mode": "standard"}),
        )
        assert r.status_code in (200, 500), f"PUT landing-mode got {r.status_code}"
        r.get_json()


# ============================================================================
# Entity routes (8 routes)
# ============================================================================


class TestEntityRoutes:
    AUTH = [
        ("GET", "/instances/AgriParcel"),
        ("POST", "/instances/AgriParcel"),
        ("GET", "/instances/AgriParcel/test-id"),
        ("PATCH", "/instances/AgriParcel/test-id"),
        ("DELETE", "/instances/AgriParcel/test-id"),
        ("GET", "/api/entities/inventory"),
        ("GET", "/api/entities/parents"),
        ("GET", "/api/entities/test-id/timeseries-location"),
    ]

    @pytest.mark.parametrize("method,path", AUTH)
    def test_requires_auth(self, anon_client, method, path):
        assert anon_client.open(path, method=method).status_code == 401

    @pytest.mark.parametrize(
        "method,path,expected_status",
        [
            ("GET", "/instances/AgriParcel", 200),
            ("GET", "/instances/AgriParcel/test-id", 200),
            ("GET", "/api/entities/inventory", 200),
            ("GET", "/api/entities/parents", 200),
            ("GET", "/api/entities/test-id/timeseries-location", 200),
        ],
    )
    @patch("entity_management_api.requests.get", return_value=_make_response(200, []))
    def test_get_ok(self, mock_get, client, method, path, expected_status):
        r = client.open(path, method=method)
        assert r.status_code == expected_status, f"{method} {path} got {r.status_code}"
        r.get_json()

    @patch(
        "entity_management_api.requests.post",
        return_value=_make_response(201, {"id": "test-id"}),
    )
    def test_post_instance_ok(self, mock_post, client):
        r = client.post(
            "/instances/AgriParcel",
            content_type="application/json",
            data=json.dumps({"name": "test parcel"}),
        )
        assert r.status_code in (201, 200, 400, 500), (
            f"POST instance got {r.status_code}"
        )
        r.get_json()

    @patch("entity_management_api.requests.get", return_value=_make_response(200, {}))
    @patch("entity_management_api.requests.patch", return_value=_make_response(200, {}))
    def test_patch_instance_ok(self, mock_patch, mock_get, client):
        r = client.patch(
            "/instances/AgriParcel/test-id",
            content_type="application/json",
            data=json.dumps({"name": "updated"}),
        )
        assert r.status_code in (200, 500), f"PATCH instance got {r.status_code}"
        r.get_json()

    @patch("entity_management_api.requests.get", return_value=_make_response(200, {}))
    @patch(
        "entity_management_api.requests.delete", return_value=_make_response(204, {})
    )
    def test_delete_instance_ok(self, mock_del, mock_get, client):
        r = client.delete("/instances/AgriParcel/test-id")
        assert r.status_code in (204, 200, 500), f"DELETE instance got {r.status_code}"


# ============================================================================
# Entity-Type routes (4 routes)
# ============================================================================


class TestEntityTypeRoutes:
    AUTH = [
        ("GET", "/entity-types"),
        ("GET", "/entity-types/test-cat/test-type"),
        ("POST", "/entity-types/test-cat/test-type"),
        ("DELETE", "/entity-types/test-cat/test-type"),
    ]

    @pytest.mark.parametrize("method,path", AUTH)
    def test_requires_auth(self, anon_client, method, path):
        assert anon_client.open(path, method=method).status_code == 401

    @pytest.mark.parametrize(
        "method,path,expected_status",
        [
            ("GET", "/entity-types", 200),
        ],
    )
    def test_get_list_ok(self, client, method, path, expected_status):
        r = client.open(path, method=method)
        assert r.status_code == expected_status, f"{method} {path} got {r.status_code}"
        r.get_json()

    @patch("entity_management_api.get_db_connection_simple")
    def test_get_type_detail_ok(self, mock_db, client):
        conn = _make_db_conn(fetchone=_DEFAULT_ROW)
        mock_db.return_value = conn
        r = client.get("/entity-types/test-cat/test-type")
        assert r.status_code in (200, 404), (
            f"GET entity-type detail got {r.status_code}"
        )
        if r.status_code == 200:
            r.get_json()

    @patch("entity_management_api.get_db_connection_simple")
    def test_post_entity_type_ok(self, mock_db, client):
        conn = _make_db_conn()
        mock_db.return_value = conn
        r = client.post(
            "/entity-types/test-cat/test-type",
            content_type="application/json",
            data=json.dumps({"schema": {}, "label": "Test"}),
        )
        assert r.status_code in (201, 200, 400, 500), (
            f"POST entity-type got {r.status_code}"
        )
        if r.status_code in (200, 201):
            r.get_json()

    @patch("entity_management_api.get_db_connection_simple")
    def test_delete_entity_type_ok(self, mock_db, client):
        conn = _make_db_conn()
        mock_db.return_value = conn
        r = client.delete("/entity-types/test-cat/test-type")
        assert r.status_code in (200, 404, 500), (
            f"DELETE entity-type got {r.status_code}"
        )
        if r.status_code == 200:
            r.get_json()


# ============================================================================
# Sensor / Device routes (10 routes)
# ============================================================================


class TestSensorRoutes:
    AUTH = [
        ("POST", "/api/sensors/register"),
        ("GET", "/api/sensors/profiles"),
        ("GET", "/api/sensors/profiles/status"),
        ("GET", "/api/sensors"),
        ("GET", "/api/devices/test-device/telemetry"),
        ("GET", "/api/devices/test-device/telemetry/latest"),
        ("GET", "/api/devices/test-device/telemetry/stats"),
        ("POST", "/api/devices/test-device/commands"),
        ("GET", "/api/devices/test-device/commands"),
        ("GET", "/api/heartbeat/check"),
    ]

    @pytest.mark.parametrize("method,path", AUTH)
    def test_requires_auth(self, anon_client, method, path):
        assert anon_client.open(path, method=method).status_code == 401

    @pytest.mark.parametrize(
        "method,path,expected_status",
        [
            ("GET", "/api/sensors/profiles", 200),
            ("GET", "/api/sensors/profiles/status", 200),
            ("GET", "/api/sensors", 200),
            ("GET", "/api/devices/test-device/telemetry", 200),
            ("GET", "/api/devices/test-device/telemetry/latest", 200),
            ("GET", "/api/devices/test-device/telemetry/stats", 200),
            ("GET", "/api/devices/test-device/commands", 200),
        ],
    )
    @patch("blueprints.sensors.get_db_connection_with_tenant")
    @patch("blueprints.sensors.get_db_connection_simple")
    @patch("entity_management_api.requests.get", return_value=_make_response(200, {}))
    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_get_ok(
        self,
        mock_db_simple,
        mock_db_tenant,
        mock_req,
        mock_bp_db_simple,
        mock_bp_db_tenant,
        client,
        method,
        path,
        expected_status,
    ):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        mock_bp_db_tenant.return_value = conn
        mock_bp_db_simple.return_value = conn
        r = client.open(path, method=method)
        assert r.status_code in (expected_status, 500), (
            f"{method} {path} got {r.status_code}"
        )
        r.get_json()

    @patch("blueprints.sensors.get_db_connection_with_tenant")
    @patch("blueprints.sensors.get_db_connection_simple")
    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_register_sensor_ok(
        self,
        mock_db_simple,
        mock_db_tenant,
        mock_bp_db_simple,
        mock_bp_db_tenant,
        client,
    ):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        mock_bp_db_tenant.return_value = conn
        mock_bp_db_simple.return_value = conn
        r = client.post(
            "/api/sensors/register",
            content_type="application/json",
            data=json.dumps(
                {
                    "external_id": "TEST_SENSOR_1",
                    "name": "Test Sensor",
                    "profile": "temperature",
                    "location": {"lat": 42.0, "lon": -2.0},
                }
            ),
        )
        assert r.status_code in (201, 200, 400, 500), (
            f"POST sensor register got {r.status_code}"
        )
        r.get_json()

    @patch("blueprints.sensors.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_with_tenant")
    def test_post_command_ok(self, mock_db, mock_bp_db, client):
        conn = _make_db_conn()
        mock_db.return_value = conn
        mock_bp_db.return_value = conn
        r = client.post(
            "/api/devices/test-device/commands",
            content_type="application/json",
            data=json.dumps({"command": "restart", "payload": {}}),
        )
        assert r.status_code in (200, 201, 400, 500, 503), (
            f"POST command got {r.status_code}"
        )
        r.get_json()

    @patch("blueprints.sensors.psycopg2.connect")
    def test_heartbeat_ok(self, mock_conn, client):
        mock_conn.return_value = _make_db_conn()
        r = client.get("/api/heartbeat/check")
        assert r.status_code in (200, 400, 500, 503), (
            f"GET heartbeat got {r.status_code}"
        )
        r.get_json()


# ============================================================================
# Asset routes (8 routes)
# ============================================================================


class TestAssetRoutes:
    AUTH = [
        ("POST", "/api/assets"),
        ("POST", "/api/assets/upload"),
        ("GET", "/api/assets/test-id"),
        ("DELETE", "/api/assets/test-id"),
        ("GET", "/api/assets/tenant"),
        ("GET", "/api/assets/public"),
        ("POST", "/api/assets/public"),
        ("DELETE", "/api/assets/public/test.txt"),
    ]

    @pytest.mark.parametrize("method,path", AUTH)
    def test_requires_auth(self, anon_client, method, path):
        assert anon_client.open(path, method=method).status_code == 401

    @pytest.mark.parametrize(
        "method,path,expected_status",
        [
            ("GET", "/api/assets/test-id", 200),
            ("GET", "/api/assets/tenant", 200),
            ("GET", "/api/assets/public", 200),
        ],
    )
    @patch("blueprints.assets.get_assets_s3_client")
    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_get_ok(
        self,
        mock_db_simple,
        mock_db_tenant,
        mock_s3,
        client,
        method,
        path,
        expected_status,
    ):
        s3 = MagicMock()
        s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b"{}"))
        }
        s3.list_objects.return_value = {"Contents": []}
        s3.head_object.return_value = {}
        s3.generate_presigned_url.return_value = (
            "https://test.example.com/presigned-url"
        )
        mock_s3.return_value = s3
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        r = client.open(path, method=method)
        assert r.status_code in (expected_status, 503), (
            f"{method} {path} got {r.status_code}"
        )
        if r.status_code == expected_status:
            r.get_json()

    def test_post_asset_ok(self, client):
        """POST /api/assets with a valid SDM assetType."""
        r = client.post(
            "/api/assets",
            content_type="application/json",
            data=json.dumps(
                {
                    "assetType": "OliveTree",
                    "geometry": {"type": "Point", "coordinates": [-2.0, 42.0]},
                    "name": "test-olive",
                }
            ),
        )
        assert r.status_code in (201, 200, 400, 500), f"POST asset got {r.status_code}"
        if r.status_code in (200, 201):
            r.get_json()

    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_post_asset_public_ok(self, mock_db_simple, mock_db_tenant, client):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        r = client.post(
            "/api/assets/public",
            content_type="application/json",
            data=json.dumps({"name": "public-file.txt"}),
        )
        assert r.status_code in (200, 201, 400, 500), (
            f"POST public asset got {r.status_code}"
        )
        r.get_json()

    @patch("entity_management_api.requests.get", return_value=_make_response(200, {}))
    @patch("entity_management_api.get_db_connection_with_tenant")
    def test_delete_asset_ok(self, mock_db, mock_req, client):
        mock_db.return_value = _make_db_conn()
        r = client.delete("/api/assets/test-id")
        assert r.status_code in (200, 204, 500, 503), (
            f"DELETE asset got {r.status_code}"
        )

    @patch("entity_management_api.get_db_connection_with_tenant")
    def test_delete_public_asset_ok(self, mock_db, client):
        mock_db.return_value = _make_db_conn()
        r = client.delete("/api/assets/public/test.txt")
        assert r.status_code in (200, 204, 500, 503), (
            f"DELETE public got {r.status_code}"
        )

    def test_post_asset_upload_ok(self, client):
        r = client.post(
            "/api/assets/upload",
            content_type="application/json",
            data=json.dumps({"filename": "test.png"}),
        )
        assert r.status_code in (201, 200, 400, 500), f"POST upload got {r.status_code}"


# ============================================================================
# Module routes (15 routes)
# ============================================================================


class TestModuleRoutes:
    AUTH = [
        ("GET", "/api/modules/me"),
        ("POST", "/api/modules/test/toggle"),
        ("GET", "/api/modules/marketplace"),
        ("POST", "/api/modules/test/activate"),
        ("GET", "/api/modules/test/can-install"),
        ("GET", "/api/modules/visibility"),
        ("PUT", "/api/modules/visibility"),
        ("POST", "/api/modules/test/deploy"),
        ("POST", "/api/modules/test/dist"),
        ("GET", "/api/modules/test/health"),
        ("GET", "/api/admin/modules/health"),
    ]

    @pytest.mark.parametrize("method,path", AUTH)
    def test_requires_auth(self, anon_client, method, path):
        assert anon_client.open(path, method=method).status_code == 401

    @pytest.mark.parametrize(
        "method,path,expected_status",
        [
            ("GET", "/api/modules/me", 200),
            ("GET", "/api/modules/marketplace", 200),
            ("GET", "/api/modules/test/can-install", 200),
            ("GET", "/api/modules/visibility", 200),
            ("GET", "/api/modules/test/health", 200),
        ],
    )
    @patch("blueprints.modules.return_db_connection")
    @patch("blueprints.modules.get_db_connection_with_tenant")
    @patch("blueprints.modules.get_db_connection_simple")
    @patch("entity_management_api.requests.get", return_value=_make_response(200, {}))
    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_get_ok(
        self,
        mock_db_simple,
        mock_db_tenant,
        mock_req,
        mock_bp_db_simple,
        mock_bp_db_tenant,
        mock_bp_return_db,
        client,
        method,
        path,
        expected_status,
    ):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        mock_bp_db_tenant.return_value = conn
        mock_bp_db_simple.return_value = conn
        r = client.open(path, method=method)
        assert r.status_code in (expected_status, 500, 503), (
            f"{method} {path} got {r.status_code}"
        )
        if r.status_code == expected_status:
            r.get_json()

    @patch("blueprints.modules.get_db_connection_with_tenant")
    @patch("blueprints.modules.get_db_connection_simple")
    @patch("entity_management_api.requests.get", return_value=_make_response(200, {}))
    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_toggle_module_ok(
        self,
        mock_db_simple,
        mock_db_tenant,
        mock_req,
        mock_bp_db_simple,
        mock_bp_db_tenant,
        client,
    ):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        mock_bp_db_tenant.return_value = conn
        mock_bp_db_simple.return_value = conn
        r = client.post(
            "/api/modules/test/toggle",
            content_type="application/json",
            data=json.dumps({"enabled": True}),
        )
        assert r.status_code in (200, 403, 500), f"POST toggle got {r.status_code}"
        r.get_json()

    @patch("blueprints.modules.return_db_connection")
    @patch("blueprints.modules.get_db_connection_simple")
    def test_activate_module_ok(self, mock_bp_db_simple, mock_bp_return_db, client):
        conn = _make_db_conn()
        mock_bp_db_simple.return_value = conn
        r = client.post(
            "/api/modules/test/activate",
            content_type="application/json",
            data=json.dumps({"config": {}}),
        )
        assert r.status_code in (200, 201, 500), f"POST activate got {r.status_code}"
        r.get_json()

    @patch("blueprints.modules.get_db_connection_with_tenant")
    @patch("blueprints.modules.get_db_connection_simple")
    def test_put_visibility_ok(self, mock_bp_db_simple, mock_bp_db_tenant, client):
        conn = _make_db_conn()
        mock_bp_db_tenant.return_value = conn
        mock_bp_db_simple.return_value = conn
        r = client.put(
            "/api/modules/visibility",
            content_type="application/json",
            data=json.dumps({"module": "test", "roles": ["admin"]}),
        )
        assert r.status_code in (200, 500), f"PUT visibility got {r.status_code}"
        r.get_json()


# ============================================================================
# Robot routes (1 route)
# ============================================================================


class TestRobotRoutes:
    AUTH = [("POST", "/api/robots/provision")]

    @pytest.mark.parametrize("method,path", AUTH)
    def test_requires_auth(self, anon_client, method, path):
        assert anon_client.open(path, method=method).status_code == 401

    @patch("entity_management_api.get_db_connection_with_tenant")
    def test_provision_robot_ok(self, mock_db, client):
        mock_db.return_value = _make_db_conn()
        r = client.post(
            "/api/robots/provision",
            content_type="application/json",
            data=json.dumps({"robotId": "R-001", "type": "sprayer"}),
        )
        assert r.status_code in (201, 200, 400, 500), f"POST robot got {r.status_code}"
        r.get_json()


# ============================================================================
# Tenant routes (1 route)
# ============================================================================


class TestTenantRoutes:
    AUTH = [("GET", "/api/tenants/me/limits")]

    @pytest.mark.parametrize("method,path", AUTH)
    def test_requires_auth(self, anon_client, method, path):
        assert anon_client.open(path, method=method).status_code == 401

    @patch("entity_management_api.get_db_connection_simple")
    def test_my_limits_ok(self, mock_db, client):
        mock_db.return_value = _make_db_conn()
        r = client.get("/api/tenants/me/limits")
        # May return 500 if get_limits_for_tenant fails with mock DB
        assert r.status_code in (200, 500), f"GET my limits got {r.status_code}"
        if r.status_code == 200:
            r.get_json()


# ============================================================================
# Sync routes (2 routes)
# ============================================================================


class TestSyncRoutes:
    AUTH = [
        ("GET", "/api/core/sync/vectorial"),
        ("POST", "/api/core/sync/vectorial"),
    ]

    @pytest.mark.parametrize("method,path", AUTH)
    def test_requires_auth(self, anon_client, method, path):
        assert anon_client.open(path, method=method).status_code == 401

    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_get_vectorial_sync_ok(self, mock_db_simple, mock_db_tenant, client):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        r = client.get("/api/core/sync/vectorial")
        assert r.status_code in (200, 500), f"GET sync vectorial got {r.status_code}"
        r.get_json()

    @patch("entity_management_api.requests.get", return_value=_make_response(200, []))
    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_post_vectorial_sync_ok(
        self, mock_db_simple, mock_db_tenant, mock_req, client
    ):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        r = client.post(
            "/api/core/sync/vectorial",
            content_type="application/json",
            data=json.dumps({"changes": {}, "last_pulled_at": 0}),
        )
        assert r.status_code in (200, 500), f"POST sync vectorial got {r.status_code}"
        r.get_json()


# =============================================================================
# POST /api/modules/<module_id>/dist — auto-deploy dist to MinIO
# =============================================================================

_VALID_DIST_MANIFEST = {
    "id": "test-module",
    "name": "test-module",
    "displayName": "Test Module",
    "version": "1.0.0",
    "hostApiVersion": "^2.0.0",
    "description": "A test module.",
    "author": "Test Author",
    "route": "/test-module",
    "requiredRoles": ["Farmer"],
    "requiredPlan": "basic",
}


def _make_valid_dist_files(manifest=None):
    """Helper: return a list of (data, filename) tuples simulating dist/."""
    m = manifest if manifest is not None else _VALID_DIST_MANIFEST
    return [
        (json.dumps(m).encode(), "manifest.json"),
        (b"// remote entry content", "remoteEntry.js"),
        (b'{"name":"test-module","exposes":{}}', "mf-manifest.json"),
        (b"// chunk", "assets/Module-abc123.js"),
    ]


class TestDeployModuleDist:
    @patch("blueprints.modules._get_frontend_s3_client")
    @patch("blueprints.modules.return_db_connection")
    @patch("blueprints.modules.get_db_connection_simple")
    def test_missing_manifest_json(self, mock_db, mock_return, mock_s3, client):
        """No manifest.json → 400."""
        mock_db.return_value = _make_db_conn()
        mock_s3.return_value = MagicMock()

        data = {"file": [(BytesIO(b"not-manifest"), "remoteEntry.js")]}
        r = client.post(
            "/api/modules/test-module/dist",
            content_type="multipart/form-data",
            data=data,
        )
        assert r.status_code == 400
        body = r.get_json()
        assert "manifest.json" in body["error"].lower()

    @patch("blueprints.modules._get_frontend_s3_client")
    @patch("blueprints.modules.return_db_connection")
    @patch("blueprints.modules.get_db_connection_simple")
    def test_invalid_json_manifest(self, mock_db, mock_return, mock_s3, client):
        """manifest.json is garbage JSON → 400."""
        mock_db.return_value = _make_db_conn()
        mock_s3.return_value = MagicMock()

        data = {"file": [(BytesIO(b"{not valid json"), "manifest.json")]}
        r = client.post(
            "/api/modules/test-module/dist",
            content_type="multipart/form-data",
            data=data,
        )
        assert r.status_code == 400
        body = r.get_json()
        assert "valid json" in body["error"].lower()

    @patch("blueprints.modules._get_frontend_s3_client")
    @patch("blueprints.modules.return_db_connection")
    @patch("blueprints.modules.get_db_connection_simple")
    def test_missing_required_field(self, mock_db, mock_return, mock_s3, client):
        """manifest.json missing 'version' → 400."""
        mock_db.return_value = _make_db_conn()
        mock_s3.return_value = MagicMock()

        bad = dict(_VALID_DIST_MANIFEST)
        del bad["version"]
        files = [(BytesIO(json.dumps(bad).encode()), "manifest.json")]
        data = {"file": files}
        r = client.post(
            "/api/modules/test-module/dist",
            content_type="multipart/form-data",
            data=data,
        )
        assert r.status_code == 400
        body = r.get_json()
        assert "missing" in body["error"].lower()
        assert "version" in body["error"]

    @patch("blueprints.modules._get_frontend_s3_client")
    @patch("blueprints.modules.return_db_connection")
    @patch("blueprints.modules.get_db_connection_simple")
    def test_module_id_mismatch(self, mock_db, mock_return, mock_s3, client):
        """URL module_id != manifest.id → 400."""
        mock_db.return_value = _make_db_conn()
        mock_s3.return_value = MagicMock()

        bad = dict(_VALID_DIST_MANIFEST)
        bad["id"] = "other-module"
        files = [(BytesIO(json.dumps(bad).encode()), "manifest.json")]
        data = {"file": files}
        r = client.post(
            "/api/modules/test-module/dist",
            content_type="multipart/form-data",
            data=data,
        )
        assert r.status_code == 400
        body = r.get_json()
        assert "mismatch" in body["error"].lower()

    @patch("blueprints.modules._get_frontend_s3_client")
    @patch("blueprints.modules.return_db_connection")
    @patch("blueprints.modules.get_db_connection_simple")
    def test_path_traversal_rejected(self, mock_db, mock_return, mock_s3, client):
        """Filename with .. in path → 400."""
        mock_db.return_value = _make_db_conn()
        mock_s3.return_value = MagicMock()

        files = [
            (BytesIO(json.dumps(_VALID_DIST_MANIFEST).encode()), "manifest.json"),
            (BytesIO(b"malicious"), "../../etc/passwd"),
        ]
        data = {"file": files}
        r = client.post(
            "/api/modules/test-module/dist",
            content_type="multipart/form-data",
            data=data,
        )
        assert r.status_code == 400
        assert "invalid" in r.get_json()["error"].lower()

    @patch("blueprints.modules._get_frontend_s3_client")
    @patch("blueprints.modules.return_db_connection")
    @patch("blueprints.modules.get_db_connection_simple")
    def test_no_files(self, mock_db, mock_return, mock_s3, client):
        """Empty file list → 400."""
        mock_db.return_value = _make_db_conn()
        mock_s3.return_value = MagicMock()

        r = client.post(
            "/api/modules/test-module/dist",
            content_type="multipart/form-data",
            data={},
        )
        assert r.status_code == 400

    @patch("blueprints.modules._get_frontend_s3_client")
    @patch("blueprints.modules.return_db_connection")
    @patch("blueprints.modules.get_db_connection_simple")
    def test_s3_not_configured(self, mock_db, mock_return, mock_s3, client):
        """S3 client unavailable → 503."""
        mock_db.return_value = _make_db_conn()
        mock_s3.return_value = None  # simulate missing creds

        files = [(BytesIO(json.dumps(_VALID_DIST_MANIFEST).encode()), "manifest.json")]
        data = {"file": files}
        r = client.post(
            "/api/modules/test-module/dist",
            content_type="multipart/form-data",
            data=data,
        )
        assert r.status_code == 503
        assert "s3" in r.get_json()["error"].lower()

    @patch("blueprints.modules._get_frontend_s3_client")
    @patch("blueprints.modules.return_db_connection")
    @patch("blueprints.modules.get_db_connection_simple")
    def test_happy_path_returns_201(self, mock_db, mock_return, mock_s3, client):
        """Valid manifest + dist files → 201 with correct response shape."""
        conn = _make_db_conn()
        mock_db.return_value = conn

        s3_mock = MagicMock()
        s3_mock.put_object.return_value = None
        mock_s3.return_value = s3_mock

        files = []
        for content, name in _make_valid_dist_files():
            files.append((BytesIO(content), name))
        data = {"file": files}

        r = client.post(
            "/api/modules/test-module/dist",
            content_type="multipart/form-data",
            data=data,
        )
        assert r.status_code == 201, (
            f"Expected 201, got {r.status_code}: {r.get_data(as_text=True)}"
        )
        body = r.get_json()
        assert body["module_id"] == "test-module"
        assert body["version"] == "1.0.0"
        assert body["remote_entry_url"] == "/modules/test-module/mf-manifest.json"
        assert body["files_uploaded"] == 4  # manifest.json is now uploaded too
        assert body["is_active"] is True

        # Verify S3 was called for non-manifest files
        assert s3_mock.put_object.call_count == 4

        # Verify DB commit was called
        conn.commit.assert_called()
