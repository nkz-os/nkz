"""Gateway hardening contracts: body-size cap + single-writer secret handling."""
import inspect
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


def test_max_content_length_configured(gateway):
    assert gateway.app.config["MAX_CONTENT_LENGTH"] == 16 * 1024 * 1024


def test_oversized_body_rejected_413(gateway):
    # Auth short-circuits unauthenticated requests before the body is read, so
    # exercise the limit where it applies: at body-read time.
    import io

    from flask import request
    from werkzeug.exceptions import RequestEntityTooLarge

    size = 16 * 1024 * 1024 + 1
    with gateway.app.test_request_context(
        "/ngsi-ld/v1/entities",
        method="POST",
        input_stream=io.BytesIO(b"x" * size),
        content_length=size,
        content_type="application/json",
    ):
        with pytest.raises(RequestEntityTooLarge):
            request.get_data()


def test_single_writer_secret_uses_compare_digest(gateway):
    # The previous == comparison was a timing side-channel
    src = inspect.getsource(gateway.enforce_agriparcel_single_writer)
    assert "compare_digest" in src
    assert "internal_secret == expected" not in src


def test_proxy_timeout_constant(gateway):
    assert gateway.PROXY_TIMEOUT == 30
