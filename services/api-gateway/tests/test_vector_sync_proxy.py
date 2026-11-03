"""Vector sync proxy (WatermelonDB pull/push) must sign requests like every other proxy."""


def _patch_auth(monkeypatch, gw, tenant="acmefarm"):
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


def test_sync_pull_forwards_signature(monkeypatch):
    import fiware_api_gateway as gw
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured.update(url=url, headers=headers, params=dict(params))
        return _Resp()

    _patch_auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "get", fake_get)
    with gw.app.test_request_context(
        "/api/core/sync/vectorial?collections=parcels&last_pulled_at=0"
    ):
        resp = gw.proxy_vector_sync_requests()
    assert resp.status_code == 200
    assert captured["url"] == f"{gw.ENTITY_MANAGER_URL}/api/core/sync/vectorial"
    assert captured["headers"]["X-Auth-Signature"] == "sig123"
    assert captured["headers"]["X-Tenant-ID"] == "acmefarm"
    assert captured["params"]["collections"] == "parcels"


def test_sync_push_forwards_signature_and_body(monkeypatch):
    import fiware_api_gateway as gw
    captured = {}

    def fake_post(url, headers=None, params=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json)
        return _Resp()

    _patch_auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "post", fake_post)
    with gw.app.test_request_context(
        "/api/core/sync/vectorial", method="POST", json={"changes": {}}
    ):
        resp = gw.proxy_vector_sync_requests()
    assert resp.status_code == 200
    assert captured["url"] == f"{gw.ENTITY_MANAGER_URL}/api/core/sync/vectorial"
    assert captured["json"] == {"changes": {}}
    assert captured["headers"]["X-Auth-Signature"] == "sig123"
    assert captured["headers"]["X-Tenant-ID"] == "acmefarm"


def test_sync_requires_token(monkeypatch):
    import fiware_api_gateway as gw

    def boom(*a, **k):
        raise AssertionError("must not be called")

    monkeypatch.setattr(gw, "get_request_token", lambda: None)
    monkeypatch.setattr(gw.requests, "get", boom)
    monkeypatch.setattr(gw.requests, "post", boom)
    with gw.app.test_request_context("/api/core/sync/vectorial"):
        resp = gw.proxy_vector_sync_requests()
    status = resp[1] if isinstance(resp, tuple) else resp.status_code
    assert status == 401
