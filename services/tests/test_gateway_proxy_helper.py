"""Tests for the extracted gateway auth/proxy helpers (WS1.f1, increment 1).

Covers ``resolve_request_auth`` and ``safe_proxy_response`` in
services/api-gateway/fiware_api_gateway.py, plus end-to-end coverage on one
migrated route (``/ngsi-ld/v1/entities``) proving the JWT happy path forwards
the expected tenant headers to the upstream call, and that a missing token
still 401s.

Follows the exact fixture / auth-mocking pattern already used in
test_gateway_hardening.py (gateway import fixture) and test_auto_proxy.py
(_patch_auth-style monkeypatching of module-level auth functions).
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

_services_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (
    _services_dir,
    os.path.join(_services_dir, "common"),
    os.path.join(_services_dir, "api-gateway"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def gateway(monkeypatch):
    """Import the gateway module with mandatory env vars mocked (same as
    test_gateway_characterization.py / test_gateway_hardening.py)."""
    monkeypatch.setenv("ORION_URL", "http://orion-test:1026")
    monkeypatch.setenv("KEYCLOAK_URL", "http://keycloak-test:8080")
    monkeypatch.setenv("CONTEXT_URL", "http://context-test/ngsi-context.jsonld")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-for-testing-only")
    monkeypatch.setenv("HMAC_SECRET", "test-hmac-secret")
    monkeypatch.setenv("KEYCLOAK_REALM", "nekazari")
    monkeypatch.setenv("TRUST_API_GATEWAY", "false")
    monkeypatch.setenv("ALLOW_JWT_FALLBACK", "false")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pass@localhost:5432/db")

    sys.modules["psycopg2"] = MagicMock()
    sys.modules["psycopg2.extras"] = MagicMock()

    import importlib

    import keycloak_auth

    importlib.reload(keycloak_auth)

    import fiware_api_gateway as gw

    importlib.reload(gw)
    return gw


class _FakeUpstreamResponse:
    def __init__(self, status_code=200, content=b'{"ok": true}', headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {"Content-Type": "application/json"}
        self.text = content.decode() if isinstance(content, bytes) else str(content)

    def json(self):
        return {}


def _patch_jwt_auth(monkeypatch, gw, tenant="montiko", roles=None):
    """Bypass real Keycloak/JWT validation the same way test_auto_proxy.py
    does, so we can exercise resolve_request_auth's JWT branch without
    constructing a real signed token."""
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(
        gw,
        "validate_jwt_token",
        lambda t: {"tenant_id": tenant, "sub": "user-1", "realm_access": {"roles": roles or []}},
    )
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: tenant)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "generate_hmac_signature", lambda t, ten: "sig123")


# =============================================================================
# End-to-end: migrated route /ngsi-ld/v1/entities (header_mode="canonical")
# =============================================================================


def test_entities_get_forwards_tenant_headers_with_valid_jwt(gateway, monkeypatch):
    """A valid JWT on GET /ngsi-ld/v1/entities must forward X-Tenant-ID and
    NGSILD-Tenant equal to the token's resolved tenant to the upstream call —
    the exact behavior the removed inline auth block guaranteed."""
    tenant = "montiko"
    _patch_jwt_auth(monkeypatch, gateway, tenant=tenant)

    fake_response = _FakeUpstreamResponse()
    fake_get = MagicMock(return_value=fake_response)
    monkeypatch.setattr(gateway.requests, "get", fake_get)

    client = gateway.app.test_client()
    resp = client.get("/ngsi-ld/v1/entities")

    assert resp.status_code == 200
    assert fake_get.called
    _, kwargs = fake_get.call_args
    forwarded_headers = kwargs["headers"]
    assert forwarded_headers["X-Tenant-ID"] == tenant
    assert forwarded_headers["NGSILD-Tenant"] == tenant
    assert forwarded_headers["Fiware-Service"] == tenant
    # canonical header_mode also carries the HMAC signature (unlike raw mode)
    assert forwarded_headers["X-Auth-Signature"] == "sig123"


def test_entities_without_token_returns_401(gateway, monkeypatch):
    """No Authorization header -> 401, before any upstream call is made."""
    monkeypatch.setattr(gateway, "get_request_token", lambda: None)
    fake_get = MagicMock()
    monkeypatch.setattr(gateway.requests, "get", fake_get)

    client = gateway.app.test_client()
    resp = client.get("/ngsi-ld/v1/entities")

    assert resp.status_code == 401
    assert "error" in resp.get_json()
    fake_get.assert_not_called()


def test_entities_role_pro_expired_blocks_mutation(gateway, monkeypatch):
    """block_expired_mutations must still 403 a POST from a role_pro_expired
    user without ever reaching the upstream call."""
    _patch_jwt_auth(monkeypatch, gateway, tenant="montiko", roles=["role_pro_expired"])
    fake_post = MagicMock()
    monkeypatch.setattr(gateway.requests, "post", fake_post)

    client = gateway.app.test_client()
    resp = client.post("/ngsi-ld/v1/entities", json={"type": "AgriParcel", "id": "urn:x"})

    assert resp.status_code == 403
    fake_post.assert_not_called()


# =============================================================================
# End-to-end: migrated route /api/timeseries/<path> (header_mode="raw")
# =============================================================================


def test_timeseries_proxy_forwards_raw_tenant_headers(gateway, monkeypatch):
    """timeseries_proxy uses header_mode='raw': NGSILD-Tenant/Fiware-Service
    set directly from the resolved tenant, and NO X-Auth-Signature (unlike
    the canonical routes) since timeseries-reader doesn't use it."""
    tenant = "montiko"
    _patch_jwt_auth(monkeypatch, gateway, tenant=tenant)

    fake_response = _FakeUpstreamResponse()
    fake_get = MagicMock(return_value=fake_response)
    monkeypatch.setattr(gateway.requests, "get", fake_get)

    client = gateway.app.test_client()
    resp = client.get("/api/timeseries/data/foo")

    assert resp.status_code == 200
    _, kwargs = fake_get.call_args
    forwarded_headers = kwargs["headers"]
    assert forwarded_headers["NGSILD-Tenant"] == tenant
    assert forwarded_headers["Fiware-Service"] == tenant
    assert forwarded_headers["X-Tenant-ID"] == tenant
    assert "X-Auth-Signature" not in forwarded_headers


