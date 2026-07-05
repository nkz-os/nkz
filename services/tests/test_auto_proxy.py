"""Tests for api-gateway auto_proxy_module routing helpers."""

import importlib
import os
import sys
from unittest.mock import MagicMock

import pytest

_services_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_common_dir = os.path.join(_services_dir, "common")
_api_gateway_dir = os.path.join(_services_dir, "api-gateway")

for _p in (_services_dir, _common_dir, _api_gateway_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def gw(monkeypatch):
    monkeypatch.setenv("ORION_URL", "http://orion-test:1026")
    monkeypatch.setenv("KEYCLOAK_URL", "http://keycloak-test:8080/auth")
    monkeypatch.setenv("CONTEXT_URL", "http://context-test/ngsi-context.jsonld")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-for-testing-only")
    monkeypatch.setenv("HMAC_SECRET", "test-hmac-secret")
    monkeypatch.setenv("KEYCLOAK_REALM", "nekazari")
    monkeypatch.setenv("TRUST_API_GATEWAY", "false")
    monkeypatch.setenv("ALLOW_JWT_FALLBACK", "false")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pass@localhost:5432/db")

    mock_psycopg2 = MagicMock()
    sys.modules["psycopg2"] = mock_psycopg2
    sys.modules["psycopg2.extras"] = MagicMock()

    import keycloak_auth

    importlib.reload(keycloak_auth)
    import fiware_api_gateway as gateway

    importlib.reload(gateway)
    return gateway


class _Resp:
    content = b'{"ok": true}'
    status_code = 200
    headers = {"Content-Type": "application/json"}


def _patch_auth(monkeypatch, gw, tenant="montiko"):
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(
        gw, "validate_jwt_token", lambda t: {"tenant_id": tenant, "sub": "user-1"}
    )
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: tenant)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "KEYCLOAK_AUTH_AVAILABLE", True)
    monkeypatch.setattr(gw, "generate_hmac_signature", lambda t, ten: "sig123")


def test_match_module_route_longest_prefix(gw):
    routes = {
        "/api/weather": {
            "backend_service": "http://short:8080",
            "backend_mount": "/api/weather",
            "module_id": "weather",
            "requires_auth": True,
        },
        "/api/weather-map": {
            "backend_service": "http://weather-map-backend:8080",
            "backend_mount": "/api/weather-map",
            "module_id": "weather-map",
            "requires_auth": True,
        },
    }
    match, prefix = gw._match_module_route("/api/weather-map/zones/p1", routes)
    assert match["module_id"] == "weather-map"
    assert prefix == "/api/weather-map"


def test_build_module_proxy_url_with_backend_mount(gw):
    match = {
        "backend_service": "http://weather-map-backend:8080",
        "backend_mount": "/api/weather-map",
    }
    url = gw._build_module_proxy_url(
        "/api/weather-map/zones/p1", "/api/weather-map", match
    )
    assert url == "http://weather-map-backend:8080/api/weather-map/zones/p1"


def test_build_module_proxy_url_soil_mount(gw):
    match = {
        "backend_service": "http://soil-module-service:8000",
        "backend_mount": "/v1/soil",
    }
    url = gw._build_module_proxy_url("/api/soil/profile", "/api/soil", match)
    assert url == "http://soil-module-service:8000/v1/soil/profile"


def test_build_module_proxy_url_soil_strip_duplicate_prefix(gw):
    match = {
        "backend_service": "http://soil-module-service:8000",
        "backend_mount": "/v1/soil",
        "strip_remainder_prefix": "/v1/soil",
    }
    url = gw._build_module_proxy_url("/api/soil/v1/soil/profile", "/api/soil", match)
    assert url == "http://soil-module-service:8000/v1/soil/profile"


def test_auto_proxy_requires_auth_returns_401(gw, monkeypatch):
    routes = {
        "/api/weather-map": {
            "backend_service": "http://weather-map-backend:8080",
            "backend_mount": "/api/weather-map",
            "module_id": "weather-map",
            "requires_auth": True,
        }
    }
    monkeypatch.setattr(gw, "_refresh_route_registry", lambda: routes)
    monkeypatch.setattr(gw, "get_request_token", lambda: None)

    with gw.app.test_request_context("/api/weather-map/zones/p1", method="GET"):
        resp = gw.auto_proxy_module("weather-map/zones/p1")
    assert resp[1] == 401


def test_auto_proxy_public_route_forwards_without_token(gw, monkeypatch):
    routes = {
        "/api/graph/agriculture": {
            "backend_service": "http://bioorchestrator-api-service:8420",
            "backend_mount": "/api/graph/agriculture",
            "module_id": "bioorchestrator",
            "requires_auth": False,
        }
    }
    captured = {}

    def fake_proxy(url, *, requires_auth):
        captured["url"] = url
        captured["requires_auth"] = requires_auth
        return gw.jsonify({"sites": []}), 200

    monkeypatch.setattr(gw, "_refresh_route_registry", lambda: routes)
    monkeypatch.setattr(gw, "get_request_token", lambda: None)
    monkeypatch.setattr(gw, "_proxy_authenticated_request", fake_proxy)

    with gw.app.test_request_context(
        "/api/graph/agriculture/trial-sites", method="GET"
    ):
        resp = gw.auto_proxy_module("graph/agriculture/trial-sites")
    assert resp[1] == 200
    assert captured["requires_auth"] is False
    assert (
        captured["url"]
        == "http://bioorchestrator-api-service:8420/api/graph/agriculture/trial-sites"
    )


def test_auto_proxy_forwards_with_hmac(gw, monkeypatch):
    routes = {
        "/api/weather-map": {
            "backend_service": "http://weather-map-backend:8080",
            "backend_mount": "/api/weather-map",
            "module_id": "weather-map",
            "requires_auth": True,
        }
    }
    captured = {}

    def fake_request(method, url, headers=None, params=None, data=None, **kwargs):
        captured.update(method=method, url=url, headers=headers)
        return _Resp()

    monkeypatch.setattr(gw, "_refresh_route_registry", lambda: routes)
    _patch_auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "request", fake_request)

    with gw.app.test_request_context("/api/weather-map/zones/p1", method="GET"):
        resp = gw.auto_proxy_module("weather-map/zones/p1")
    status = resp[1] if isinstance(resp, tuple) else resp.status_code
    assert status == 200
    assert captured["url"] == "http://weather-map-backend:8080/api/weather-map/zones/p1"
    assert captured["headers"]["X-Auth-Signature"] == "sig123"
    assert captured["headers"]["X-Tenant-ID"] == "montiko"


def test_auto_proxy_unknown_prefix_returns_404(gw, monkeypatch):
    monkeypatch.setattr(gw, "_refresh_route_registry", lambda: {})
    with gw.app.test_request_context("/api/unknown/foo", method="GET"):
        resp = gw.auto_proxy_module("unknown/foo")
    assert resp[1] == 404
