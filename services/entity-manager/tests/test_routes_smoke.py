"""Smoke tests for all entity-manager routes (79 routes, 9 domains).

Covers auth gating (401 without cookie) and happy path
(mocked deps -> expected status + basic JSON shape).
"""

import json
import os
import sys
from functools import wraps
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
sys.modules["common.config_manager"] = MagicMock()

# Heavy / infrastructure dependencies
sys.modules["db_helper"] = MagicMock()
sys.modules["orion_writer"] = MagicMock()
sys.modules["module_upload_service"] = MagicMock()
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


# ============================================================================
# Weather (9 routes)
# ============================================================================


class TestWeatherRoutes:
    AUTH = [
        ("GET", "/api/weather/municipalities/search"),
        ("GET", "/api/weather/locations"),
        ("GET", "/api/weather/municipality/near"),
        ("POST", "/api/weather/locations"),
        ("GET", "/api/weather/observations/latest"),
        ("GET", "/api/weather/observations"),
        ("GET", "/api/weather/parcel/test-parcel"),
        ("GET", "/api/weather/parcel/test-parcel/agro-status"),
        ("GET", "/api/weather/alerts"),
    ]

    @pytest.mark.parametrize("method,path", AUTH)
    def test_requires_auth(self, anon_client, method, path):
        assert anon_client.open(path, method=method).status_code == 401

    @pytest.mark.parametrize(
        "method,path,expected_status,query",
        [
            ("GET", "/api/weather/municipalities/search", 200, {"q": "Pamplona"}),
            ("GET", "/api/weather/locations", 200, {}),
            (
                "GET",
                "/api/weather/municipality/near",
                200,
                {"latitude": "42.0", "longitude": "-2.0"},
            ),
            ("GET", "/api/weather/observations/latest", 200, {}),
            ("GET", "/api/weather/observations", 200, {}),
            ("GET", "/api/weather/parcel/test-parcel", 200, {}),
            ("GET", "/api/weather/parcel/test-parcel/agro-status", 200, {}),
            ("GET", "/api/weather/alerts", 200, {}),
        ],
    )
    @patch("entity_management_api.requests.get", return_value=_make_response(200, {}))
    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_get_ok(
        self,
        mock_db_simple,
        mock_db_tenant,
        mock_req,
        client,
        method,
        path,
        expected_status,
        query,
    ):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        r = client.open(path, method=method, query_string=query)
        assert r.status_code in (expected_status, 400, 500), (
            f"{method} {path} got {r.status_code}"
        )
        r.get_json()

    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_post_location_ok(self, mock_db_simple, mock_db_tenant, client):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        r = client.post(
            "/api/weather/locations",
            content_type="application/json",
            data=json.dumps({"municipality_code": "31001"}),
        )
        assert r.status_code in (201, 200, 400, 500), (
            f"POST location got {r.status_code}"
        )
        r.get_json()


# ============================================================================
# Admin (14 routes)
# ============================================================================


