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
