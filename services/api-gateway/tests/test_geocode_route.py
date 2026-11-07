"""Tests for /api/geocode proxy route — normalises Photon JSON to frontend contract.

Regression focus: the gateway must never forward values Photon rejects with 400
(unsupported ``lang``, malformed bbox/lat/lon) — that surfaced to users as an
always-failing 502 search. Tests therefore assert the exact upstream params.
"""

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
            }
        }
    ]
}


class _FakeOkResponse:
    status_code = 200

    def __init__(self, payload=None):
        self._payload = payload if payload is not None else PHOTON_RESP

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _UpstreamRecorder:
    """Capture the params/headers the gateway sends upstream."""

    def __init__(self, response=None):
        self.calls = []
        self.response = response or _FakeOkResponse()

    def __call__(self, url, params=None, **kwargs):
        self.calls.append({"url": url, "params": dict(params or {})})
        return self.response


def _run_geocode(monkeypatch, gw_module, query_string, response=None):
    recorder = _UpstreamRecorder(response)
    monkeypatch.setattr(gw_module.requests, "get", recorder)
    with gw_module.app.test_request_context(query_string):
        resp = gw_module.proxy_geocode()
    return resp, recorder.calls


# ---------------------------------------------------------------------------
# Upstream params — never send anything Photon rejects
# ---------------------------------------------------------------------------

def test_geocode_never_forwards_unsupported_lang_es(monkeypatch):
    """lang=es (and any unsupported lang) must be dropped: Photon answers 400."""
    _patch_auth(monkeypatch, gw)
    resp, calls = _run_geocode(monkeypatch, gw, "/api/geocode?q=Pamplona&lang=es")
    assert resp.status_code == 200
    assert "lang" not in calls[0]["params"]


def test_geocode_omits_lang_when_absent(monkeypatch):
    _patch_auth(monkeypatch, gw)
    resp, calls = _run_geocode(monkeypatch, gw, "/api/geocode?q=Pamplona")
    assert resp.status_code == 200
    assert "lang" not in calls[0]["params"]


def test_geocode_forwards_supported_langs(monkeypatch):
    _patch_auth(monkeypatch, gw)
    for lang in ("en", "fr", "de", "EN"):
        resp, calls = _run_geocode(monkeypatch, gw, f"/api/geocode?q=Pamplona&lang={lang}")
        assert resp.status_code == 200, f"lang={lang}"
        assert calls[0]["params"]["lang"] == lang.lower()


def test_geocode_forwards_valid_bbox(monkeypatch):
    _patch_auth(monkeypatch, gw)
    resp, calls = _run_geocode(
        monkeypatch, gw, "/api/geocode?q=Alcala&bbox=-31.0,27.0,45.0,72.0"
    )
    assert resp.status_code == 200
    assert calls[0]["params"]["bbox"] == "-31.000000,27.000000,45.000000,72.000000"


def test_geocode_drops_invalid_bbox(monkeypatch):
    _patch_auth(monkeypatch, gw)
    for bad in ("-31,27,45", "a,b,c,d", "-31,27,45,20", "-200,27,45,72", "-31,27,45,95"):
        resp, calls = _run_geocode(monkeypatch, gw, f"/api/geocode?q=x&bbox={bad}")
        assert resp.status_code == 200, f"bbox={bad}"
        assert "bbox" not in calls[0]["params"], f"bbox={bad}"


def test_geocode_forwards_lat_lon_bias(monkeypatch):
    _patch_auth(monkeypatch, gw)
    resp, calls = _run_geocode(
        monkeypatch, gw, "/api/geocode?q=Alcala&lat=40.4168&lon=-3.7038"
    )
    assert resp.status_code == 200
    assert calls[0]["params"]["lat"] == "40.416800"
    assert calls[0]["params"]["lon"] == "-3.703800"


def test_geocode_drops_invalid_lat_lon(monkeypatch):
    _patch_auth(monkeypatch, gw)
    resp, calls = _run_geocode(monkeypatch, gw, "/api/geocode?q=x&lat=abc&lon=999")
    assert resp.status_code == 200
    assert "lat" not in calls[0]["params"]
    assert "lon" not in calls[0]["params"]


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def test_geocode_normalises_photon(monkeypatch):
    _patch_auth(monkeypatch, gw)
    resp, _ = _run_geocode(monkeypatch, gw, "/api/geocode?q=Pamplona&limit=5")
    assert resp.status_code == 200
    body = resp.get_json()
    r = body["results"][0]
    assert r["label"].startswith("Pamplona")
    assert r["name"] == "Pamplona"
    assert "Navarra" in r["secondary"] and "España" in r["secondary"]
    assert r["lat"] == 42.8125 and r["lon"] == -1.6458
    assert r["bbox"] == [-1.70, 42.77, -1.58, 42.86]
    assert r["type"] == "city" and r["countryCode"] == "ES"


def test_geocode_normalises_street_address(monkeypatch):
    _patch_auth(monkeypatch, gw)
    payload = {
        "features": [
            {
                "geometry": {"coordinates": [-1.644, 42.814]},
                "properties": {
                    "name": "5 Plaza del Castillo",
                    "street": "Plaza del Castillo",
                    "housenumber": "5",
                    "postcode": "31001",
                    "city": "Pamplona",
                    "state": "Navarra",
                    "country": "España",
                    "countrycode": "ES",
                    "osm_key": "place",
                    "osm_value": "house",
                },
            }
        ]
    }
    resp, _ = _run_geocode(
        monkeypatch, gw, "/api/geocode?q=Plaza+del+Castillo+5", response=_FakeOkResponse(payload)
    )
    r = resp.get_json()["results"][0]
    assert r["name"] == "5 Plaza del Castillo"
    assert r["type"] == "house"
    # context must not duplicate the primary name
    assert r["secondary"].startswith("Pamplona")


def test_geocode_rebuilds_name_when_missing(monkeypatch):
    _patch_auth(monkeypatch, gw)
    payload = {
        "features": [
            {
                "geometry": {"coordinates": [-1.64, 42.81]},
                "properties": {
                    "street": "Calle Mayor",
                    "housenumber": "12",
                    "city": "Pamplona",
                    "countrycode": "ES",
                    "osm_value": "house",
                },
            }
        ]
    }
    resp, _ = _run_geocode(
        monkeypatch, gw, "/api/geocode?q=Calle+Mayor+12", response=_FakeOkResponse(payload)
    )
    r = resp.get_json()["results"][0]
    assert r["name"] == "12 Calle Mayor"
    assert r["label"].startswith("12 Calle Mayor")


# ---------------------------------------------------------------------------
# Route guards (unchanged behaviour)
# ---------------------------------------------------------------------------

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
