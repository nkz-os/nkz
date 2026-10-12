"""X-Tenant-ID is trusted only with a valid X-Auth-Signature.

Any pod in the namespace can set X-Tenant-ID; the signature proves it came
from the gateway (or a caller holding HMAC_SECRET).
"""
import os
import sys
import time

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from nkz_platform_sdk.crypto import generate_hmac_signature

import app.auth as auth
from app.auth import require_auth, require_auth_optional

SECRET = "hmac-test"
TOKEN = "header.payload.sig"

_app = FastAPI()


@_app.get("/required")
def _required(tenant: str = Depends(require_auth)):
    return {"tenant": tenant}


@_app.get("/optional")
def _optional(tenant=Depends(require_auth_optional)):
    return {"tenant": tenant}


client = TestClient(_app)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HMAC_SECRET", SECRET)
    monkeypatch.setenv("REQUIRE_HMAC_SIGNATURE", "false")
    auth._UNSIGNED_SEEN.clear()


def _signed(tenant, token=TOKEN, secret=SECRET, ts=None):
    headers = {"X-Tenant-ID": tenant,
               "X-Auth-Signature": generate_hmac_signature(secret, token, tenant, ts)}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def test_gateway_signature_is_accepted():
    resp = client.get("/required", headers=_signed("acme"))
    assert resp.status_code == 200 and resp.json() == {"tenant": "acme"}


def test_service_signature_without_token_is_accepted():
    resp = client.get("/required", headers=_signed("acme", token=""))
    assert resp.json() == {"tenant": "acme"}


def test_signature_for_another_tenant_is_rejected():
    headers = _signed("acme")
    headers["X-Tenant-ID"] = "victim"
    assert client.get("/required", headers=headers).status_code == 401
    assert client.get("/optional", headers=headers).status_code == 401


def test_signature_with_wrong_secret_is_rejected():
    assert client.get("/required", headers=_signed("acme", secret="other")).status_code == 401


def test_expired_signature_is_rejected():
    old = int(time.time()) - 600
    assert client.get("/required", headers=_signed("acme", ts=old)).status_code == 401


def test_signed_request_without_tenant_is_rejected():
    headers = _signed("acme")
    del headers["X-Tenant-ID"]
    assert client.get("/required", headers=headers).status_code == 401


# ── not enforcing: unsigned callers keep working and are logged ─────────────

def test_unsigned_tenant_is_still_accepted_and_logged_once(caplog):
    for _ in range(3):
        resp = client.get("/required", headers={"X-Tenant-ID": "acme", "X-User-ID": "worker"})
        assert resp.json() == {"tenant": "acme"}
    logged = [r for r in caplog.records if "Unsigned request" in r.getMessage()]
    assert len(logged) == 1
    assert "caller=worker" in logged[0].getMessage()


def test_unsigned_jwt_fallback_still_works():
    import base64
    import json
    claims = base64.urlsafe_b64encode(json.dumps({"tenant_id": "acme"}).encode()).decode().rstrip("=")
    resp = client.get("/required", headers={"Authorization": f"Bearer h.{claims}.s"})
    assert resp.json() == {"tenant": "acme"}


def test_no_secret_keeps_legacy_behaviour(monkeypatch):
    monkeypatch.delenv("HMAC_SECRET")
    headers = _signed("acme")
    assert client.get("/required", headers=headers).json() == {"tenant": "acme"}


# ── enforcing ────────────────────────────────────────────────────────────────

def test_enforced_unsigned_required_is_401(monkeypatch):
    monkeypatch.setenv("REQUIRE_HMAC_SIGNATURE", "true")
    resp = client.get("/required", headers={"X-Tenant-ID": "acme", "Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 401


def test_enforced_unsigned_optional_drops_the_tenant(monkeypatch):
    monkeypatch.setenv("REQUIRE_HMAC_SIGNATURE", "true")
    resp = client.get("/optional", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 200 and resp.json() == {"tenant": None}


def test_enforced_signed_is_accepted(monkeypatch):
    monkeypatch.setenv("REQUIRE_HMAC_SIGNATURE", "true")
    assert client.get("/required", headers=_signed("acme")).json() == {"tenant": "acme"}
    assert client.get("/optional", headers=_signed("acme")).json() == {"tenant": "acme"}


def test_enforced_without_secret_fails_closed(monkeypatch):
    monkeypatch.setenv("REQUIRE_HMAC_SIGNATURE", "true")
    monkeypatch.delenv("HMAC_SECRET")
    assert client.get("/required", headers=_signed("acme")).status_code == 503
    # Anonymous calls carry nothing to verify: public data stays public.
    assert client.get("/optional").json() == {"tenant": None}
