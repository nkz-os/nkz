"""Module-to-service calls: signed without a user JWT, verified fail-closed.

Uses the real common auth_middleware (loaded by path: other tests in this
directory replace sys.modules["auth_middleware"] with a stub).
"""
import hashlib
import hmac
import importlib.util
import os
import sys
import time

import pytest
from flask import Flask, g, jsonify

_here = os.path.dirname(__file__)
_common = os.path.normpath(os.path.join(_here, "..", "..", "common"))
sys.path.insert(0, os.path.normpath(os.path.join(_here, "..")))
if _common not in sys.path:
    sys.path.insert(0, _common)

from module_auth import accept_module_signature  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "real_auth_middleware", os.path.join(_common, "auth_middleware.py")
)
am = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(am)

SECRET = "s3cret"
PAYLOAD = {"sub": "u1", "tenant_id": "acme", "realm_access": {"roles": ["Farmer"]}}


def _sign(token, tenant, secret=SECRET, ts=None):
    ts = str(int(time.time()) if ts is None else ts)
    digest = hmac.new(
        secret.encode(), f"{token}|{tenant}|{ts}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{digest}:{ts}"


@pytest.fixture
def secret(monkeypatch):
    def _set(value):
        monkeypatch.setattr(am, "HMAC_SECRET", value)
        # verify_hmac_signature reads the secret of the module that defines it
        monkeypatch.setitem(am.verify_hmac_signature.__globals__, "HMAC_SECRET", value)

    _set(SECRET)
    monkeypatch.setattr(am, "REQUIRE_HMAC_SIGNATURE", True)
    return _set


@pytest.fixture
def client():
    app = Flask(__name__)
    require_auth = accept_module_signature(am.require_auth, am)

    @app.route("/p")
    @require_auth
    def protected():
        return jsonify(
            tenant=g.tenant, module=getattr(g, "module_caller", False), user=g.user
        )

    return app.test_client()


def test_module_signature_accepted(secret, client):
    resp = client.get(
        "/p",
        headers={
            "X-Tenant-ID": "acme",
            "X-User-ID": "agent",
            "X-Auth-Signature": _sign("", "acme"),
        },
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"tenant": "acme", "module": True, "user": "agent"}


def test_signature_for_other_tenant_rejected(secret, client):
    resp = client.get(
        "/p", headers={"X-Tenant-ID": "victim", "X-Auth-Signature": _sign("", "acme")}
    )
    assert resp.status_code == 401


def test_wrong_secret_rejected(secret, client):
    resp = client.get(
        "/p",
        headers={"X-Tenant-ID": "acme", "X-Auth-Signature": _sign("", "acme", "other")},
    )
    assert resp.status_code == 401


def test_expired_signature_rejected(secret, client):
    old = int(time.time()) - 301
    resp = client.get(
        "/p", headers={"X-Tenant-ID": "acme", "X-Auth-Signature": _sign("", "acme", ts=old)}
    )
    assert resp.status_code == 401


def test_signature_over_a_token_is_not_a_module_signature(secret, client):
    resp = client.get(
        "/p", headers={"X-Tenant-ID": "acme", "X-Auth-Signature": _sign("tok", "acme")}
    )
    assert resp.status_code == 401


def test_missing_tenant_rejected(secret, client):
    resp = client.get("/p", headers={"X-Auth-Signature": _sign("", "")})
    assert resp.status_code == 401


def test_missing_secret_is_503(secret, client):
    secret("")
    resp = client.get(
        "/p", headers={"X-Tenant-ID": "acme", "X-Auth-Signature": _sign("", "acme")}
    )
    assert resp.status_code == 503


def test_unsigned_without_token_rejected(secret, client):
    resp = client.get("/p", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 401


def test_user_path_unchanged(secret, client, monkeypatch):
    monkeypatch.setattr(am, "validate_jwt_token", lambda token: dict(PAYLOAD))
    resp = client.get(
        "/p",
        headers={"Authorization": "Bearer tok", "X-Auth-Signature": _sign("tok", "acme")},
    )
    assert resp.status_code == 200
    assert resp.get_json()["module"] is False


def test_token_present_never_takes_module_path(secret, client, monkeypatch):
    """A module-style signature next to a token is checked as a user request."""
    monkeypatch.setattr(am, "validate_jwt_token", lambda token: dict(PAYLOAD))
    resp = client.get(
        "/p",
        headers={
            "Authorization": "Bearer tok",
            "X-Tenant-ID": "acme",
            "X-Auth-Signature": _sign("", "acme"),
        },
    )
    assert resp.status_code == 401


def test_cookie_token_never_takes_module_path(secret, client, monkeypatch):
    monkeypatch.setattr(am, "validate_jwt_token", lambda token: None)
    client.set_cookie("nkz_token", "tok")
    resp = client.get(
        "/p", headers={"X-Tenant-ID": "acme", "X-Auth-Signature": _sign("", "acme")}
    )
    assert resp.status_code == 401
