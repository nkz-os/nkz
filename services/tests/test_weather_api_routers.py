"""TestClient suite for weather-api routers — Orion-LD, DB and Open-Meteo mocked.

Covers: /health, parcel agro-status (auth, 404, 400, happy path, fail-safe
degradation, Orion WeatherObserved fallback), sensor cross-validation,
location resolution, observations endpoints + telemetry fallback, alerts,
coordinates forecast, and NGSI-LD compliance of the agroStatus persist call.
"""

import sys
import os
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "weather-api"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers.parcels import (  # noqa: E402
    _cross_validate_sensors,
    _persist_agro_status_to_orion,
    _resolve_parcel_location,
)

client = TestClient(app)

AUTH = {"X-Tenant-ID": "test-tenant"}
PARCEL_ID = "urn:ngsi-ld:AgriParcel:test-tenant:p1"
NOW = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)

PARCEL_ENTITY = {
    "id": PARCEL_ID,
    "type": "AgriParcel",
    "name": {"type": "Property", "value": "Olivar Sur"},
    "location": {
        "type": "GeoProperty",
        "value": {"type": "Point", "coordinates": [-1.64, 42.49]},
    },
    "elevation": {"type": "Property", "value": 450.0},
}

LATEST_OBS_ROW = {
    "observed_at": NOW,
    "temp_avg": 22.0, "temp_min": 14.0, "temp_max": 27.0,
    "humidity_avg": 55.0, "precip_mm": 0.0, "precip_probability": 10,
    "wind_speed_ms": 3.0, "wind_gusts_ms": 4.0, "wind_direction_deg": 180,
    "pressure_hpa": 1013.0,
    "solar_rad_w_m2": None, "solar_rad_ghi_w_m2": None, "solar_rad_dni_w_m2": None,
    "eto_mm": 4.0, "soil_moisture_0_10cm": None, "soil_moisture_10_40cm": None,
    "gdd_accumulated": None, "delta_t": None,
    "source": "OPEN-METEO", "data_type": "HISTORY", "metadata": {},
}


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class FakeCursor:
    """Scripted cursor: one result per execute(), consumed in call order."""

    def __init__(self, script):
        self._script = script
        self._current = None

    def execute(self, query, params=None):
        self._current = self._script.pop(0) if self._script else None

    def fetchone(self):
        return self._current

    def fetchall(self):
        return self._current or []

    def close(self):
        pass


class FakeConn:
    def __init__(self, script):
        self._script = script

    def cursor(self, cursor_factory=None):
        return FakeCursor(self._script)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_db(script):
    """get_db_connection replacement; the script is shared across connections."""
    shared = list(script)
    return lambda tenant_id="default": FakeConn(shared)


def db_down(tenant_id="default"):
    raise RuntimeError("db down")


def orion_response(status=200, payload=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload if payload is not None else {}
    resp.text = json.dumps(payload or {})
    return resp


def orion_router(parcel=None, soil=None, weather_observed=None):
    """Side effect for app.routers.parcels.requests.get, dispatching by URL."""

    def _get(url, **kwargs):
        params = kwargs.get("params") or {}
        if url.endswith(f"/entities/{PARCEL_ID}"):
            if parcel is None:
                return orion_response(404, {"title": "Not Found"})
            return orion_response(200, parcel)
        if params.get("type") == "AgriSoil":
            return orion_response(200, soil if soil is not None else [])
        if params.get("type") == "WeatherObserved":
            return orion_response(
                200, weather_observed if weather_observed is not None else []
            )
        return orion_response(404, {})

    return _get


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
def test_health_liveness():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "healthy", "service": "weather-api"}


