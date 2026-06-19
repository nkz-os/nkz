"""Tests for /api/geocode proxy route — normalises Photon JSON to frontend contract."""

import fiware_api_gateway as gw


def _patch_auth(monkeypatch, gw_module, token="tok", tenant="geocode"):
    """Patch the auth chain exactly like existing gateway tests do."""
    monkeypatch.setattr(gw_module, "get_request_token", lambda: token)
    monkeypatch.setattr(
        gw_module, "validate_jwt_token", lambda t: {"tenant_id": tenant} if token else None
    )
    monkeypatch.setattr(gw_module, "extract_tenant_id", lambda p: tenant)
    monkeypatch.setattr(gw_module, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw_module, "KEYCLOAK_AUTH_AVAILABLE", True)
    monkeypatch.setattr(gw_module, "generate_hmac_signature", lambda t, ten: "sig123")


PHOTON_RESP = {
    "features": [
        {
            "geometry": {"coordinates": [-1.6458, 42.8125]},
            "properties": {
                "name": "Pamplona",
                "state": "Navarra",
                "country": "España",
                "countrycode": "ES",
                "osm_key": "place",
                "osm_value": "city",
                "extent": [-1.70, 42.86, -1.58, 42.77],
            },
        }
    ]
}


class _FakeOkResponse:
    status_code = 200

    def json(self):
        return PHOTON_RESP

    def raise_for_status(self):
        pass


def test_geocode_normalises_photon(monkeypatch):
    _patch_auth(monkeypatch, gw)

    def fake_get(*a, **kw):
        return _FakeOkResponse()

    monkeypatch.setattr(gw.requests, "get", fake_get)

    with gw.app.test_request_context("/api/geocode?q=Pamplona&limit=5"):
        resp = gw.proxy_geocode()
    assert resp.status_code == 200
    body = resp.get_json()
    r = body["results"][0]
    assert r["label"].startswith("Pamplona")
    assert r["lat"] == 42.8125 and r["lon"] == -1.6458
    assert r["bbox"] == [-1.70, 42.77, -1.58, 42.86]
    assert r["type"] == "city" and r["countryCode"] == "ES"


def test_geocode_upstream_error_returns_502(monkeypatch):
    _patch_auth(monkeypatch, gw)

    def _raise(*a, **kw):
        raise Exception("photon down")

    monkeypatch.setattr(gw.requests, "get", _raise)

    with gw.app.test_request_context("/api/geocode?q=x"):
        resp = gw.proxy_geocode()
    assert resp.status_code == 502


def test_geocode_requires_auth(monkeypatch):
    _patch_auth(monkeypatch, gw, token=None)
    with gw.app.test_request_context("/api/geocode?q=x"):
        resp = gw.proxy_geocode()
    assert resp.status_code == 401


def test_empty_query_returns_empty_results(monkeypatch):
    _patch_auth(monkeypatch, gw)
    with gw.app.test_request_context("/api/geocode?q="):
        resp = gw.proxy_geocode()
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["results"] == []
