"""The session cookie is host-only and never leaves the gateway.

A ``Domain`` attribute would hand the cookie to every sibling subdomain of the
installation, including ones that run tenant-controlled code.
"""

import time
from http.cookies import SimpleCookie


def _patch_session_auth(monkeypatch, gw):
    payload = {"tenant_id": "montiko", "sub": "user-1", "exp": time.time() + 300}
    monkeypatch.setattr(gw, "validate_jwt_token", lambda t, **kw: payload)
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: "montiko")
    monkeypatch.setattr(gw, "_is_tenant_suspended", lambda t: False)


def _session_cookie(resp):
    jar = SimpleCookie()
    for header in resp.headers.getlist("Set-Cookie"):
        jar.load(header)
    return jar["nkz_token"]


def test_session_cookie_is_host_only(app, client, monkeypatch):
    _patch_session_auth(monkeypatch, app)
    resp = client.post("/api/auth/session", json={"token": "tok"})
    assert resp.status_code == 200
    morsel = _session_cookie(resp)
    assert morsel["domain"] == ""
    assert morsel["httponly"]
    assert morsel["secure"]
    assert morsel["samesite"] == "Strict"
    assert morsel["path"] == "/"


def test_logout_clears_host_only_cookie(client):
    resp = client.delete("/api/auth/session")
    assert resp.status_code == 200
    morsel = _session_cookie(resp)
    assert morsel.value == ""
    assert morsel["domain"] == ""
    assert morsel["path"] == "/"


def test_no_domain_knob_exists(app):
    assert not hasattr(app, "COOKIE_DOMAIN")
