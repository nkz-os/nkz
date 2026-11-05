"""Tests for GET /internal/tenants/<tenant>/users (Keycloak mocked)."""

import os
import sys

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import tenant_user_api as api  # noqa: E402

SECRET = "test-internal-secret"
HDR = {"X-Internal-Service-Secret": SECRET}


class FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


def _user(uid, tenant, email=None, locale=None, enabled=True, key="tenant_id"):
    attrs = {key: [tenant]}
    if locale:
        attrs["locale"] = [locale]
    return {"id": uid, "email": email or f"{uid}@example.invalid",
            "enabled": enabled, "attributes": attrs}


class FakeKeycloak:
    def __init__(self, users, roles, search_status=200, failing_roles=()):
        self.users = users
        self.roles = roles
        self.search_status = search_status
        self.failing_roles = set(failing_roles)
        self.search_params = []

    def get(self, url, headers=None, params=None, timeout=None):
        if url.endswith("/users"):
            self.search_params.append(dict(params or {}))
            if self.search_status != 200:
                return FakeResp(self.search_status, {})
            first = int((params or {}).get("first", 0))
            mx = int((params or {}).get("max", 500))
            key, _, val = (params or {}).get("q", "").partition(":")
            matched = [u for u in self.users
                       if u.get("attributes", {}).get(key) == [val]]
            return FakeResp(200, matched[first:first + mx])
        if url.endswith("/role-mappings/realm/composite"):
            uid = url.split("/users/")[1].split("/")[0]
            if uid in self.failing_roles:
                return FakeResp(500, {})
            return FakeResp(200, [{"name": r} for r in self.roles.get(uid, [])])
        raise AssertionError(f"unexpected url {url}")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "INTERNAL_SERVICE_SECRET", SECRET, raising=False)
    monkeypatch.setattr(api.keycloak, "get_admin_token", lambda: "tok")
    api.app.config["TESTING"] = True
    return api.app.test_client()


def _install(monkeypatch, fake):
    monkeypatch.setattr(api.requests, "get", fake.get)


URL = "/internal/tenants/acme/users"


def test_requires_secret(client, monkeypatch):
    _install(monkeypatch, FakeKeycloak([], {}))
    assert client.get(URL + "?roles=TenantAdmin").status_code == 401
    assert client.get(URL + "?roles=TenantAdmin",
                      headers={"X-Internal-Service-Secret": "wrong"}).status_code == 401
    monkeypatch.setattr(api, "INTERNAL_SERVICE_SECRET", "")
    assert client.get(URL + "?roles=TenantAdmin", headers=HDR).status_code == 401
    assert client.get(URL + "?roles=TenantAdmin",
                      headers={"X-Internal-Service-Secret": ""}).status_code == 401


def test_non_ascii_header_is_401_not_500(client, monkeypatch):
    _install(monkeypatch, FakeKeycloak([], {}))
    r = client.get(URL + "?roles=TenantAdmin",
                   headers={"X-Internal-Service-Secret": "ñ"})
    assert r.status_code == 401


def test_filters_by_role_and_tenant(client, monkeypatch):
    users = [
        _user("u1", "acme", locale="eu"),
        _user("u2", "acme"),
        _user("u3", "acme"),
        _user("u4", "tenant-a"),
        _user("u5", "acme", key="tenant"),
    ]
    roles = {"u1": ["TenantAdmin", "default-roles"], "u2": ["TechnicalConsultant"],
             "u3": ["Farmer"], "u4": ["TenantAdmin"], "u5": ["Farmer"]}
    _install(monkeypatch, FakeKeycloak(users, roles))
    r = client.get(URL + "?roles=TenantAdmin,TechnicalConsultant", headers=HDR)
    assert r.status_code == 200
    got = {u["user_id"]: u for u in r.get_json()["users"]}
    assert set(got) == {"u1", "u2"}
    assert got["u1"]["locale"] == "eu"
    assert got["u2"]["locale"] is None
    assert got["u1"]["email"] == "u1@example.invalid"
    assert got["u1"]["enabled"] is True
    assert "TenantAdmin" in got["u1"]["roles"]


