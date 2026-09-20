"""Gateway proxy for entity-manager catalog endpoints (parents/inventory)."""


def _patch_auth(monkeypatch, gw, tenant="montiko"):
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(gw, "validate_jwt_token", lambda t: {"tenant_id": tenant})
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: tenant)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "KEYCLOAK_AUTH_AVAILABLE", True)
    monkeypatch.setattr(gw, "generate_hmac_signature", lambda t, ten: "sig123")


class _Resp:
    content = b'{"entities": []}'
    status_code = 200
    headers = {}


def test_parents_forwards_to_entity_manager(monkeypatch):
    import fiware_api_gateway as gw
    captured = {}

    def fake_request(method, url, headers=None, params=None, data=None, timeout=None):
        captured.update(method=method, url=url, headers=headers, params=params)
        return _Resp()

    _patch_auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "request", fake_request)
    with gw.app.test_request_context("/api/entities/parents?type=AgriParcel", method="GET"):
        resp = gw.proxy_entity_catalog()
    assert resp.status_code == 200
    assert captured["url"] == f"{gw.ENTITY_MANAGER_URL}/api/entities/parents"
    assert captured["headers"]["X-Auth-Signature"] == "sig123"
    assert captured["headers"]["X-Tenant-ID"] == "montiko"
    assert dict(captured["params"]) == {"type": "AgriParcel"}


def test_inventory_forwards_to_entity_manager(monkeypatch):
    import fiware_api_gateway as gw
    captured = {}

    def fake_request(method, url, headers=None, params=None, data=None, timeout=None):
        captured.update(method=method, url=url, headers=headers)
        return _Resp()

    _patch_auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "request", fake_request)
    with gw.app.test_request_context("/api/entities/inventory", method="GET"):
        resp = gw.proxy_entity_catalog()
    assert resp.status_code == 200
    assert captured["url"] == f"{gw.ENTITY_MANAGER_URL}/api/entities/inventory"


def test_catalog_requires_auth(monkeypatch):
    import fiware_api_gateway as gw
    monkeypatch.setattr(gw, "get_request_token", lambda: None)
    with gw.app.test_request_context("/api/entities/parents", method="GET"):
        resp = gw.proxy_entity_catalog()
    assert resp.status_code == 401
