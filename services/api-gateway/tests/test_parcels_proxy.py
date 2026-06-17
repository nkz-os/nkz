"""Gateway proxy for parcel-module CONTROL endpoints only (activation), NOT entity CRUD.
Entity CRUD goes via /ngsi-ld. Direct invocation in a request context (cross_origin wraps the return).
"""


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


def test_module_activate_forwards_to_entity_manager(monkeypatch):
    import fiware_api_gateway as gw
    captured = {}

    def fake_request(method, url, headers=None, params=None, data=None, timeout=None):
        captured.update(method=method, url=url, headers=headers)
        return _Resp()

    _patch_auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "request", fake_request)
    sub = "urn:ngsi-ld:AgriParcel:x/modules/weather/activate"
    with gw.app.test_request_context(f"/api/entities/parcels/{sub}", method="POST", json={}):
        resp = gw.proxy_parcel_modules(sub)
    assert resp.status_code == 200
    assert captured["url"] == f"{gw.ENTITY_MANAGER_URL}/api/entities/parcels/{sub}"
    assert captured["headers"]["X-Auth-Signature"] == "sig123"
    assert captured["headers"]["X-Tenant-ID"] == "montiko"


def test_module_proxy_rejects_non_module_subpath(monkeypatch):
    import fiware_api_gateway as gw
    _patch_auth(monkeypatch, gw)
    with gw.app.test_request_context(
        "/api/entities/parcels/urn:ngsi-ld:AgriParcel:x", method="POST", json={}
    ):
        resp = gw.proxy_parcel_modules("urn:ngsi-ld:AgriParcel:x")
    assert resp.status_code == 404


def test_module_proxy_requires_auth(monkeypatch):
    import fiware_api_gateway as gw
    monkeypatch.setattr(gw, "get_request_token", lambda: None)
    with gw.app.test_request_context(
        "/api/entities/parcels/urn:ngsi-ld:AgriParcel:x/modules", method="GET"
    ):
        resp = gw.proxy_parcel_modules("urn:ngsi-ld:AgriParcel:x/modules")
    assert resp.status_code == 401
