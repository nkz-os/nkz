"""An installation that never sets CORS_ORIGINS must hear about it at startup.

The same-site guard keys on that list; with the local-development default the
gateway silently rejects every cookie-authenticated browser mutation.
"""

import importlib
import logging
import os
import sys
from unittest.mock import MagicMock

_services_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_services_dir, os.path.join(_services_dir, "common"),
           os.path.join(_services_dir, "api-gateway")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reload_gateway(monkeypatch):
    for k, v in (
        ("ORION_URL", "http://orion-test:1026"),
        ("KEYCLOAK_URL", "http://keycloak-test:8080"),
        ("CONTEXT_URL", "http://context-test/ngsi-context.jsonld"),
        ("HMAC_SECRET", "test-hmac-secret"),
        ("KEYCLOAK_REALM", "nekazari"),
        ("POSTGRES_URL", "postgresql://user:pass@localhost:5432/db"),
    ):
        monkeypatch.setenv(k, v)
    sys.modules["psycopg2"] = MagicMock()
    sys.modules["psycopg2.extras"] = MagicMock()
    import keycloak_auth
    importlib.reload(keycloak_auth)
    import fiware_api_gateway as g
    return importlib.reload(g)


def _critical_about_cors(caplog):
    return [r for r in caplog.records if r.levelno == logging.CRITICAL and "CORS_ORIGINS" in r.getMessage()]


def test_unset_cors_origins_is_loud(monkeypatch, caplog):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    with caplog.at_level(logging.CRITICAL, logger="fiware_api_gateway"):
        gw = _reload_gateway(monkeypatch)
    assert _critical_about_cors(caplog), "a missing CORS_ORIGINS must log CRITICAL"
    assert "http://localhost:3000" in gw.ALLOWED_ORIGINS  # local development still works


def test_configured_cors_origins_is_quiet(monkeypatch, caplog):
    monkeypatch.setenv("CORS_ORIGINS", "https://ui.example")
    with caplog.at_level(logging.CRITICAL, logger="fiware_api_gateway"):
        gw = _reload_gateway(monkeypatch)
    assert not _critical_about_cors(caplog)
    assert gw.ALLOWED_ORIGINS == {"https://ui.example"}
