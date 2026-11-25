"""Ids that reach the broker are only ids."""

import functools
import logging
import os
import sys
from pathlib import Path

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")
_REPO_ROOT = Path(_SERVICES_DIR).parent
for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(scope="module")
def sdm():
    """Import sdm_api with auth stubbed, then restore every global touched."""
    from flask import g

    mp = pytest.MonkeyPatch()
    mp.setenv("ORION_URL", "http://orion.invalid:1026")
    mp.setenv("MONGODB_URL", "mongodb://localhost:1/x")
    mp.setenv("CORS_ORIGINS", "https://app.invalid")
    mp.delenv("CSRF_GUARD_ENFORCE", raising=False)
    saved = sys.modules.pop("sdm_api", None)

    import auth_middleware

    def fake_auth(f):
        @functools.wraps(f)
        def wrapper(*a, **k):
            g.tenant = "tenant-a"
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
def client(sdm):
    c = sdm.app.test_client()
    c.set_cookie("nkz_token", "t")
    return c


from urllib.parse import quote  # noqa: E402

GOOD = ["urn:ngsi-ld:Device:tenant-a:logger:1", "urn:ngsi-ld:Device:a b", "urn:ngsi-ld:Device:ñ", "urn:ngsi-ld:Device:gw-0123456789ab"]
OTHER = ["urn:a:b?attrs=x", "urn:a:b#x", 'urn:a:b"', "urn:a:b\\", "urn:a:b%00", "urn:a:b%3Fx", "urn:a:b%2F..%2Fc", ".."]


class _Upstream:
    status_code = 404
    text = ""

    def json(self):
        return {}


@pytest.fixture
def broker(monkeypatch, sdm):
    calls = []

    def fake(*a, **k):
        calls.append((a, k))
        return _Upstream()

    for name in ("post", "get", "patch", "put", "delete", "request"):
        monkeypatch.setattr(sdm.requests, name, fake)
    return calls


ROUTES = [
    ("get", "/sdm/entities/Device/instances/{}"),
    ("patch", "/sdm/entities/Device/instances/{}"),
    ("delete", "/sdm/entities/Device/instances/{}"),
    ("post", "/sdm/entities/{}/iot/regenerate-key"),
    ("get", "/sdm/entities/{}/iot/details"),
]


@pytest.mark.parametrize("method, route", ROUTES)
@pytest.mark.parametrize("entity_id", OTHER)
def test_other_ids_are_refused(client, broker, method, route, entity_id):
    resp = getattr(client, method)(route.format(quote(entity_id, safe="")), json={"n": {"type": "Property", "value": 1}})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Invalid identifier"}
    assert broker == []


@pytest.mark.parametrize("entity_id", GOOD)
def test_well_formed_ids_are_asked(client, broker, entity_id):
    client.get(f"/sdm/entities/Device/instances/{quote(entity_id, safe='')}")
    assert broker[0][0][0].endswith(f"/ngsi-ld/v1/entities/{entity_id}")


@pytest.mark.parametrize("entity_type", ['Device"', "Dev?ice", "Device%23"])
def test_other_types_are_refused(client, broker, entity_type):
    assert client.get(f"/sdm/entities/{quote(entity_type, safe='')}/instances").status_code == 400
    assert broker == []


def test_preflight_is_not_judged(client):
    assert client.options("/sdm/entities/Device/instances/a%3Fb").status_code != 400


@pytest.mark.parametrize("entity_id", OTHER + ["urn:ngsi-ld:Device:x/../../subscriptions/s1", "x/y"])
def test_other_body_ids_are_refused_on_create(client, broker, monkeypatch, sdm, entity_id):
    monkeypatch.setattr(sdm, "_check_tenant_limits", lambda tenant, entity_type: True)
    resp = client.post("/sdm/entities/Building/instances", json={"id": entity_id, "name": "x"})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Invalid identifier"}
    assert broker == []