def test_role_lookup_failure_returns_502(client, monkeypatch):
    users = [_user("u1", "acme"), _user("u2", "acme")]
    roles = {"u1": ["TenantAdmin"], "u2": ["TenantAdmin"]}
    _install(monkeypatch, FakeKeycloak(users, roles, failing_roles=["u2"]))
    r = client.get(URL + "?roles=TenantAdmin", headers=HDR)
    assert r.status_code == 502
    assert "users" not in r.get_json()


def test_search_failure_returns_502(client, monkeypatch):
    _install(monkeypatch, FakeKeycloak([], {}, search_status=500))
    assert client.get(URL + "?roles=TenantAdmin", headers=HDR).status_code == 502


def test_admin_token_failure_returns_502(client, monkeypatch):
    monkeypatch.setattr(api.keycloak, "get_admin_token", lambda: None)
    assert client.get(URL + "?roles=TenantAdmin", headers=HDR).status_code == 502


@pytest.mark.parametrize("tenant", ["Acme", "a.b", "a%20b", "acme%0A"])
def test_invalid_tenant_rejected(client, monkeypatch, tenant):
    _install(monkeypatch, FakeKeycloak([], {}))
    r = client.get(f"/internal/tenants/{tenant}/users?roles=TenantAdmin", headers=HDR)
    assert r.status_code == 400


def test_missing_roles_rejected(client, monkeypatch):
    _install(monkeypatch, FakeKeycloak([], {}))
    assert client.get(URL, headers=HDR).status_code == 400


def test_paginates(client, monkeypatch):
    users = [_user(f"u{i}", "acme") for i in range(501)]
    roles = {u["id"]: ["TenantAdmin"] for u in users}
    fake = FakeKeycloak(users, roles)
    _install(monkeypatch, fake)
    r = client.get(URL + "?roles=TenantAdmin", headers=HDR)
    assert r.status_code == 200
    assert len(r.get_json()["users"]) == 501
    tid = [p for p in fake.search_params if p["q"] == "tenant_id:acme"]
    assert [p["first"] for p in tid] == [0, 500]
    assert all(p["max"] == 500 for p in fake.search_params)


def test_legacy_tenant_attribute_found_once(client, monkeypatch):
    users = [_user("u1", "acme", key="tenant"), _user("u2", "acme")]
    both = _user("u3", "acme")
    both["attributes"]["tenant"] = ["acme"]
    users.append(both)
    roles = {u["id"]: ["TenantAdmin"] for u in users}
    fake = FakeKeycloak(users, roles)
    _install(monkeypatch, fake)
    r = client.get(URL + "?roles=TenantAdmin", headers=HDR)
    assert r.status_code == 200
    ids = sorted(u["user_id"] for u in r.get_json()["users"])
    assert ids == ["u1", "u2", "u3"]
    assert {p["q"] for p in fake.search_params} == {"tenant_id:acme", "tenant:acme"}


def test_legacy_search_failure_returns_502(client, monkeypatch):
    fake = FakeKeycloak([_user("u1", "acme")], {"u1": ["TenantAdmin"]})
    orig = fake.get

    def get(url, headers=None, params=None, timeout=None):
        if params and params.get("q") == "tenant:acme":
            return FakeResp(500, {})
        return orig(url, headers=headers, params=params, timeout=timeout)

    monkeypatch.setattr(api.requests, "get", get)
    assert client.get(URL + "?roles=TenantAdmin", headers=HDR).status_code == 502


def test_conflicting_attributes_use_primary_owner(client, monkeypatch):
    users = [_user("u1", "other"), _user("u2", "acme")]
    users[0]["attributes"]["tenant"] = ["acme"]
    roles = {"u1": ["TenantAdmin"], "u2": ["TenantAdmin"]}
    _install(monkeypatch, FakeKeycloak(users, roles))
    r = client.get(URL + "?roles=TenantAdmin", headers=HDR)
    assert r.status_code == 200
    assert [u["user_id"] for u in r.get_json()["users"]] == ["u2"]


def test_user_without_id_returns_502(client, monkeypatch):
    u = _user("u1", "acme")
    del u["id"]
    _install(monkeypatch, FakeKeycloak([u], {}))
    r = client.get(URL + "?roles=TenantAdmin", headers=HDR)
    assert r.status_code == 502
