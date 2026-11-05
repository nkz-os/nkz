"""The same-site guard is installed in sdm-integration, which has a direct ingress route."""

import functools
import os
import sys

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")
for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

URL = "/sdm/entities/Device/instances"


@pytest.fixture(scope="module")
def client():
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

    test_client = sdm_api.app.test_client()
    test_client.set_cookie("nkz_token", "t")
    yield test_client

    sys.modules.pop("sdm_api", None)
    if saved is not None:
        sys.modules["sdm_api"] = saved
    mp.undo()


def _post(client, **headers):
    # An empty body stops at validation, so nothing reaches Orion.
    return client.post(URL, json={}, headers=headers)


def test_cross_site_cookie_mutation_rejected(client):
    r = _post(client, Origin="https://evil.invalid")
    assert r.status_code == 403
    assert r.get_json() == {"error": "Cross-site request rejected"}


def test_bearer_not_blocked(client):
    r = _post(client, Origin="https://evil.invalid", Authorization="Bearer t")
    assert r.status_code != 403


def test_allowed_origin_not_blocked(client):
    assert _post(client, Origin="https://app.invalid").status_code != 403
