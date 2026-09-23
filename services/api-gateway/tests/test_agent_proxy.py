"""Gateway proxy for the agent module management API (/api/agent/*)."""


def _patch_auth(monkeypatch, gw, tenant="montiko"):
    payload = {
        "tenant_id": tenant,
        "sub": "user-1",
        "realm_access": {"roles": ["role_pro"]},
    }
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(gw, "validate_jwt_token", lambda t: payload)
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: tenant)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "KEYCLOAK_AUTH_AVAILABLE", True)
    monkeypatch.setattr(gw, "generate_hmac_signature", lambda t, ten: "sig123")


class _Resp:
    content = b'{"ok": true}'
    status_code = 200
    headers = {"Content-Type": "application/json"}


def test_link_tokens_forwards_identity_headers(monkeypatch):
    import fiware_api_gateway as gw

    captured = {}

    def fake_request(method, url, headers=None, params=None, data=None, **kwargs):
        captured.update(method=method, url=url, headers=headers)
        return _Resp()

    _patch_auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "request", fake_request)
    with gw.app.test_request_context("/api/agent/link-tokens", method="POST"):
        resp = gw.agent_link_tokens_proxy()
    assert resp.status_code == 200
    assert captured["url"] == f"{gw.AGENT_API_URL}/api/agent/link-tokens"
    assert captured["headers"]["X-Tenant-ID"] == "montiko"
    assert captured["headers"]["X-User-ID"] == "user-1"
    assert captured["headers"]["X-User-Roles"] == "role_pro"
    assert captured["headers"]["Authorization"] == "Bearer tok"


def test_links_list_forwards_identity_headers(monkeypatch):
    import fiware_api_gateway as gw

    captured = {}

    def fake_request(method, url, headers=None, params=None, data=None, **kwargs):
        captured.update(method=method, url=url, headers=headers)
        return _Resp()

    _patch_auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "request", fake_request)
    with gw.app.test_request_context("/api/agent/links", method="GET"):
        resp = gw.agent_links_proxy()
    assert resp.status_code == 200
    assert captured["url"] == f"{gw.AGENT_API_URL}/api/agent/links"
    assert captured["headers"]["X-Tenant-ID"] == "montiko"


def test_link_delete_forwards_link_id(monkeypatch):
    import fiware_api_gateway as gw

    captured = {}

    def fake_request(method, url, headers=None, params=None, data=None, **kwargs):
        captured.update(method=method, url=url, headers=headers)
        return _Resp()

    _patch_auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "request", fake_request)
    with gw.app.test_request_context("/api/agent/links/abc123", method="DELETE"):
        resp = gw.agent_link_delete_proxy("abc123")
    assert resp.status_code == 200
    assert captured["url"] == f"{gw.AGENT_API_URL}/api/agent/links/abc123"
    assert captured["method"] == "DELETE"


def test_links_requires_auth(monkeypatch):
    import fiware_api_gateway as gw

    monkeypatch.setattr(gw, "get_request_token", lambda: None)
    with gw.app.test_request_context("/api/agent/links", method="GET"):
        resp = gw.agent_links_proxy()
    status = resp[1] if isinstance(resp, tuple) else resp.status_code
    assert status == 401


def test_webhook_path_is_not_routed_through_gateway_proxies():
    """The Telegram webhook has its own dedicated Ingress that bypasses the
    gateway. These explicit routes must stay scoped to link-tokens/links so
    they never swallow /api/agent/webhook/telegram."""
    import fiware_api_gateway as gw

    client = gw.app.test_client()
    resp = client.post("/api/agent/webhook/telegram")
    assert resp.status_code == 404
