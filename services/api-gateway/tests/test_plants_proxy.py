"""Gateway proxy for the plants endpoints (entity-manager is the single writer of AgriPlant)."""


def _patch_auth(monkeypatch, gw, tenant="acmefarm"):
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(gw, "validate_jwt_token", lambda t: {"tenant_id": tenant})
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: tenant)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "KEYCLOAK_AUTH_AVAILABLE", True)
    monkeypatch.setattr(gw, "generate_hmac_signature", lambda t, ten: "sig123")


class _Resp:
    content = b'{"written": []}'
    status_code = 200
    headers = {}


def _capture(monkeypatch, gw):
    captured = {}

    def fake_request(method, url, headers=None, params=None, data=None, timeout=None):
        captured.update(method=method, url=url, headers=headers, data=data)
        return _Resp()
    monkeypatch.setattr(gw.requests, "request", fake_request)
    return captured


def test_plants_commit_forwards_signed(monkeypatch):
    import fiware_api_gateway as gw
    _patch_auth(monkeypatch, gw)
    captured = _capture(monkeypatch, gw)
    with gw.app.test_request_context("/api/entities/plants", method="POST", json={"parcel_id": "p"}):
        resp = gw.proxy_plants()
    assert resp.status_code == 200
    assert captured["url"] == f"{gw.ENTITY_MANAGER_URL}/entities/plants"
    assert captured["headers"]["X-Auth-Signature"] == "sig123"
    assert captured["headers"]["X-Tenant-ID"] == "acmefarm"


def test_plants_preview_forwards(monkeypatch):
    import fiware_api_gateway as gw
    _patch_auth(monkeypatch, gw)
    captured = _capture(monkeypatch, gw)
    with gw.app.test_request_context("/api/entities/plants/preview", method="POST", json={}):
        gw.proxy_plants("preview")
    assert captured["url"] == f"{gw.ENTITY_MANAGER_URL}/entities/plants/preview"


def test_plants_proxy_requires_auth(monkeypatch):
    import fiware_api_gateway as gw
    monkeypatch.setattr(gw, "get_request_token", lambda: None)
    with gw.app.test_request_context("/api/entities/plants", method="POST", json={}):
        resp = gw.proxy_plants()
    assert resp.status_code == 401
