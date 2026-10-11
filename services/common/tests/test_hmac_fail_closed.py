"""Missing HMAC configuration must refuse requests, not wave them through."""
from flask import Flask

import auth_middleware
import keycloak_auth

PAYLOAD = {"sub": "u1", "tenant_id": "acme", "realm_access": {"roles": ["Farmer"]}}


def _app():
    app = Flask(__name__)

    @app.route("/p")
    @auth_middleware.require_auth
    def protected():
        return "ok"

    return app


def test_verify_without_secret_is_false(monkeypatch):
    monkeypatch.setattr(keycloak_auth, "HMAC_SECRET", "")
    assert keycloak_auth.verify_hmac_signature("abc:123", "tok", "acme") is False


def test_require_auth_503_when_secret_missing(monkeypatch):
    monkeypatch.setattr(auth_middleware, "validate_jwt_token", lambda token: dict(PAYLOAD))
    monkeypatch.setattr(auth_middleware, "REQUIRE_HMAC_SIGNATURE", True)
    monkeypatch.setattr(auth_middleware, "HMAC_SECRET", "")
    resp = _app().test_client().get("/p", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 503


def test_require_auth_passes_with_valid_signature(monkeypatch):
    monkeypatch.setattr(auth_middleware, "validate_jwt_token", lambda token: dict(PAYLOAD))
    monkeypatch.setattr(auth_middleware, "REQUIRE_HMAC_SIGNATURE", True)
    monkeypatch.setattr(auth_middleware, "HMAC_SECRET", "s3cret")
    monkeypatch.setattr(keycloak_auth, "HMAC_SECRET", "s3cret")
    sig = keycloak_auth.generate_hmac_signature("t", "acme")
    resp = _app().test_client().get(
        "/p", headers={"Authorization": "Bearer t", "X-Auth-Signature": sig}
    )
    assert resp.status_code == 200


def test_require_auth_401_without_signature(monkeypatch):
    monkeypatch.setattr(auth_middleware, "validate_jwt_token", lambda token: dict(PAYLOAD))
    monkeypatch.setattr(auth_middleware, "REQUIRE_HMAC_SIGNATURE", True)
    monkeypatch.setattr(auth_middleware, "HMAC_SECRET", "s3cret")
    resp = _app().test_client().get("/p", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 401


def _reload_keycloak_auth(monkeypatch, caplog, secret):
    """Reload keycloak_auth under a given HMAC_SECRET env and return CRITICAL messages.

    The module is reloaded again with the original environment in `finally`, so
    later tests see it in the state it had before this test.
    """
    import importlib
    import logging

    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("HMAC_SECRET", secret)
    try:
        with caplog.at_level(logging.CRITICAL):
            caplog.clear()
            importlib.reload(keycloak_auth)
            return [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]
    finally:
        monkeypatch.undo()
        importlib.reload(keycloak_auth)


def test_import_logs_critical_when_secret_unset(monkeypatch, caplog):
    messages = _reload_keycloak_auth(monkeypatch, caplog, "")
    assert any("HMAC_SECRET is unset" in m for m in messages)


def test_import_is_silent_when_secret_set(monkeypatch, caplog):
    messages = _reload_keycloak_auth(monkeypatch, caplog, "s3cret")
    assert not any("HMAC_SECRET is unset" in m for m in messages)