def test_timeseries_proxy_without_token_returns_401(gateway, monkeypatch):
    monkeypatch.setattr(gateway, "get_request_token", lambda: None)
    fake_get = MagicMock()
    monkeypatch.setattr(gateway.requests, "get", fake_get)

    client = gateway.app.test_client()
    resp = client.get("/api/timeseries/data/foo")

    assert resp.status_code == 401
    fake_get.assert_not_called()


# =============================================================================
# Direct unit coverage of resolve_request_auth's per-route flag behavior
# =============================================================================


def test_resolve_request_auth_missing_token_error(gateway):
    with gateway.app.test_request_context("/whatever", method="GET"):
        tenant, headers, err = gateway.resolve_request_auth()
    assert tenant is None
    assert headers is None
    response, status = err
    assert status == 401


def test_resolve_request_auth_allow_pat_false_falls_back_to_jwt_rejection(gateway, monkeypatch):
    """subscriptions() passes allow_pat=False. A PAT-shaped token must NOT be
    resolved as a PAT — it must fall into the JWT branch and fail there,
    exactly like today's subscriptions() (which never checks is_pat_token)."""
    monkeypatch.setattr(gateway, "get_request_token", lambda: "nkz_pat_something")
    monkeypatch.setattr(gateway, "is_pat_token", lambda t: True)
    monkeypatch.setattr(gateway, "validate_jwt_token", lambda t: None)

    with gateway.app.test_request_context("/ngsi-ld/v1/subscriptions", method="GET"):
        tenant, headers, err = gateway.resolve_request_auth(allow_pat=False, header_mode="canonical")

    assert tenant is None
    assert headers is None
    response, status = err
    assert status == 401
    assert response.get_json()["error"] == "Invalid or expired token"


def test_resolve_request_auth_pat_rate_limit_flag_respected(gateway, monkeypatch):
    """entity_by_id/entities pass pat_rate_limit=False: rate_limit must NOT
    be called at all in the PAT branch when the flag is off."""
    monkeypatch.setattr(gateway, "get_request_token", lambda: "nkz_pat_something")
    monkeypatch.setattr(gateway, "is_pat_token", lambda t: True)
    monkeypatch.setattr(gateway, "obtain_gateway_service_jwt", lambda: "svc-jwt")
    rate_limit_mock = MagicMock(return_value=True)
    monkeypatch.setattr(gateway, "rate_limit", rate_limit_mock)

    with gateway.app.test_request_context("/ngsi-ld/v1/entities", method="GET"):
        gateway.g.pat_tenant_id = "montiko"
        tenant, headers, err = gateway.resolve_request_auth(
            header_mode="canonical",
            pat_rate_limit=False,
            pat_tenant_missing_message="PAT tenant not resolved",
        )

    assert err is None
    assert tenant == "montiko"
    rate_limit_mock.assert_not_called()


def test_resolve_request_auth_pat_missing_tenant_message_is_parameterized(gateway, monkeypatch):
    """entity_by_id/entities use 'PAT tenant not resolved'; timeseries_proxy
    uses the default 'Invalid or expired PAT'. Both must be reproducible
    verbatim via the pat_tenant_missing_message flag."""
    monkeypatch.setattr(gateway, "get_request_token", lambda: "nkz_pat_something")
    monkeypatch.setattr(gateway, "is_pat_token", lambda t: True)

    with gateway.app.test_request_context("/ngsi-ld/v1/entities", method="GET"):
        # g.pat_tenant_id deliberately left unset -> falsy -> tenant-missing path
        _, _, err = gateway.resolve_request_auth(
            header_mode="canonical", pat_tenant_missing_message="PAT tenant not resolved"
        )
    assert err[0].get_json()["error"] == "PAT tenant not resolved"

    with gateway.app.test_request_context("/api/timeseries/x", method="GET"):
        _, _, err = gateway.resolve_request_auth(header_mode="raw")
    assert err[0].get_json()["error"] == "Invalid or expired PAT"


def test_resolve_request_auth_header_mode_raw_has_no_signature_or_link(gateway, monkeypatch):
    """header_mode='raw' must not call generate_hmac_signature nor add the
    Link/Accept/Fiware-ServicePath headers that inject_fiware_headers adds —
    proving raw mode really is the minimal timeseries_proxy shape."""
    _patch_jwt_auth(monkeypatch, gateway, tenant="montiko")
    hmac_mock = MagicMock(return_value="sig123")
    monkeypatch.setattr(gateway, "generate_hmac_signature", hmac_mock)

    with gateway.app.test_request_context("/api/timeseries/x", method="GET"):
        tenant, headers, err = gateway.resolve_request_auth(header_mode="raw", jwt_rate_limit=False)

    assert err is None
    assert tenant == "montiko"
    hmac_mock.assert_not_called()
    assert "X-Auth-Signature" not in headers
    assert "Link" not in headers
    assert "Fiware-ServicePath" not in headers