# ---------------------------------------------------------------------------
# Agro-status endpoint
# ---------------------------------------------------------------------------
class TestAgroStatusEndpoint:
    URL = f"/api/weather/parcel/{PARCEL_ID}/agro-status"

    def test_requires_auth(self):
        assert client.get(self.URL).status_code == 401

    def test_parcel_not_found_in_orion(self):
        with patch("app.routers.parcels.requests.get",
                   side_effect=orion_router(parcel=None)):
            r = client.get(self.URL, headers=AUTH)
        assert r.status_code == 404

    def test_parcel_without_geometry_is_400(self):
        bare = {k: v for k, v in PARCEL_ENTITY.items() if k != "location"}
        with patch("app.routers.parcels.requests.get",
                   side_effect=orion_router(parcel=bare)):
            r = client.get(self.URL, headers=AUTH)
        assert r.status_code == 400

    def test_happy_path_with_db_weather(self):
        script = [
            [],  # sensors near parcel → none
            {"municipality_code": "31201", "station_elevation_m": "445"},  # nearest
            LATEST_OBS_ROW,  # latest observation
            [{"precip_mm": 2.0, "eto_mm": 1.0, "observed_at": NOW}],  # 3-day history
        ]
        with patch("app.routers.parcels.requests.get",
                   side_effect=orion_router(parcel=PARCEL_ENTITY)), \
             patch("app.routers.parcels.requests.patch",
                   return_value=orion_response(204)) as persist, \
             patch("app.routers.parcels.get_db_connection", fake_db(script)):
            r = client.get(self.URL, headers=AUTH)

        assert r.status_code == 200
        body = r.json()
        assert body["parcel_id"] == PARCEL_ID
        assert set(body["semaphores"]) == {"spraying", "workability", "irrigation"}
        assert body["semaphores"]["spraying"] != "unknown"
        assert body["weather"]["temperature"] == 22.0
        assert body["source_confidence"] == "WEATHER-OBS"
        persist.assert_called_once()  # agroStatus written back to Orion

    def test_db_down_degrades_gracefully_never_500(self):
        """Fail-safe mandate: DB outage must degrade, not 500."""
        with patch("app.routers.parcels.requests.get",
                   side_effect=orion_router(parcel=PARCEL_ENTITY)), \
             patch("app.routers.parcels.requests.patch",
                   return_value=orion_response(204)), \
             patch("app.routers.parcels.get_db_connection", db_down):
            r = client.get(self.URL, headers=AUTH)

        assert r.status_code == 200
        body = r.json()
        assert body["source_confidence"] == "UNAVAILABLE"
        assert body["semaphores"] == {
            "spraying": "unknown", "workability": "unknown", "irrigation": "unknown"
        }
        assert "no_data_reason" in body

    def test_orion_weatherobserved_fallback_when_db_down(self):
        wo = {
            "id": "urn:ngsi-ld:WeatherObserved:test-tenant:p1",
            "type": "WeatherObserved",
            "dateObserved": {
                "type": "Property",
                "value": {"@type": "DateTime", "@value": "2026-06-11T11:00:00Z"},
            },
            "temperature": {"type": "Property", "value": 21.0},
            "relativeHumidity": {"type": "Property", "value": 60.0},
            "windSpeed": {"type": "Property", "value": 2.5},
            "precipitation": {"type": "Property", "value": 0.0},
            "et0": {"type": "Property", "value": 3.0},
            "stationElevation": {"type": "Property", "value": 445},
        }
        with patch("app.routers.parcels.requests.get",
                   side_effect=orion_router(parcel=PARCEL_ENTITY,
                                            weather_observed=[wo])), \
             patch("app.routers.parcels.requests.patch",
                   return_value=orion_response(204)), \
             patch("app.routers.parcels.get_db_connection", db_down):
            r = client.get(self.URL, headers=AUTH)

        assert r.status_code == 200
        body = r.json()
        assert body["weather"]["temperature"] == 21.0
        assert body["source_confidence"] == "WEATHER-OBS"
        assert body["semaphores"]["spraying"] != "unknown"


# ---------------------------------------------------------------------------
# Sensor cross-validation (pure)
# ---------------------------------------------------------------------------
class TestCrossValidateSensors:
    @staticmethod
    def _sensor(sid, dist, payload):
        return {
            "external_id": sid, "name": sid, "distance_m": dist,
            "observed_at": "2026-06-11T10:00:00Z", "payload": dict(payload),
        }

    def test_median_fusion(self):
        sensors = [
            self._sensor("a", 100, {"temperature": 20.0}),
            self._sensor("b", 200, {"temperature": 22.0}),
            self._sensor("c", 300, {"temperature": 21.0}),
        ]
        out = _cross_validate_sensors(sensors)
        assert out["validation"]["status"] == "cross_validated"
        assert out["validation"]["unreliable_ids"] == []
        assert out["payload"]["temperature"] == 21.0  # median of 20/21/22

    def test_outlier_over_30pct_from_median_is_excluded(self):
        sensors = [
            self._sensor("a", 100, {"temperature": 20.0}),
            self._sensor("b", 200, {"temperature": 21.0}),
            self._sensor("c", 300, {"temperature": 80.0}),  # broken sensor
        ]
        out = _cross_validate_sensors(sensors)
        assert out["validation"]["unreliable_ids"] == ["c"]
        assert out["validation"]["reliable_sensors"] == 2
        assert out["payload"]["temperature"] == 20.5  # median of survivors

    def test_single_sensor_passthrough(self):
        s = self._sensor("solo", 50, {"temperature": 19.0})
        assert _cross_validate_sensors([s]) is s


