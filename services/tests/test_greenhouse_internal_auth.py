"""Auth contract for POST /api/greenhouse/internal/<path>.

Regression test for the fix that makes the gateway actually validate
X-Internal-Service-Secret (via hmac.compare_digest against
INTERNAL_SERVICE_SECRET) on greenhouse internal routes, instead of just
checking the header is present. Previously any non-empty header value
passed the gateway hop unchecked (the greenhouse-dt backend still validated
it, but the gateway should too — defense in depth).
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
    """Import the gateway module with mandatory env vars mocked."""
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
    """Deterministic stand-in for a ``requests.Response`` from greenhouse-dt."""

    status_code = 599
    text = ""
    content = b""
    headers: dict = {}

    def json(self):
        return {}


def test_greenhouse_internal_without_secret_returns_401(gateway, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "correct-horse-battery-staple")
    client = gateway.app.test_client()

    resp = client.post("/api/greenhouse/internal/foo", json={})

    assert resp.status_code == 401


def test_greenhouse_internal_with_blank_secret_returns_401(gateway, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "correct-horse-battery-staple")
    client = gateway.app.test_client()

    resp = client.post(
        "/api/greenhouse/internal/foo",
        json={},
        headers={"X-Internal-Service-Secret": ""},
    )

    assert resp.status_code == 401


def test_greenhouse_internal_with_wrong_secret_returns_401(gateway, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "correct-horse-battery-staple")
    client = gateway.app.test_client()

    resp = client.post(
        "/api/greenhouse/internal/foo",
        json={},
        headers={"X-Internal-Service-Secret": "wrong-secret"},
    )

    assert resp.status_code == 401


def test_greenhouse_internal_unset_secret_env_returns_401(gateway, monkeypatch):
    # If INTERNAL_SERVICE_SECRET isn't configured server-side at all, the
    # route must fail closed (401), never fall open just because the caller
    # sent a non-empty header (the pre-fix behavior).
    monkeypatch.delenv("INTERNAL_SERVICE_SECRET", raising=False)
    client = gateway.app.test_client()

    resp = client.post(
        "/api/greenhouse/internal/foo",
        json={},
        headers={"X-Internal-Service-Secret": "anything-non-empty"},
    )

    assert resp.status_code == 401


def test_greenhouse_internal_with_correct_secret_is_not_401(gateway, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "correct-horse-battery-staple")
    fake_call = MagicMock(return_value=_FakeUpstreamResponse())
    monkeypatch.setattr(gateway.requests, "request", fake_call)
    client = gateway.app.test_client()

    resp = client.post(
        "/api/greenhouse/internal/foo",
        json={},
        headers={"X-Internal-Service-Secret": "correct-horse-battery-staple"},
    )

    # Passed the gateway's auth gate; the fake upstream returns the
    # sentinel 599, proving the request reached the proxy/forward step.
    assert resp.status_code != 401
    assert resp.status_code == 599
