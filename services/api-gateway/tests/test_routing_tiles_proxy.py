"""Tiles proxy must target the routing module's real prefix and reject odd subpaths."""


def _patch_auth(monkeypatch, gw, tenant="acmefarm"):
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(gw, "validate_jwt_token", lambda t: {"tenant_id": tenant})
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: tenant)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "KEYCLOAK_AUTH_AVAILABLE", True)
    monkeypatch.setattr(gw, "generate_hmac_signature", lambda t, ten: "sig123")


class _Resp:
    content = b"pmtiles"
    status_code = 200
    headers = {}


def test_tiles_proxy_targets_module_prefix(monkeypatch):
    import fiware_api_gateway as gw
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured.update(url=url, headers=headers, params=dict(params))
        return _Resp()

    _patch_auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "get", fake_get)
    with gw.app.test_request_context("/api/routing/tiles/parcel.pmtiles"):
        resp = gw.proxy_routing_tiles("parcel.pmtiles")
    assert resp[1] == 200 if isinstance(resp, tuple) else resp.status_code == 200
    assert captured["url"] == f"{gw.ROUTING_API_URL}/api/routing/tiles"
    assert "nkz-module-gis-routing/tiles" not in captured["url"]
    assert captured["params"]["parcel_id"] == "parcel.pmtiles"
    assert captured["headers"]["X-Tenant-ID"] == "acmefarm"


def test_tiles_proxy_rejects_path_traversal(monkeypatch):
    import fiware_api_gateway as gw

    def boom(*a, **k):
        raise AssertionError("must not be called")

    _patch_auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "get", boom)
    for bad in ("../secret", "a/b"):
        with gw.app.test_request_context(f"/api/routing/tiles/{bad}"):
            resp = gw.proxy_routing_tiles(bad)
        status = resp[1] if isinstance(resp, tuple) else resp.status_code
        assert status == 400