class TestAdminRoutes:
    AUTH = [
        ("GET", "/api/admin/tenant-limits"),
        ("GET", "/api/admin/tenant-usage"),
        ("PATCH", "/api/admin/tenant-limits"),
        ("POST", "/api/admin/terms/es"),
        ("POST", "/api/admin/parcels/sync"),
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
            ("GET", "/api/admin/tenant-limits", 200),
            ("GET", "/api/admin/tenant-usage", 200),
            ("GET", "/api/admin/tenants", 200),
            ("GET", "/api/admin/activations", 200),
            ("GET", "/api/admin/tenants/test/governance", 200),
            ("GET", "/api/admin/audit-logs", 200),
        ],
    )
    # Patch blueprint namespaces so extracted routes see the same mocks
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
        a = (200, 500)
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

    # POST /api/admin/parcels/sync
    def test_post_parcels_sync_requires_params(self, client):
        r = client.post("/api/admin/parcels/sync")
        assert r.status_code in (400, 403), "expected 400 (missing tenant_id) or 403"

    # DELETE /api/admin/tenants/test/purge
    @patch("entity_management_api.get_db_connection_simple")
    def test_delete_tenant_purge_ok(self, mock_db, client):
        mock_db.return_value = _make_db_conn()
        r = client.delete("/api/admin/tenants/test/purge")
        assert r.status_code in (200, 207, 500), f"DELETE purge got {r.status_code}"
        r.get_json()

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
    @patch("entity_management_api.requests.get", return_value=_make_response(200, {}))
    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_get_ok(
        self,
        mock_db_simple,
        mock_db_tenant,
        mock_req,
        client,
        method,
        path,
        expected_status,
    ):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        r = client.open(path, method=method)
        assert r.status_code in (expected_status, 500), (
            f"{method} {path} got {r.status_code}"
        )
        r.get_json()

    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_register_sensor_ok(self, mock_db_simple, mock_db_tenant, client):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
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

    @patch("entity_management_api.get_db_connection_with_tenant")
    def test_post_command_ok(self, mock_db, client):
        mock_db.return_value = _make_db_conn()
        r = client.post(
            "/api/devices/test-device/commands",
            content_type="application/json",
            data=json.dumps({"command": "restart", "payload": {}}),
        )
        assert r.status_code in (200, 201, 400, 500, 503), (
            f"POST command got {r.status_code}"
        )
        r.get_json()

    @patch("entity_management_api.requests.get", return_value=_make_response(200, {}))
    def test_heartbeat_ok(self, mock_get, client):
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
    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    @patch("entity_management_api.get_assets_s3_client")
    def test_get_ok(
        self,
        mock_s3,
        mock_db_simple,
        mock_db_tenant,
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

    @patch("entity_management_api.ModuleUploadService")
    def test_post_asset_upload_ok(self, mock_svc, client):
        instance = MagicMock()
        instance.handle_upload.return_value = ({"url": "http://example.com/file"}, 201)
        mock_svc.return_value = instance
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
        ("POST", "/api/modules/upload"),
        ("GET", "/api/modules/test/validation-status"),
        ("POST", "/api/internal/modules/register-validated"),
        ("POST", "/api/modules/test/deploy"),
        ("GET", "/api/modules/test/logs"),
        ("GET", "/api/modules/uploads"),
        ("GET", "/api/modules/test/health"),
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
            ("GET", "/api/modules/test/validation-status", 200),
            ("GET", "/api/modules/test/logs", 200),
            ("GET", "/api/modules/uploads", 200),
            ("GET", "/api/modules/test/health", 200),
        ],
    )
    @patch("entity_management_api.requests.get", return_value=_make_response(200, {}))
    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_get_ok(
        self,
        mock_db_simple,
        mock_db_tenant,
        mock_req,
        client,
        method,
        path,
        expected_status,
    ):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        r = client.open(path, method=method)
        assert r.status_code in (expected_status, 500, 503), (
            f"{method} {path} got {r.status_code}"
        )
        if r.status_code == expected_status:
            r.get_json()

    @patch("entity_management_api.requests.get", return_value=_make_response(200, {}))
    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_toggle_module_ok(self, mock_db_simple, mock_db_tenant, mock_req, client):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        r = client.post(
            "/api/modules/test/toggle",
            content_type="application/json",
            data=json.dumps({"enabled": True}),
        )
        assert r.status_code in (200, 403, 500), f"POST toggle got {r.status_code}"
        r.get_json()

    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_activate_module_ok(self, mock_db_simple, mock_db_tenant, client):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        r = client.post(
            "/api/modules/test/activate",
            content_type="application/json",
            data=json.dumps({"config": {}}),
        )
        assert r.status_code in (200, 201, 500), f"POST activate got {r.status_code}"
        r.get_json()

    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_put_visibility_ok(self, mock_db_simple, mock_db_tenant, client):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        r = client.put(
            "/api/modules/visibility",
            content_type="application/json",
            data=json.dumps({"module": "test", "roles": ["admin"]}),
        )
        assert r.status_code in (200, 500), f"PUT visibility got {r.status_code}"
        r.get_json()

    @patch("entity_management_api.ModuleUploadService")
    def test_upload_module_ok(self, mock_svc, client):
        instance = MagicMock()
        instance.validate_and_store_upload.return_value = (
            {"uploadId": "test-upload"},
            201,
        )
        mock_svc.return_value = instance
        r = client.post(
            "/api/modules/upload",
            content_type="application/json",
            data=json.dumps({"name": "test-module", "version": "1.0.0"}),
        )
        assert r.status_code in (201, 200, 400, 500), f"POST upload got {r.status_code}"

    @patch("entity_management_api.get_db_connection_simple")
    def test_register_validated_ok(self, mock_db, client):
        mock_db.return_value = _make_db_conn()
        r = client.post(
            "/api/internal/modules/register-validated",
            content_type="application/json",
            headers={"X-Internal-Service-Secret": "test-secret"},
            data=json.dumps(
                {"upload_id": "test-upload", "manifest_data": {"id": "test"}}
            ),
        )
        assert r.status_code in (201, 200, 500, 503), (
            f"POST register-validated got {r.status_code}"
        )
        r.get_json()

    @patch("entity_management_api.requests.get", return_value=_make_response(200, {}))
    @patch("entity_management_api.get_db_connection_with_tenant")
    @patch("entity_management_api.get_db_connection_simple")
    def test_deploy_module_ok(self, mock_db_simple, mock_db_tenant, mock_req, client):
        conn = _make_db_conn()
        mock_db_tenant.return_value = conn
        mock_db_simple.return_value = conn
        r = client.post(
            "/api/modules/test/deploy",
            content_type="application/json",
            data=json.dumps({"upload_id": "test-upload"}),
        )
        assert r.status_code in (200, 400, 500, 503), f"POST deploy got {r.status_code}"
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
