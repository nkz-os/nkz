"""IoT Device creation in sdm-integration: validation order and compensation."""

import functools
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")
for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

PARCEL_A = "urn:ngsi-ld:AgriParcel:aaaa"
POINT = {"type": "Point", "coordinates": [-1.6, 42.8]}
ORION = "http://orion.invalid:1026"


@pytest.fixture(scope="module")
def sdm():
    """Import sdm_api with auth stubbed, then restore every global touched."""
    from flask import g

    mp = pytest.MonkeyPatch()
    mp.setenv("ORION_URL", ORION)
    mp.setenv("MONGODB_URL", "mongodb://localhost:1/x")
    saved = sys.modules.pop("sdm_api", None)

    import auth_middleware

    def fake_auth(f):
        @functools.wraps(f)
        def wrapper(*a, **k):
            g.tenant = "test-tenant"
            g.farmer_id = "user-1"
            return f(*a, **k)

        return wrapper

    mp.setattr(auth_middleware, "require_auth", fake_auth)
    mp.setattr(auth_middleware, "require_entity_ownership", lambda f: f)
    import sdm_api

    yield sdm_api

    sys.modules.pop("sdm_api", None)
    if saved is not None:
        sys.modules["sdm_api"] = saved
    mp.undo()


@pytest.fixture
def env(sdm):
    """Patched collaborators; returns the client plus the recording mocks."""
    ok = MagicMock(status_code=201, text="")
    with patch.object(sdm.requests, "post", return_value=ok) as post, \
         patch.object(sdm.requests, "delete", return_value=MagicMock(status_code=204, text="")) as delete, \
         patch.object(sdm, "provision_iot_device", return_value={
             "provisioned": True, "mqtt": {"topics": {}}, "api_key": "k", "error": None}) as prov, \
         patch.object(sdm, "_check_tenant_limits", return_value=True), \
         patch.object(sdm, "log_entity_operation"):
        yield sdm.app.test_client(), post, delete, prov


def _body(**over):
    body = {
        "name": "s1",
        "hasDeviceProfile": {"type": "Relationship", "object": "urn:ngsi-ld:DeviceProfile:p1"},
        "location": {"type": "GeoProperty", "value": POINT},
        "controlledAsset": {"type": "Relationship", "object": [PARCEL_A]},
    }
    body.update(over)
    return {k: v for k, v in body.items() if v is not None}


URL = "/sdm/entities/Device/instances"


def test_wizard_body_with_has_device_profile_is_provisioned(env):
    client, post, _, prov = env
    r = client.post(URL, json=_body())
    assert r.status_code == 201
    assert prov.call_args.kwargs["profile_id"] == "p1"


def test_legacy_ref_device_profile_is_accepted(env):
    client, post, _, prov = env
    body = _body(hasDeviceProfile=None, refDeviceProfile={"type": "Relationship", "object": "p1"})
    r = client.post(URL, json=body)
    assert r.status_code == 201
    assert prov.call_args.kwargs["profile_id"] == "p1"
    sent = post.call_args.kwargs["json"]
    assert "hasDeviceProfile" in sent
    assert "refDeviceProfile" not in sent
    assert sent["hasDeviceProfile"] == {
        "type": "Relationship", "object": "urn:ngsi-ld:DeviceProfile:p1"}


def test_bare_string_profile_is_written_as_urn_relationship(env):
    client, post, _, prov = env
    r = client.post(URL, json=_body(hasDeviceProfile="p1"))
    assert r.status_code == 201
    assert prov.call_args.kwargs["profile_id"] == "p1"
    assert post.call_args.kwargs["json"]["hasDeviceProfile"] == {
        "type": "Relationship", "object": "urn:ngsi-ld:DeviceProfile:p1"}


def test_missing_profile_writes_nothing(env):
    client, post, _, prov = env
    r = client.post(URL, json=_body(hasDeviceProfile=None))
    assert r.status_code == 400
    post.assert_not_called()
    prov.assert_not_called()


@pytest.mark.parametrize("over", [
    {"location": None},
    {"location": {"type": "GeoProperty", "value": {"type": "Polygon", "coordinates": []}}},
    {"controlledAsset": None},
    {"controlledAsset": {"type": "Relationship", "object": []}},
])
def test_device_without_point_or_parcel_writes_nothing(env, over):
    client, post, _, prov = env
    r = client.post(URL, json=_body(**over))
    assert r.status_code == 400
    post.assert_not_called()


def test_bare_geojson_point_is_accepted(env):
    client, post, _, _ = env
    assert client.post(URL, json=_body(location=POINT)).status_code == 201