# ---------------------------------------------------------------------------
# Parcel location resolution (pure)
# ---------------------------------------------------------------------------
class TestResolveParcelLocation:
    def test_point(self):
        assert _resolve_parcel_location(PARCEL_ENTITY) == (-1.64, 42.49)

    def test_polygon_centroid(self):
        entity = {
            "location": {
                "type": "GeoProperty",
                "value": {
                    "type": "Polygon",
                    "coordinates": [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]],
                },
            }
        }
        assert _resolve_parcel_location(entity) == (1.0, 1.0)

    def test_unwrapped_geojson_value(self):
        entity = {"location": {"type": "Point", "coordinates": [-1.5, 42.0]}}
        assert _resolve_parcel_location(entity) == (-1.5, 42.0)

    def test_missing_location_returns_none(self):
        assert _resolve_parcel_location({"id": "urn:x"}) is None


# ---------------------------------------------------------------------------
# Observations endpoints
# ---------------------------------------------------------------------------
class TestObservationsEndpoints:
    def test_latest_primary_source(self):
        rows = [dict(LATEST_OBS_ROW, municipality_code="31201")]
        with patch("app.routers.observations.get_db_connection", fake_db([rows])):
            r = client.get("/api/weather/observations/latest", headers=AUTH)
        assert r.status_code == 200
        obs = r.json()["observations"]
        assert len(obs) == 1
        assert obs[0]["temp_avg"] == 22.0

    def test_latest_falls_back_to_telemetry_events(self):
        telemetry_row = {
            "entity_id": "urn:ngsi-ld:WeatherObserved:test-tenant:p1",
            "observed_at": NOW,
            "measurements_raw": {"temperature": 19.0, "municipalityCode": "31201"},
            "location_raw": {
                "type": "GeoProperty",
                "value": {"type": "Point", "coordinates": [-1.6, 42.5]},
            },
        }
        script = [[], [telemetry_row]]  # weather_observations empty → telemetry
        with patch("app.routers.observations.get_db_connection", fake_db(script)):
            r = client.get("/api/weather/observations/latest", headers=AUTH)
        assert r.status_code == 200
        obs = r.json()["observations"]
        assert obs[0]["temp_avg"] == 19.0  # NGSI-LD attr mapped to DB column name
        assert obs[0]["longitude"] == -1.6
        assert obs[0]["latitude"] == 42.5

    def test_observations_with_filters(self):
        rows = [
            dict(LATEST_OBS_ROW, municipality_code="31201"),
            dict(LATEST_OBS_ROW, municipality_code="31201", temp_avg=20.0),
        ]
        with patch("app.routers.observations.get_db_connection", fake_db([rows])):
            r = client.get(
                "/api/weather/observations",
                params={"municipality_code": "31201", "data_type": "HISTORY"},
                headers=AUTH,
            )
        assert r.status_code == 200
        assert r.json()["count"] == 2

    def test_db_error_returns_500(self):
        with patch("app.routers.observations.get_db_connection", db_down):
            r = client.get("/api/weather/observations", headers=AUTH)
        assert r.status_code == 500
        assert r.json() == {"error": "Database error"}


