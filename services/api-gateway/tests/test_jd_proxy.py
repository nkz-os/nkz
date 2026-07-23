"""Gateway proxy for John Deere connect module (/api/jd/*)."""


def _patch_auth(monkeypatch, gw, tenant="montiko"):
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(gw, "validate_jwt_token", lambda t: {"tenant_id": tenant})
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: tenant)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "KEYCLOAK_AUTH_AVAILABLE", True)
    monkeypatch.setattr(gw, "generate_hmac_signature", lambda t, ten: "sig123")


class _Resp:
    content = b'{"ok": true}'
    status_code = 200
    headers = {}


def test_jd_status_forwards_with_hmac(monkeypatch):
    import fiware_api_gateway as gw

    captured = {}

    def fake_request(method, url, headers=None, params=None, data=None, timeout=None):
        captured.update(method=method, url=url, headers=headers)
        return _Resp()

    _patch_auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "request", fake_request)
    with gw.app.test_request_context("/api/jd/status", method="GET"):
        resp = gw.proxy_jd_connect()
    assert resp.status_code == 200
    assert captured["url"] == f"{gw.JD_CONNECT_URL}/api/jd/status"
    assert captured["headers"]["X-Auth-Signature"] == "sig123"
    assert captured["headers"]["X-Tenant-ID"] == "montiko"


def test_jd_machines_requires_tenant(monkeypatch):
    import fiware_api_gateway as gw

    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(gw, "validate_jwt_token", lambda t: {"tenant_id": "montiko"})
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: None)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "KEYCLOAK_AUTH_AVAILABLE", True)
    with gw.app.test_request_context(
        "/api/jd/machines", method="GET", headers={}
    ):
        resp = gw.proxy_jd_connect()
    status = resp[1] if isinstance(resp, tuple) else resp.status_code
    assert status == 401


def test_jd_callback_forwards_without_signature(monkeypatch):
    import fiware_api_gateway as gw

    captured = {}

    def fake_request(method, url, headers=None, params=None, data=None, timeout=None):
        captured.update(method=method, url=url, headers=headers or {})
        return _Resp()

    monkeypatch.setattr(gw, "get_request_token", lambda: None)
    monkeypatch.setattr(gw.requests, "request", fake_request)
    with gw.app.test_request_context(
        "/api/jd/oauth/callback?code=x&state=y", method="GET"
    ):
        resp = gw.proxy_jd_callback()
    assert resp.status_code == 200
    assert captured["url"] == f"{gw.JD_CONNECT_URL}/api/jd/oauth/callback"
    assert "X-Auth-Signature" not in captured["headers"]
