"""The n8n proxy must carry no deployment of its own.

It used to gate on one hard-coded virtual host and, when a request arrived
without enough context, serve a hard-coded tenant. Both make the route useless
to anyone else installing this, and the second hands one tenant's workflows to
another the moment a second tenant exists.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

_services_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_services_dir, os.path.join(_services_dir, "common"),
           os.path.join(_services_dir, "api-gateway")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def gw(monkeypatch):
    for k, v in (
        ("ORION_URL", "http://orion-test:1026"),
        ("KEYCLOAK_URL", "http://keycloak-test:8080"),
        ("CONTEXT_URL", "http://context-test/ngsi-context.jsonld"),
        ("JWT_SECRET", "test-jwt-secret-for-testing-only"),
        ("HMAC_SECRET", "test-hmac-secret"),
        ("KEYCLOAK_REALM", "nekazari"),
        ("POSTGRES_URL", "postgresql://user:pass@localhost:5432/db"),
        ("N8N_PROXY_HOST", "automation.example.com"),
    ):
        monkeypatch.setenv(k, v)
    sys.modules["psycopg2"] = MagicMock()
    sys.modules["psycopg2.extras"] = MagicMock()
    import importlib

    import keycloak_auth
    importlib.reload(keycloak_auth)
    import fiware_api_gateway as g
    importlib.reload(g)
    return g


def test_no_default_names_a_deployment(gw):
    """Every knob here must be empty until an operator sets it."""
    assert gw.N8N_PROXY_HOST == "automation.example.com"   # from the fixture
    assert gw.N8N_DEFAULT_TENANT == ""
    assert gw.COOKIE_DOMAIN == "", "a cookie scoped to someone else's domain is dropped"
    assert gw.ZULIP_HOST == ""


def test_the_zulip_host_header_is_omitted_when_unset(gw):
    assert gw._zulip_host_header() == {}


def test_an_unconfigured_proxy_host_disables_the_route(gw, monkeypatch):
    monkeypatch.setattr(gw, "N8N_PROXY_HOST", "")
    client = gw.app.test_client()
    r = client.get("/tenant-a/", headers={"Host": "automation.example.com"})
    assert r.status_code == 404


def test_a_request_on_another_host_is_not_served(gw):
    client = gw.app.test_client()
    r = client.get("/tenant-a/", headers={"Host": "something-else.example.com"})
    assert r.status_code == 404


@pytest.mark.parametrize(
    "referer,expected",
    [
        ("https://automation.example.com/tenant-a/workflow/1", "tenant-a"),
        ("https://any-host.example/t1/", "t1"),          # host-independent
        ("https://automation.example.com/", ""),
        ("", ""),
        ("https://automation.example.com/Not_A_Tenant/", ""),
    ],
)
def test_the_tenant_comes_from_the_referer_path(gw, referer, expected):
    assert gw._n8n_tenant_from_referer(referer) == expected


def test_an_asset_request_without_context_is_refused(gw):
    """Guessing would serve one tenant's n8n to another."""
    client = gw.app.test_client()
    r = client.get("/assets/app.js", headers={"Host": "automation.example.com"})
    assert r.status_code == 404


def test_a_single_tenant_install_may_name_its_tenant(gw, monkeypatch):
    monkeypatch.setattr(gw, "N8N_DEFAULT_TENANT", "only-tenant")
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["url"] = url
        resp = MagicMock(status_code=200, headers={}, content=b"", raw=None)
        return resp

    monkeypatch.setattr(gw.requests, "request", fake_request, raising=False)
    monkeypatch.setattr(gw.requests, "get", lambda url, **k: fake_request("GET", url, **k), raising=False)
    client = gw.app.test_client()
    client.get("/assets/app.js", headers={"Host": "automation.example.com"})
    assert "only-tenant" in captured.get("url", ""), captured
