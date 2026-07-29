"""Tests for the parcel-scoped weather alerts endpoint and geo helpers."""

import os
import sys
from unittest.mock import MagicMock, patch

# ── Path setup (mirror the smoke test) ──────────────────────────────────────
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")
for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from app.geo import active_alert_filter, parcel_centroid


# ── Pure helpers ────────────────────────────────────────────────────────────
def test_centroid_of_point():
    e = {"location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [-1.64, 42.81]}}}
    assert parcel_centroid(e) == (-1.64, 42.81)


def test_centroid_of_polygon_is_mean_of_ring():
    ring = [[0, 0], [0, 2], [2, 2], [2, 0], [0, 0]]
    e = {"location": {"type": "GeoProperty", "value": {"type": "Polygon", "coordinates": [ring]}}}
    lon, lat = parcel_centroid(e)
    assert round(lon, 3) == 1.0 and round(lat, 3) == 1.0


def test_centroid_keyvalues_shape():
    e = {"location": {"type": "Point", "coordinates": [3.0, 4.0]}}
    assert parcel_centroid(e) == (3.0, 4.0)


def test_centroid_missing_location_returns_none():
    assert parcel_centroid({"id": "x"}) is None


def test_active_alert_filter_shape():
    assert active_alert_filter("2026-07-27T00:00:00Z") == 'validTo>"2026-07-27T00:00:00Z"'


# ── Endpoint ────────────────────────────────────────────────────────────────
def _resp(status, payload):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    m.text = ""
    m.content = b"x"
    return m


def _client():
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def test_parcel_alerts_returns_covering_alert():
    parcel = {
        "id": "urn:ngsi-ld:AgriParcel:t:1",
        "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [-1.64, 42.81]}},
    }
    alert = {"id": "urn:ngsi-ld:WeatherAlert:meteoalarm:x", "severity": {"value": "severe"}}

    def fake_get(url, **kw):
        if "/entities/" in url:  # parcel fetch from tenant store
            return _resp(200, parcel)
        return _resp(200, [alert])  # geo-query in tenant default

    with patch("app.routers.alerts.requests.get", side_effect=fake_get):
        r = _client().get(
            "/api/weather/parcel/urn:ngsi-ld:AgriParcel:t:1/alerts",
            headers={"X-Tenant-ID": "montiko"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1 and body["alerts"][0]["id"].endswith(":x")


def test_parcel_alerts_no_location_returns_empty():
    parcel = {"id": "urn:ngsi-ld:AgriParcel:t:2"}  # no location
    with patch("app.routers.alerts.requests.get", return_value=_resp(200, parcel)):
        r = _client().get(
            "/api/weather/parcel/urn:ngsi-ld:AgriParcel:t:2/alerts",
            headers={"X-Tenant-ID": "montiko"},
        )
    assert r.status_code == 200 and r.json()["count"] == 0


def test_parcel_alerts_requires_tenant():
    r = _client().get("/api/weather/parcel/urn:ngsi-ld:AgriParcel:t:3/alerts")
    assert r.status_code == 401
