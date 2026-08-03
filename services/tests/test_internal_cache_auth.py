"""Auth contract for POST /internal/cache/invalidate.

Regression test for the fix that adds X-Internal-Service-Secret enforcement
to this route (previously reachable by any anonymous caller with network
reach to the gateway — see test_gateway_characterization.py's now-empty
SUSPICIOUS_UNAUTH_2XX for the prior finding).
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


def test_cache_invalidate_without_secret_returns_401(gateway, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "correct-horse-battery-staple")
    client = gateway.app.test_client()

    resp = client.post("/internal/cache/invalidate", json={"key": "routes"})

    assert resp.status_code == 401


def test_cache_invalidate_with_wrong_secret_returns_401(gateway, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "correct-horse-battery-staple")
    client = gateway.app.test_client()

    resp = client.post(
        "/internal/cache/invalidate",
        json={"key": "routes"},
        headers={"X-Internal-Service-Secret": "wrong-secret"},
    )

    assert resp.status_code == 401


def test_cache_invalidate_with_correct_secret_is_not_401(gateway, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "correct-horse-battery-staple")
    client = gateway.app.test_client()

    resp = client.post(
        "/internal/cache/invalidate",
        json={"key": "routes"},
        headers={"X-Internal-Service-Secret": "correct-horse-battery-staple"},
    )

    assert resp.status_code != 401
    assert resp.status_code == 200


def test_cache_invalidate_unset_secret_env_returns_401(gateway, monkeypatch):
    # If INTERNAL_SERVICE_SECRET isn't configured server-side at all, the
    # route must fail closed (401), never fall open to 200.
    monkeypatch.delenv("INTERNAL_SERVICE_SECRET", raising=False)
    client = gateway.app.test_client()

    resp = client.post(
        "/internal/cache/invalidate",
        json={"key": "routes"},
        headers={"X-Internal-Service-Secret": ""},
    )

    assert resp.status_code == 401
