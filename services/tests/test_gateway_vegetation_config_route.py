"""Tests for api-gateway proxy routes: /api/vegetation/config*

Before this change, the browser-facing Copernicus BYOK settings panel
(GET/PUT/DELETE /api/vegetation/config, GET .../credentials-status,
GET .../usage) 404'd — the vegetation backend exposed these endpoints
but the gateway had no explicit @app.route for them (no catch-all here).

These tests assert:
  - the routes exist (no more 404)
  - unauthenticated requests are rejected (401), not silently 404'd
  - authenticated requests forward Authorization, X-Tenant-ID and a
    well-formed X-Auth-Signature (`{64-char hex}:{timestamp}`) to the
    vegetation-prime backend, using the REAL generate_hmac_signature
    (not mocked) so the format assertion is meaningful
  - PUT request bodies are preserved end-to-end
"""

import importlib
import json
import os
import re
import sys
from unittest.mock import MagicMock

import pytest

_services_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_common_dir = os.path.join(_services_dir, "common")
_api_gateway_dir = os.path.join(_services_dir, "api-gateway")

for _p in (_services_dir, _common_dir, _api_gateway_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

HMAC_SIG_RE = re.compile(r"^[0-9a-f]{64}:\d+$")


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
    monkeypatch.setenv(
        "VEGETATION_API_URL", "http://vegetation-prime-api-service:8000"
    )

    mock_psycopg2 = MagicMock()
    sys.modules["psycopg2"] = mock_psycopg2
    sys.modules["psycopg2.extras"] = MagicMock()

    import keycloak_auth

    importlib.reload(keycloak_auth)
    import fiware_api_gateway as gateway

    importlib.reload(gateway)
    return gateway


class _Resp:
    def __init__(self, content=b'{"ok": true}', status_code=200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "application/json"}


def _patch_auth(monkeypatch, gw, tenant="montiko", token="tok"):
    """Authenticate as a real user — but use the REAL generate_hmac_signature
    (not mocked) so we can assert on its actual output format.
    """
    monkeypatch.setattr(gw, "get_request_token", lambda: token)
    monkeypatch.setattr(
        gw, "validate_jwt_token", lambda t: {"tenant_id": tenant, "sub": "user-1"}
    )
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: tenant)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "KEYCLOAK_AUTH_AVAILABLE", True)


def _capture_requests(monkeypatch, gw, resp=None):
    captured = {}

    def fake_request(method, url, headers=None, params=None, data=None, **kwargs):
        captured.update(method=method, url=url, headers=headers, params=params, data=data)
        return resp or _Resp()

    monkeypatch.setattr(gw.requests, "request", fake_request)
    return captured