def test_parcel_links_normalization(sdm):
    f = sdm._parcel_links
    assert f(PARCEL_A) == [PARCEL_A]
    assert f({"object": PARCEL_A}) == [PARCEL_A]
    assert f({"object": [PARCEL_A, "urn:b"]}) == [PARCEL_A, "urn:b"]
    assert f([{"object": PARCEL_A}, "urn:b", PARCEL_A]) == [PARCEL_A, "urn:b"]
    assert f(None) == []


def test_non_device_iot_type_needs_only_profile(env):
    client, post, _, prov = env
    body = _body(location=None, controlledAsset=None)
    r = client.post("/sdm/entities/WeatherStation/instances", json=body)
    assert r.status_code == 201


def test_failed_provisioning_removes_the_entity(sdm, env):
    client, post, delete, prov = env
    prov.return_value = {"provisioned": False, "error": "IoT Agent timeout - device not provisioned", "mqtt": None}
    r = client.post(URL, json=_body())
    assert r.status_code == 502
    assert r.get_json()["error"] == "IoT Agent timeout - device not provisioned"
    entity_id = post.call_args.kwargs["json"]["id"]
    assert delete.call_args.args[0] == f"{ORION}/ngsi-ld/v1/entities/{entity_id}"
    assert delete.call_args.kwargs["headers"]["NGSILD-Tenant"] == "test-tenant"


def test_failed_provisioning_and_failed_delete_still_502(sdm, env):
    client, _, delete, prov = env
    prov.return_value = {"provisioned": False, "error": "boom", "mqtt": None}
    delete.side_effect = sdm.requests.exceptions.ConnectionError("down")
    assert client.post(URL, json=_body()).status_code == 502


def test_regenerate_key_reads_has_device_profile(sdm):
    entity = {
        "id": "urn:ngsi-ld:Device:d1", "type": "Device",
        "hasDeviceProfile": {"type": "Relationship", "object": "urn:ngsi-ld:DeviceProfile:p9"},
    }
    got = MagicMock(status_code=200)
    got.json.return_value = entity
    with patch.object(sdm.requests, "get", return_value=got), \
         patch.object(sdm, "provision_iot_device", return_value={
             "provisioned": True, "api_key": "k", "mqtt": {}, "error": None}) as prov:
        r = sdm.app.test_client().post("/sdm/entities/urn:ngsi-ld:Device:d1/iot/regenerate-key")
    assert r.status_code == 200
    assert prov.call_args.kwargs["profile_id"] == "p9"


def test_exception_after_orion_write_removes_the_entity(env):
    client, post, delete, prov = env
    prov.side_effect = RuntimeError("agent exploded")
    r = client.post(URL, json=_body())
    assert r.status_code >= 500
    entity_id = post.call_args.kwargs["json"]["id"]
    assert delete.call_args.args[0] == f"{ORION}/ngsi-ld/v1/entities/{entity_id}"


def test_production_path_order_sends_ld_json_without_link(tmp_path):
    """Image layout: the local auth_middleware shim shadows common's. Run in a
    fresh interpreter so no module state leaks into other tests."""
    import subprocess
    code = (
        "import sys\n"
        f"sys.path[:0] = [{_SVC_DIR!r}]\n"
        "import auth_middleware as am\n"
        f"assert am.__file__.startswith({_SVC_DIR!r}), am.__file__\n"
        "from flask import Flask, g\n"
        "app = Flask('t')\n"
        "with app.app_context():\n"
        "    g.tenant = 'test-tenant'\n"
        "    h = am.inject_fiware_headers({}, 'test-tenant', body={'@context': 'x', 'id': 'i'})\n"
        "    assert h['Content-Type'] == 'application/ld+json' and 'Link' not in h, h\n"
        "    h = am.inject_fiware_headers({}, 'test-tenant')\n"
        "    assert h['Content-Type'] == 'application/json' and 'Link' in h, h\n"
        "print('ok')\n"
    )
    env = dict(os.environ, CONTEXT_URL="http://ctx.invalid/c.jsonld",
               PYTHONPATH=os.pathsep.join([_SERVICES_DIR, _COMMON_DIR]))
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path,
                         capture_output=True, text=True)
    assert out.returncode == 0 and "ok" in out.stdout, out.stderr


def test_orion_post_timeout_is_5xx_and_attempts_rollback(sdm, env):
    client, post, delete, prov = env
    post.side_effect = sdm.requests.exceptions.Timeout("slow")
    r = client.post(URL, json=_body())
    assert r.status_code >= 500
    assert post.call_args.kwargs["timeout"] == 10
    assert delete.called
    assert delete.call_args.args[0].startswith(f"{ORION}/ngsi-ld/v1/entities/")
    prov.assert_not_called()
