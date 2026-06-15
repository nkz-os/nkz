"""Tests for the parcels blueprint — create endpoint."""
import json
import os
import sys
from functools import wraps
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level stubs — must come BEFORE importing entity_management_api
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

# Replicate the same smart require_auth mock used in test_routes_smoke.py
_common_mock = MagicMock()


def _require_auth(f=None, **kwargs):
    """Mirrors test_routes_smoke behaviour: 401 unless cookie/Bearer present."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kw):
            from flask import request, g, jsonify
            token = request.cookies.get("nkz_token") or request.headers.get("Authorization", "")
            if not token:
                return jsonify({"error": "Unauthorized"}), 401
            g.current_user = {
                "sub": "test-user",
                "tenant_id": "test-tenant",
                "realm_access": {"roles": ["PlatformAdmin"]},
            }
            g.roles = ["PlatformAdmin"]
            g.tenant_id = "test-tenant"
            g.tenant = "montiko"
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
_tier_quotas_mock = MagicMock()
_tier_quotas_mock.LEVEL_TO_TIER = {0: "free"}
_tier_quotas_mock.quotas_for_tier = lambda tier: {"max_parcels": 0}
sys.modules["common.tier_quotas"] = _tier_quotas_mock
sys.modules["db_helper"] = MagicMock()
sys.modules["orion_writer"] = MagicMock()
sys.modules["module_upload_service"] = MagicMock()
sys.modules["parcel_sync"] = MagicMock()
sys.modules["module_metrics"] = MagicMock()
_geo_utils_mock = MagicMock()
_geo_utils_mock.get_parcel_location.return_value = {"lat": 42.0, "lon": -2.0}
sys.modules["geo_utils"] = _geo_utils_mock

import entity_management_api as ema  # noqa: E402


@pytest.fixture
def client():
    ema.app.config["TESTING"] = True
    with ema.app.test_client() as c:
        c.set_cookie("nkz_token", "fake-token")
        yield c


POLY = {
    "type": "Polygon",
    "coordinates": [[
        [-2.08, 42.64], [-2.08, 42.641], [-2.079, 42.641],
        [-2.079, 42.64], [-2.08, 42.64],
    ]],
}


def test_create_parcel_generates_uuid_and_writes_orion(client):
    with patch("blueprints.parcels._orion_query_by_cadastral_ref", return_value=[]), \
         patch("blueprints.parcels._orion_upsert") as up, \
         patch("blueprints.parcels._current_tenant", return_value="montiko"):
        up.return_value = (201, {})
        resp = client.post(
            "/api/entities/parcels",
            json={"name": "P1", "geometry": POLY, "cadastralReference": "REF-1"},
            headers={"X-Tenant-ID": "montiko", "X-User-ID": "u1"},
        )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["id"].startswith("urn:ngsi-ld:AgriParcel:")
    written = up.call_args.args[1]
    assert written["type"] == "AgriParcel"
    assert written["id"] == body["id"]


def test_create_parcel_invalid_geometry_returns_422(client):
    with patch("blueprints.parcels._current_tenant", return_value="montiko"):
        resp = client.post(
            "/api/entities/parcels",
            json={"name": "P1", "geometry": {"type": "Point", "coordinates": [0, 0]}},
            headers={"X-Tenant-ID": "montiko", "X-User-ID": "u1"},
        )
    assert resp.status_code == 422


def test_create_with_existing_cadastral_ref_updates_not_duplicates(client):
    existing = [{"id": "urn:ngsi-ld:AgriParcel:abc", "type": "AgriParcel"}]
    with patch("blueprints.parcels._orion_query_by_cadastral_ref", return_value=existing), \
         patch("blueprints.parcels._orion_patch_attrs", return_value=(204, {})) as pa, \
         patch("blueprints.parcels._current_tenant", return_value="montiko"):
        resp = client.post("/api/entities/parcels",
                           json={"name": "P1", "geometry": POLY, "cadastralReference": "REF-1"},
                           headers={"X-Tenant-ID": "montiko", "X-User-ID": "u1"})
    assert resp.status_code == 200
    assert resp.get_json()["id"] == "urn:ngsi-ld:AgriParcel:abc"
    assert pa.called


def test_create_zone_links_to_parent_and_inherits(client):
    from unittest.mock import patch
    with patch("blueprints.parcels._orion_upsert", return_value=(201, {})) as up, \
         patch("blueprints.parcels._orion_entity_exists", return_value=True), \
         patch("blueprints.parcels._current_tenant", return_value="montiko"):
        resp = client.post("/api/entities/parcels/urn:ngsi-ld:AgriParcel:parent/zones",
                           json={"zones": [{"name": "Z1", "geometry": POLY}], "inherit": {"cropType": "olive"}},
                           headers={"X-Tenant-ID": "montiko", "X-User-ID": "u1"})
    assert resp.status_code == 201
    zone = up.call_args.args[1]
    assert zone["type"] == "AgriParcel"
    assert zone["category"]["value"] == "managementZone"
    assert zone["hasAgriParcel"]["object"] == "urn:ngsi-ld:AgriParcel:parent"
    assert zone["cropType"]["value"] == "olive"


def test_patch_parcel_attrs(client):
    from unittest.mock import patch
    with patch("blueprints.parcels._orion_patch_attrs", return_value=(204, {})) as pa, \
         patch("blueprints.parcels._current_tenant", return_value="montiko"):
        resp = client.patch("/api/entities/parcels/urn:ngsi-ld:AgriParcel:x",
                            json={"cropType": "vine"}, headers={"X-Tenant-ID": "montiko"})
    assert resp.status_code == 204
    assert pa.called


def test_delete_parcel_cascades_to_zones(client):
    from unittest.mock import patch
    children = [{"id": "urn:ngsi-ld:AgriParcel:z1"}]
    with patch("blueprints.parcels._orion_query_children", return_value=children), \
         patch("blueprints.parcels._orion_delete", return_value=204) as dl, \
         patch("blueprints.parcels._current_tenant", return_value="montiko"):
        resp = client.delete("/api/entities/parcels/urn:ngsi-ld:AgriParcel:p1",
                            headers={"X-Tenant-ID": "montiko"})
    assert resp.status_code == 204
    assert dl.call_count == 2  # parent + 1 child


def test_query_by_cadastral_ref_includes_link_context_header():
    from unittest.mock import patch, MagicMock
    import blueprints.parcels as p
    fake = MagicMock(status_code=200)
    fake.json.return_value = []
    with patch("blueprints.parcels.requests.get", return_value=fake) as g, \
         patch("blueprints.parcels.inject_fiware_headers", side_effect=lambda h, t=None, **k: {**h, "NGSILD-Tenant": t}):
        p._orion_query_by_cadastral_ref("montiko", "REF-1")
    sent_headers = g.call_args.kwargs["headers"]
    assert "Link" in sent_headers and "json-ld#context" in sent_headers["Link"]
    assert sent_headers.get("NGSILD-Tenant") == "montiko"


def test_create_zone_missing_parent_returns_404(client):
    from unittest.mock import patch
    with patch("blueprints.parcels._orion_entity_exists", return_value=False), \
         patch("blueprints.parcels._current_tenant", return_value="montiko"):
        resp = client.post("/api/entities/parcels/urn:ngsi-ld:AgriParcel:nope/zones",
                           json={"zones": [{"name": "Z1", "geometry": POLY}]},
                           headers={"X-Tenant-ID": "montiko"})
    assert resp.status_code == 404


def test_create_parcel_rejects_injecting_cadastral_ref(client):
    from unittest.mock import patch
    with patch("blueprints.parcels._current_tenant", return_value="montiko"):
        resp = client.post("/api/entities/parcels",
                           json={"name": "P1", "geometry": POLY, "cadastralReference": 'x"||id~="urn'},
                           headers={"X-Tenant-ID": "montiko"})
    assert resp.status_code == 422


def test_projection_endpoint_requires_internal_secret(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "s3cret")
    resp = client.post("/internal/parcels/project", json={"data": []},
                       headers={"X-Internal-Service-Secret": "wrong"})
    assert resp.status_code == 403


def test_projection_endpoint_upserts_on_notification(client, monkeypatch):
    from unittest.mock import patch
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "s3cret")
    notif = {"data": [{
        "id": "urn:ngsi-ld:AgriParcel:11111111-1111-1111-1111-111111111111",
        "type": "AgriParcel",
        "location": {"type": "GeoProperty", "value": {"type": "Polygon",
            "coordinates": [[[0,0],[0,0.001],[0.001,0.001],[0,0]]]}},
    }]}
    with patch("blueprints.parcels.project_rows") as pr:
        resp = client.post("/internal/parcels/project", json=notif,
                           headers={"X-Internal-Service-Secret": "s3cret",
                                    "NGSILD-Tenant": "montiko"})
    assert resp.status_code == 200
    assert pr.called


def test_reconcile_parcels_calls_project_rows(client):
    from unittest.mock import patch, MagicMock
    entities = [
        {
            "id": "urn:ngsi-ld:AgriParcel:11111111-1111-1111-1111-111111111111",
            "type": "AgriParcel",
        }
    ]
    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = entities
    with patch("blueprints.parcels.requests.get", return_value=fake_resp), \
         patch("blueprints.parcels.project_rows") as pr, \
         patch("blueprints.parcels._current_tenant", return_value="montiko"):
        resp = client.post("/api/admin/parcels/reconcile",
                           headers={"X-Tenant-ID": "montiko"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["reconciled"] == 1
    pr.assert_called_once_with("montiko", entities, deleted=False)


def test_create_parcel_ensures_projection_subscription(client):
    from unittest.mock import patch
    with patch("blueprints.parcels._orion_query_by_cadastral_ref", return_value=[]), \
         patch("blueprints.parcels._orion_upsert", return_value=(201, {})), \
         patch("blueprints.parcels.ensure_projection_subscription") as ens, \
         patch("blueprints.parcels._current_tenant", return_value="montiko"):
        resp = client.post("/api/entities/parcels",
                           json={"name": "P1", "geometry": POLY},
                           headers={"X-Tenant-ID": "montiko", "X-User-ID": "u1"})
    assert resp.status_code == 201
    assert ens.called  # subscription ensured on create (ensure-on-use)