# ---------------------------------------------------------------------------
# Routes exist (no 404) and require auth
# ---------------------------------------------------------------------------
class TestVegetationConfigRoutesExist:
    def test_get_config_without_auth_returns_401_not_404(self, gw, monkeypatch):
        monkeypatch.setattr(gw, "get_request_token", lambda: None)
        client = gw.app.test_client()
        resp = client.get("/api/vegetation/config")
        assert resp.status_code == 401

    def test_get_usage_without_auth_returns_401_not_404(self, gw, monkeypatch):
        monkeypatch.setattr(gw, "get_request_token", lambda: None)
        client = gw.app.test_client()
        resp = client.get("/api/vegetation/config/usage")
        assert resp.status_code == 401

    def test_get_credentials_status_without_auth_returns_401_not_404(self, gw, monkeypatch):
        monkeypatch.setattr(gw, "get_request_token", lambda: None)
        client = gw.app.test_client()
        resp = client.get("/api/vegetation/config/credentials-status")
        assert resp.status_code == 401

    def test_delete_config_without_auth_returns_401_not_404(self, gw, monkeypatch):
        monkeypatch.setattr(gw, "get_request_token", lambda: None)
        client = gw.app.test_client()
        resp = client.delete("/api/vegetation/config")
        assert resp.status_code == 401

    def test_put_config_without_auth_returns_401_not_404(self, gw, monkeypatch):
        monkeypatch.setattr(gw, "get_request_token", lambda: None)
        client = gw.app.test_client()
        resp = client.put("/api/vegetation/config", json={"copernicus_client_id": "x"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# HMAC + auth header forwarding
# ---------------------------------------------------------------------------
class TestVegetationConfigHmacForwarding:
    def test_get_config_forwards_hmac_and_auth_headers(self, gw, monkeypatch):
        _patch_auth(monkeypatch, gw, tenant="montiko", token="tok-abc")
        captured = _capture_requests(monkeypatch, gw)

        client = gw.app.test_client()
        resp = client.get("/api/vegetation/config")

        assert resp.status_code == 200
        assert (
            captured["url"]
            == "http://vegetation-prime-api-service:8000/api/vegetation/config"
        )
        headers = captured["headers"]
        assert headers["Authorization"] == "Bearer tok-abc"
        assert headers["X-Tenant-ID"] == "montiko"
        assert "X-Auth-Signature" in headers
        assert HMAC_SIG_RE.match(headers["X-Auth-Signature"]), (
            f"X-Auth-Signature not well-formed hex:timestamp: {headers['X-Auth-Signature']!r}"
        )

    def test_get_usage_forwards_hmac_and_auth_headers(self, gw, monkeypatch):
        _patch_auth(monkeypatch, gw, tenant="montiko", token="tok-abc")
        captured = _capture_requests(monkeypatch, gw)

        client = gw.app.test_client()
        resp = client.get("/api/vegetation/config/usage")

        assert resp.status_code == 200
        assert (
            captured["url"]
            == "http://vegetation-prime-api-service:8000/api/vegetation/config/usage"
        )
        headers = captured["headers"]
        assert headers["Authorization"] == "Bearer tok-abc"
        assert headers["X-Tenant-ID"] == "montiko"
        assert HMAC_SIG_RE.match(headers["X-Auth-Signature"])

    def test_get_credentials_status_forwards_hmac_and_auth_headers(self, gw, monkeypatch):
        _patch_auth(monkeypatch, gw, tenant="montiko", token="tok-abc")
        captured = _capture_requests(monkeypatch, gw)

        client = gw.app.test_client()
        resp = client.get("/api/vegetation/config/credentials-status")

        assert resp.status_code == 200
        assert (
            captured["url"]
            == "http://vegetation-prime-api-service:8000/api/vegetation/config/credentials-status"
        )
        headers = captured["headers"]
        assert headers["Authorization"] == "Bearer tok-abc"
        assert headers["X-Tenant-ID"] == "montiko"
        assert HMAC_SIG_RE.match(headers["X-Auth-Signature"])

    def test_delete_config_forwards_hmac_and_auth_headers(self, gw, monkeypatch):
        _patch_auth(monkeypatch, gw, tenant="montiko", token="tok-abc")
        captured = _capture_requests(
            monkeypatch, gw, resp=_Resp(content=b"", status_code=204)
        )

        client = gw.app.test_client()
        resp = client.delete("/api/vegetation/config")

        assert resp.status_code == 204
        assert captured["method"] == "DELETE"
        headers = captured["headers"]
        assert headers["Authorization"] == "Bearer tok-abc"
        assert headers["X-Tenant-ID"] == "montiko"
        assert HMAC_SIG_RE.match(headers["X-Auth-Signature"])

    def test_signature_differs_per_tenant(self, gw, monkeypatch):
        """Sanity check the signature is not a static/mocked stand-in."""
        _patch_auth(monkeypatch, gw, tenant="montiko", token="tok-abc")
        captured_a = _capture_requests(monkeypatch, gw)
        gw.app.test_client().get("/api/vegetation/config")
        sig_a = captured_a["headers"]["X-Auth-Signature"].split(":")[0]

        _patch_auth(monkeypatch, gw, tenant="other-tenant", token="tok-abc")
        captured_b = _capture_requests(monkeypatch, gw)
        gw.app.test_client().get("/api/vegetation/config")
        sig_b = captured_b["headers"]["X-Auth-Signature"].split(":")[0]

        assert sig_a != sig_b


# ---------------------------------------------------------------------------
# PUT body pass-through
# ---------------------------------------------------------------------------
class TestVegetationConfigPutBodyPassthrough:
    def test_put_config_preserves_request_body(self, gw, monkeypatch):
        _patch_auth(monkeypatch, gw, tenant="montiko", token="tok-abc")
        captured = _capture_requests(monkeypatch, gw)

        payload = {
            "copernicus_client_id": "sh-client-id-123",
            "copernicus_client_secret": "sh-secret-xyz",
        }
        client = gw.app.test_client()
        resp = client.put("/api/vegetation/config", json=payload)

        assert resp.status_code == 200
        assert captured["method"] == "PUT"
        assert (
            captured["url"]
            == "http://vegetation-prime-api-service:8000/api/vegetation/config"
        )
        forwarded_body = json.loads(captured["data"])
        assert forwarded_body == payload
        headers = captured["headers"]
        assert headers["Authorization"] == "Bearer tok-abc"
        assert headers["X-Tenant-ID"] == "montiko"
        assert HMAC_SIG_RE.match(headers["X-Auth-Signature"])
