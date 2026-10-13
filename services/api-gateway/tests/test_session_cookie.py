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


class _Resp:
    content = b'{"ok": true}'
    status_code = 200
    headers = {"Content-Type": "application/json"}


def _capture_requests(monkeypatch, gw):
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(kwargs, method=method, url=url)
        return _Resp()

    monkeypatch.setattr(gw.requests, "request", fake_request)
    return captured


def test_upstream_cookies_drop_the_session_cookie(app):
    with app.app.test_request_context("/x", headers={"Cookie": "nkz_token=secret; n8n-auth=keep"}):
        assert app._cookies_for_upstream() == {"n8n-auth": "keep"}


def test_module_proxy_never_forwards_the_session_cookie(app, monkeypatch):
    captured = _capture_requests(monkeypatch, app)
    monkeypatch.setattr(app, "get_request_token", lambda: "tok")
    monkeypatch.setattr(app, "validate_jwt_token", lambda t, **kw: {"tenant_id": "montiko", "sub": "u"})
    monkeypatch.setattr(app, "extract_tenant_id", lambda p: "montiko")
    monkeypatch.setattr(app, "rate_limit", lambda t: True)
    monkeypatch.setattr(app, "has_role", lambda r, p: False)
    monkeypatch.setattr(app, "generate_hmac_signature", lambda t, ten: "sig")
    with app.app.test_request_context("/api/x", method="GET", headers={"Cookie": "nkz_token=secret; other=1"}):
        app._proxy_authenticated_request("http://backend/x")
    assert "nkz_token" not in captured["cookies"]
    assert captured["cookies"]["other"] == "1"
    assert captured["headers"]["Authorization"] == "Bearer tok"


def test_n8n_proxy_forwards_its_own_cookie_but_not_the_session(app, monkeypatch):
    captured = _capture_requests(monkeypatch, app)
    monkeypatch.setattr(app, "N8N_PROXY_HOST", "automation.example.com")
    headers = {"Host": "automation.example.com", "Cookie": "nkz_token=secret; n8n-auth=keep"}
    with app.app.test_request_context("/t1/rest/login", method="POST", headers=headers):
        app.n8n_tenant_proxy(tenant_id="t1", subpath="rest/login")
    assert captured["cookies"] == {"n8n-auth": "keep"}
