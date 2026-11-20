"""Entity creation in sdm-integration is checked against the closed type list."""

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

from common.entity_type_guard import EntityTypeGuard, find_config_dir, load_accepted  # noqa: E402

REJECTION = {
    "error": "Entity type not accepted",
    "types": ["FooAsset"],
    "hint": "Accepted types are listed in config/entity-types of the platform repository",
}


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


def _set_mode(monkeypatch, sdm, enforce):
    monkeypatch.setattr(sdm, "ENTITY_TYPE_GUARD", EntityTypeGuard("sdm-integration", load_accepted(_REPO_ROOT / "config"), enforce))


def _no_outbound(monkeypatch, sdm):
    calls = []

    def fail(*a, **k):
        calls.append((a, k))
        raise AssertionError("outbound request made")

    for name in ("post", "get", "patch", "put", "delete", "request"):
        monkeypatch.setattr(sdm.requests, name, fail)
    return calls


def _limits_stub(monkeypatch, sdm, result):
    calls = []

    def stub(tenant, entity_type):
        calls.append((tenant, entity_type))
        return result

    monkeypatch.setattr(sdm, "_check_tenant_limits", stub)
    return calls


def test_not_enforcing_reaches_limits_check_and_warns(sdm, client, monkeypatch, caplog):
    _set_mode(monkeypatch, sdm, False)
    limits = _limits_stub(monkeypatch, sdm, False)
    with caplog.at_level(logging.WARNING):
        r = client.post("/sdm/entities/FooAsset/instances", json={"name": "x"})
    assert r.status_code == 403
    assert limits == [("tenant-a", "FooAsset")]
    assert any("entity_type_guard" in rec.getMessage() and rec.levelno == logging.WARNING for rec in caplog.records)


def test_enforcing_instance_rejected(sdm, client, monkeypatch):
    _set_mode(monkeypatch, sdm, True)
    limits = _limits_stub(monkeypatch, sdm, True)
    outbound = _no_outbound(monkeypatch, sdm)
    r = client.post("/sdm/entities/FooAsset/instances", json={"name": "x"})
    assert r.status_code == 422
    assert r.get_json() == REJECTION
    assert limits == []
    assert outbound == []


def test_enforcing_prefixed_sdm_name_rejected(sdm, client, monkeypatch):
    # nkz:Device would be stored under the platform namespace, not as the SDM type.
    _set_mode(monkeypatch, sdm, True)
    limits = _limits_stub(monkeypatch, sdm, True)
    outbound = _no_outbound(monkeypatch, sdm)
    r = client.post("/sdm/entities/nkz%3ADevice/instances", json={"name": "x"})
    assert r.status_code == 422
    assert r.get_json() == {**REJECTION, "types": ["nkz:Device"]}
    assert limits == []
    assert outbound == []


def test_enforcing_batch_rejected(sdm, client, monkeypatch):
    _set_mode(monkeypatch, sdm, True)
    limits = _limits_stub(monkeypatch, sdm, True)
    outbound = _no_outbound(monkeypatch, sdm)
    r = client.post("/sdm/entities/FooAsset/batch", json={"entities": [{"name": "a", "lat": 1, "lng": 2}]})
    assert r.status_code == 422
    assert r.get_json() == REJECTION
    assert limits == []
    assert outbound == []


def test_body_type_is_ignored(sdm, client, monkeypatch):
    _set_mode(monkeypatch, sdm, True)
    _limits_stub(monkeypatch, sdm, True)
    _no_outbound(monkeypatch, sdm)
    r = client.post("/sdm/entities/FooAsset/instances", json={"type": "Device"})
    assert r.status_code == 422


def test_enforcing_accepted_type_not_rejected(sdm, client, monkeypatch):
    _set_mode(monkeypatch, sdm, True)
    limits = _limits_stub(monkeypatch, sdm, False)
    r = client.post("/sdm/entities/Device/instances", json={"name": "x"})
    assert r.status_code == 403
    assert limits == [("tenant-a", "Device")]


@pytest.mark.parametrize(
    "method,url",
    [
        ("get", "/sdm/entities/FooAsset"),
        ("get", "/sdm/entities/FooAsset/instances"),
        ("get", "/sdm/entities/FooAsset/instances/urn:ngsi-ld:FooAsset:1"),
        ("patch", "/sdm/entities/FooAsset/instances/urn:ngsi-ld:FooAsset:1"),
        ("delete", "/sdm/entities/FooAsset/instances/urn:ngsi-ld:FooAsset:1"),
    ],
)
def test_other_routes_not_checked(sdm, client, monkeypatch, method, url):
    _set_mode(monkeypatch, sdm, True)
    kwargs = {"json": {"name": "x"}} if method == "patch" else {}
    r = getattr(client, method)(url, **kwargs)
    assert r.get_json() != REJECTION


def test_ships_off_by_default(sdm, monkeypatch):
    # A guard that enforces without being asked would reject writes that work today.
    monkeypatch.delenv("ENTITY_TYPE_GUARD_ENFORCE", raising=False)
    monkeypatch.delenv("ENTITY_TYPE_CONFIG_DIR", raising=False)
    guard = EntityTypeGuard.from_env("sdm-integration")
    assert guard.enforce is False
    assert guard.accepted is not None
    assert sdm.ENTITY_TYPE_GUARD.enforce is False


def test_dockerfile_copies_config_files():
    text = (_REPO_ROOT / "services/sdm-integration/Dockerfile").read_text(encoding="utf-8")
    assert "COPY config/entity-types /app/config/entity-types" in text
    assert "COPY config/ngsi-ld-context.json /app/config/ngsi-ld-context.json" in text


def test_find_config_dir_resolves_repo_config(monkeypatch):
    monkeypatch.delenv("ENTITY_TYPE_CONFIG_DIR", raising=False)
    assert find_config_dir() == (_REPO_ROOT / "config").resolve()