# ---------------------------------------------------------------------------
# Alerts endpoint
# ---------------------------------------------------------------------------
class TestAlertsEndpoint:
    def test_requires_auth(self):
        assert client.get("/api/weather/alerts").status_code == 401

    def test_returns_alerts_sorted_by_severity(self):
        alerts = [
            {"id": "urn:ngsi-ld:WeatherAlert:1", "type": "WeatherAlert",
             "severity": {"type": "Property", "value": "minor"}},
            {"id": "urn:ngsi-ld:WeatherAlert:2", "type": "WeatherAlert",
             "severity": {"type": "Property", "value": "severe"}},
        ]
        with patch("app.routers.alerts.requests.get",
                   return_value=orion_response(200, alerts)) as orion_get:
            r = client.get("/api/weather/alerts", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 2
        assert body["alerts"][0]["id"].endswith(":2")  # severe first
        # Alerts are geographic → always queried in the 'default' tenant
        headers = orion_get.call_args.kwargs["headers"]
        assert headers["NGSILD-Tenant"] == "default"

    def test_orion_error_returns_empty_list(self):
        with patch("app.routers.alerts.requests.get",
                   return_value=orion_response(500, {"title": "boom"})):
            r = client.get("/api/weather/alerts", headers=AUTH)
        assert r.status_code == 200
        assert r.json() == {"alerts": [], "count": 0, "source": "orion-ld"}


# ---------------------------------------------------------------------------
# Coordinates forecast endpoint
# ---------------------------------------------------------------------------
class TestCoordinatesEndpoint:
    def test_maps_openmeteo_daily_arrays(self):
        daily = {
            "time": ["2026-06-11", "2026-06-12"],
            "temperature_2m_max": [25.0, 26.0],
            "temperature_2m_min": [12.0, None],
            "weather_code": [1, 2],
            "precipitation_sum": [0.0, 4.2],
            "precipitation_probability_max": [5, 60],
            "wind_speed_10m_max": [3.2, 5.0],
            "wind_direction_10m_dominant": [180, 200],
            "et0_fao_evapotranspiration": [4.1, 3.0],
            "shortwave_radiation_sum": [25.92, None],
        }
        payload = {"daily": daily, "elevation": 420.0}
        with patch("app.routers.coordinates.requests.get",
                   return_value=orion_response(200, payload)):
            r = client.get("/api/weather/coordinates",
                           params={"lat": 42.5, "lon": -1.6, "days": 2})
        assert r.status_code == 200
        body = r.json()
        assert body["elevation_m"] == 420.0
        assert len(body["forecast"]) == 2
        # MJ/m²/day → W/m² conversion (÷ 0.0864)
        assert body["forecast"][0]["solar_rad_w_m2"] == 300.0
        assert body["forecast"][1]["temp_min"] is None  # None survives mapping

    def test_upstream_error_returns_502(self):
        with patch("app.routers.coordinates.requests.get",
                   return_value=orion_response(500)):
            r = client.get("/api/weather/coordinates",
                           params={"lat": 42.5, "lon": -1.6})
        assert r.status_code == 502

    def test_invalid_latitude_is_422(self):
        r = client.get("/api/weather/coordinates", params={"lat": 999, "lon": -1.6})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# agroStatus persist — NGSI-LD compliance (platform mandate)
# ---------------------------------------------------------------------------
class TestPersistAgroStatus:
    RESULT = {
        "semaphores": {"spraying": "optimal", "workability": "optimal",
                       "irrigation": "satisfied"},
        "soil": {"texture_applied": True, "texture_class": "loam",
                 "field_capacity": 0.32, "wilting_point": 0.12},
        "metrics": {"delta_t": 5.0, "water_balance": 6.0, "spraying_reason": None},
        "timestamp": "2026-06-11T12:00:00+00:00",
        "source_confidence": "WEATHER-OBS",
        "downscaling": "applied",
    }

    def test_attrs_patch_is_ngsild_compliant(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "context_url",
                            "http://context-test/ngsi-context.jsonld")
        with patch("app.routers.parcels.requests.patch",
                   return_value=orion_response(204)) as mock_patch:
            _persist_agro_status_to_orion("test-tenant", PARCEL_ID, self.RESULT)

        assert mock_patch.call_count == 1
        url = mock_patch.call_args.args[0]
        assert url.endswith(f"/entities/{PARCEL_ID}/attrs")
        headers = mock_patch.call_args.kwargs["headers"]
        # Attribute fragments MUST go as application/json + Link (CLAUDE.md §3)
        assert headers["Content-Type"] == "application/json"
        assert "ngsi-context.jsonld" in headers.get("Link", "")
        assert headers["NGSILD-Tenant"] == "test-tenant"
        assert headers["Fiware-Service"] == "test-tenant"
        body = mock_patch.call_args.kwargs["json"]
        assert "@context" not in body
        assert body["agroStatus"]["type"] == "Property"
        value = body["agroStatus"]["value"]
        assert value["spraying"] == "optimal"
        assert value["downscalingApplied"] is True
        assert value["soilTexture"] == "loam"
        assert value["deltaT"] == 5.0

    def test_persist_failure_is_swallowed(self):
        with patch("app.routers.parcels.requests.patch",
                   side_effect=RuntimeError("orion down")):
            _persist_agro_status_to_orion("t", PARCEL_ID, {})  # must not raise
