"""Ids that reach the broker are only ids."""

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch
from urllib.parse import quote

import pytest

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")

_test_dir = os.path.dirname(os.path.abspath(__file__))
_svc_dir = os.path.normpath(os.path.join(_test_dir, ".."))
_services_dir = os.path.normpath(os.path.join(_svc_dir, ".."))
for _p in (_svc_dir, _services_dir, os.path.join(_services_dir, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_common_mock = MagicMock()
_common_mock.require_auth = lambda f=None, **kw: f if f else (lambda g: g)
_common_mock.inject_fiware_headers = lambda h, *a, **kw: dict(h)
for _mod in ("common", "common.auth_middleware", "common.api_errors", "common.ngsi_headers",
             "common.ngsi_payload_guard"):
    sys.modules.setdefault(_mod, _common_mock)

from flask import Flask, g  # noqa: E402

from blueprints import entities, sync  # noqa: E402
from helpers.route_identifiers import ROUTE_IDS, install_identifier_check  # noqa: E402

GOOD = ["urn:ngsi-ld:Device:tenant-a:logger:1", "urn:ngsi-ld:Device:a b", "urn:ngsi-ld:Device:a%20b", "urn:ngsi-ld:Device:ñ",
        "robot-1", "Vineyard", "da36ccd2-85d2-4c76-b552-c5c835a987c1"]
OTHER = ["a/../b", "..", "a?b", "a#b", 'a"b', "a\\b", "a%00", "a%3Fb", "a%22b", "a\n"]
GEOMETRY = {"type": "Polygon", "coordinates": [[[-1.6, 42.7], [-1.59, 42.7], [-1.59, 42.71], [-1.6, 42.7]]]}


def _app():
    app = Flask(__name__)
    install_identifier_check(app)
    for name in sorted(ROUTE_IDS):
        app.add_url_rule(f"/{name}/<{name}>", name, lambda **kw: "ok", methods=["GET", "POST"])
        app.add_url_rule(f"/path-{name}/<path:{name}>/x", f"path-{name}", lambda **kw: "ok")
    app.add_url_rule("/api/internal/tenants/<tenant_id>/ingestion", "tenants", lambda tenant_id: tenant_id, methods=["POST"])
    app.add_url_rule("/files/<path:filename>", "files", lambda filename: filename)
    return app


def test_the_checked_arguments():
    assert ROUTE_IDS == {"entity_id", "entity_type", "sensor_id", "parcel_id", "device_urn", "device_id"}


@pytest.mark.parametrize("name", sorted(["entity_id", "entity_type", "sensor_id", "parcel_id", "device_urn", "device_id"]))
@pytest.mark.parametrize("route", ["/{}/{}", "/path-{}/{}/x"])
def test_route_arguments(name, route):
    client = _app().test_client()
    for value in GOOD:
        assert client.get(route.format(name, quote(value, safe=""))).status_code == 200, value
    for value in OTHER:
        if "path-" in route and "\n" in value:
            continue  # a path argument does not match a line break
        if "/" in value and "path-" not in route:
            continue  # nor does a plain one match a slash
        resp = client.get(route.format(name, quote(value, safe="")))
        assert resp.status_code == 400, value
        assert resp.get_json() == {"error": "Invalid identifier"}


def test_other_arguments_and_preflight_are_not_judged():
    client = _app().test_client()
    assert client.post("/api/internal/tenants/a%3Fb/ingestion").status_code == 200
    assert client.get("/files/a%23b").status_code == 200
    assert client.options("/entity_id/a%3Fb").status_code == 200
    assert client.get("/nope").status_code == 404


def test_the_service_installs_the_check():
    """Asked to the real application, in a fresh interpreter: the suite replaces shared modules."""
    code = (
        "import entity_management_api as e\n"
        "r = e.app.test_client().delete('/entities/parcels/a%3Fb')\n"
        "print('STATUS', r.status_code, r.get_json())"
    )
    env = {
        **os.environ, "CORS_ORIGINS": "https://app.invalid", "KEYCLOAK_URL": "http://k/auth", "MINIO_ENDPOINT": "m:9000",
        "MINIO_ACCESS_KEY": "k", "MINIO_SECRET_KEY": "s", "ASSETS_BUCKET": "b",
        "PYTHONPATH": os.pathsep.join([_svc_dir, _services_dir, os.path.join(_services_dir, "common")]),
    }
    out = subprocess.run([sys.executable, "-c", code], cwd=_svc_dir, env=env, capture_output=True, text=True, timeout=120)
    assert "STATUS 400 {'error': 'Invalid identifier'}" in out.stdout, out.stderr[-500:]


def _resp(status, body=None):
    r = MagicMock(status_code=status, text="")
    r.json.return_value = body
    return r


@pytest.fixture
def orion():
    app = Flask(__name__)

    @app.before_request
    def _tenant():
        g.tenant = g.tenant_id = "tenant-a"

    app.register_blueprint(entities.entities_bp)
    get = MagicMock(return_value=_resp(200, []))
    post = MagicMock(return_value=_resp(201))
    with patch.object(entities.requests, "get", get), patch.object(entities.requests, "post", post), \
            patch.object(entities, "inject_fiware_headers", lambda h, *a, **kw: dict(h)):
        yield app.test_client(), get, post


def _create(client, body):
    return client.post("/entities/parcels", json={"geometry": GEOMETRY, **body}, headers={"Authorization": "Bearer t"},
                       environ_base={"HTTP_COOKIE": "nkz_token=t"})


@pytest.mark.parametrize("reference", ["REF1", "31 001/A-2", "Ñ-1"])
@pytest.mark.parametrize("around", ["{}", " {} ", "{}\n", "\t{}"])
def test_cadastral_reference_is_asked(orion, reference, around):
    client, get, _ = orion
    assert _create(client, {"cadastralReference": around.format(reference)}).status_code == 201
    call = get.call_args_list[0]
    assert call.args[0].endswith("/ngsi-ld/v1/entities")
    assert call.kwargs["params"] == {
        "type": "AgriParcel", "q": f'cadastralReference=="{reference}"', "limit": 100, "options": "keyValues",
    }


@pytest.mark.parametrize("reference", ['x"', 'x";a==1', "a?b", "a#b", "a\\b", "a\x00", "x" * 257, 3, ["a"]])
def test_other_cadastral_references_are_refused(orion, reference):
    client, get, post = orion
    resp = _create(client, {"cadastralReference": reference})
    assert resp.status_code == 422
    assert resp.get_json() == {"error": "Invalid cadastralReference"}
    assert get.call_args_list == [] and post.call_args_list == []


@pytest.mark.parametrize("parent", ["urn:ngsi-ld:AgriParcel:p1?attrs=x", "urn:ngsi-ld:AgriParcel:p1#", 'urn:ngsi-ld:AgriParcel:p1"', "urn:ngsi-ld:AgriParcel:%00", "urn:ngsi-ld:AgriParcel:x/../../types"])
def test_other_parents_are_refused(orion, parent):
    client, get, post = orion
    resp = _create(client, {"parentId": parent})
    assert resp.status_code == 422
    assert get.call_args_list == [] and post.call_args_list == []


def test_child_zones_are_asked_with_parameters(orion):
    client, get, _ = orion
    with patch.object(entities.requests, "delete", MagicMock(return_value=_resp(204))):
        client.delete("/entities/parcels/da36ccd2-85d2-4c76-b552-c5c835a987c1", headers={"Authorization": "Bearer t"},
                      environ_base={"HTTP_COOKIE": "nkz_token=t"})
    call = get.call_args_list[0]
    assert call.args[0].endswith("/ngsi-ld/v1/entities")
    assert call.kwargs["params"] == {
        "type": "AgriParcel", "limit": 100, "options": "keyValues",
        "q": 'hasAgriParcel=="urn:ngsi-ld:AgriParcel:da36ccd2-85d2-4c76-b552-c5c835a987c1"',
    }


def test_sync_ids():
    assert sync._is_syncable_urn("urn:ngsi-ld:AgriParcel:p1") is True
    assert sync._is_syncable_urn("urn:ngsi-ld:AgriParcel:a b") is True
    for value in ("p1", "", None, 3, "urn:a:b?x", "urn:a:b#", 'urn:a:b"', "urn:a:b%00"):
        assert sync._is_syncable_urn(value) is False


def test_sync_push_does_not_write_other_ids():
    app = Flask(__name__)
    patch_call = MagicMock(return_value=MagicMock(ok=True, status_code=204))
    changes = {"parcels": {"updated": [
        {"id": "w1", "remote_id": "urn:ngsi-ld:AgriParcel:x/../../types", "name": "n"},
        {"id": "w2", "remote_id": "urn:ngsi-ld:AgriParcel:p1", "name": "n"},
    ]}}
    with patch.object(sync.requests, "patch", patch_call), patch.object(sync, "_get_redis", lambda: None), \
            patch.object(sync, "inject_fiware_headers", lambda h, *a, **kw: dict(h)), \
            app.test_request_context("/", method="POST", json={"changes": changes}):
        g.tenant = "tenant-a"
        resp = sync._core_vector_sync_push()
    assert resp.get_json() == {"status": "success", "experimentalRejectedIds": {"parcels": ["w1"]}}
    assert [c.args[0].rsplit("/entities/", 1)[1] for c in patch_call.call_args_list] == ["urn:ngsi-ld:AgriParcel:p1/attrs"]
