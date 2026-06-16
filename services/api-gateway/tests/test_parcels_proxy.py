"""Tests for proxy_parcels_requests() — the gateway route that forwards the
entity-manager parcel API (single source of truth for AgriParcel).

The browser/modules POST/PATCH/DELETE /api/entities/parcels; the gateway must
proxy these to entity-manager (which writes Orion directly). Without this route
the requests 404 and parcel creation breaks while enforcement is active.

We invoke the view directly inside a request context (mirroring
test_parcel_write_enforcement) to avoid the global auth before_request hooks.
"""


def _patch_auth(monkeypatch, gw, tenant="montiko"):
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(gw, "validate_jwt_token", lambda t: {"tenant_id": tenant})
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: tenant)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)


class _Resp:
    def __init__(self):
        self.content = b'{"id":"urn:ngsi-ld:AgriParcel:x","created":true}'
        self.status_code = 201
        self.headers = {}


def test_proxy_parcels_create_forwards_to_entity_manager(monkeypatch):
    import fiware_api_gateway as gw

    captured = {}

    def fake_request(method, url, headers=None, params=None, data=None, timeout=None):
        captured.update(method=method, url=url, headers=headers, data=data)
        return _Resp()

    _patch_auth(monkeypatch, gw)
    monkeypatch.setattr(gw, "KEYCLOAK_AUTH_AVAILABLE", True)
    monkeypatch.setattr(gw, "generate_hmac_signature", lambda t, ten: "sig123")
    monkeypatch.setattr(gw.requests, "request", fake_request)

    with gw.app.test_request_context(
        "/api/entities/parcels",
        method="POST",
        json={"name": "P", "geometry": {"type": "Polygon", "coordinates": []}},
    ):
        resp = gw.proxy_parcels_requests()

    assert resp.status_code == 201
    assert captured["method"] == "POST"
    assert captured["url"] == f"{gw.ENTITY_MANAGER_URL}/api/entities/parcels"
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert captured["headers"]["X-Tenant-ID"] == "montiko"
    # HMAC signature required by entity-manager (REQUIRE_HMAC_SIGNATURE)
    assert captured["headers"]["X-Auth-Signature"] == "sig123"
    assert b'"name"' in captured["data"]


def test_proxy_parcels_attrs_subpath_forwards(monkeypatch):
    import fiware_api_gateway as gw

    captured = {}

    def fake_request(method, url, headers=None, params=None, data=None, timeout=None):
        captured.update(method=method, url=url)
        return _Resp()

    _patch_auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "request", fake_request)

    sub = "urn:ngsi-ld:AgriParcel:x/attrs"
    with gw.app.test_request_context(
        f"/api/entities/parcels/{sub}",
        method="PATCH",
        json={"elevation": {"type": "Property", "value": 576}},
    ):
        resp = gw.proxy_parcels_requests(sub)
    assert resp.status_code == 201

    assert captured["method"] == "PATCH"
    assert captured["url"] == f"{gw.ENTITY_MANAGER_URL}/api/entities/parcels/{sub}"


def test_proxy_parcels_requires_auth(monkeypatch):
    import fiware_api_gateway as gw

    monkeypatch.setattr(gw, "get_request_token", lambda: None)
    with gw.app.test_request_context("/api/entities/parcels", method="POST", json={}):
        resp = gw.proxy_parcels_requests()
    assert resp.status_code == 401
