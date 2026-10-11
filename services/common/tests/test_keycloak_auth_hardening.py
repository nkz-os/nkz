"""require_keycloak_auth must never skip signature verification."""
import time
from pathlib import Path

import jwt
from flask import Flask

import keycloak_auth


def _app():
    app = Flask(__name__)

    @app.route("/p")
    @keycloak_auth.require_keycloak_auth
    def protected():
        return "ok"

    return app


def test_trust_flag_is_gone():
    assert "TRUST_API_GATEWAY" not in Path(keycloak_auth.__file__).read_text()


def test_unsigned_token_rejected_even_with_flag_and_tenant_header(monkeypatch):
    monkeypatch.setenv("TRUST_API_GATEWAY", "true")

    def reject(token):
        raise keycloak_auth.TokenValidationError("bad signature")

    monkeypatch.setattr(keycloak_auth, "validate_keycloak_token", reject)
    # A well-formed but attacker-signed token: decodable without verification,
    # so only real signature validation can reject it.
    forged = jwt.encode(
        {"sub": "attacker", "tenant_id": "acme", "exp": int(time.time()) + 300},
        "attacker-controlled-signing-key-32b!",
        algorithm="HS256",
    )
    resp = _app().test_client().get(
        "/p", headers={"Authorization": f"Bearer {forged}", "X-Tenant-ID": "acme"}
    )
    assert resp.status_code == 401
