"""Only JWKS-verifiable (RS256) tokens are accepted; no shared-secret fallback."""
import jwt

import auth_middleware
import keycloak_auth

SECRET = "x" * 40


def _hs256_token():
    return jwt.encode(
        {"sub": "u1", "tenant_id": "acme", "exp": 4102444800}, SECRET, algorithm="HS256"
    )


def test_fallback_function_is_gone():
    assert not hasattr(keycloak_auth, "validate_token_fallback")
    assert not hasattr(auth_middleware, "JWT_SECRET")
    assert not hasattr(auth_middleware, "ALLOW_JWT_FALLBACK")


def test_hs256_token_signed_with_hmac_secret_is_rejected(monkeypatch):
    monkeypatch.setattr(keycloak_auth, "HMAC_SECRET", SECRET)
    monkeypatch.setattr(auth_middleware, "HMAC_SECRET", SECRET)

    def _jwks_rejects(token):
        raise auth_middleware.TokenValidationError("not verifiable by JWKS")

    monkeypatch.setattr(auth_middleware, "validate_keycloak_token", _jwks_rejects)
    assert auth_middleware.validate_jwt_token(_hs256_token()) is None


def test_rejected_when_keycloak_module_unavailable(monkeypatch):
    monkeypatch.setattr(auth_middleware, "KEYCLOAK_AUTH_AVAILABLE", False)
    monkeypatch.setattr(auth_middleware, "HMAC_SECRET", SECRET)
    assert auth_middleware.validate_jwt_token(_hs256_token()) is None
