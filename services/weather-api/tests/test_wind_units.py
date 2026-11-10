"""Wind speed fields named *_ms must be metres per second, not Open-Meteo's km/h default."""
import os, sys
from unittest.mock import MagicMock, patch

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi.testclient import TestClient

from app.main import app
import app.routers.coordinates as coords
import app.routers.municipality_forecast as muni


def _upstream(wind_ms: float):
    """Fake Open-Meteo: answers in km/h unless the request asks for m/s."""
    def get(url, params=None, timeout=None):
        unit = (params or {}).get("wind_speed_unit", "kmh")
        wind = wind_ms if unit == "ms" else wind_ms * 3.6
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {
            "elevation": 100.0,
            "daily": {
                "time": ["2026-07-01"],
                "temperature_2m_max": [30.0], "temperature_2m_min": [15.0],
                "weather_code": [1], "precipitation_sum": [0.0],
                "precipitation_probability_max": [0],
                "wind_speed_10m_max": [wind], "wind_direction_10m_dominant": [270],
                "et0_fao_evapotranspiration": [5.0], "shortwave_radiation_sum": [25.0],
            },
        }
        return r
    return get


def test_coordinates_wind_is_ms():
    with patch.object(coords.requests, "get", side_effect=_upstream(10.0)):
        r = TestClient(app).get("/api/weather/coordinates?lat=42.0&lon=-2.0&days=1")
    assert r.status_code == 200, r.text
    assert r.json()["forecast"][0]["wind_speed_ms"] == 10.0


def test_municipality_wind_is_ms():
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.return_value = {
        "name": "X", "province": "Y", "latitude": 42.0, "longitude": -2.0, "aemet_id": None,
    }
    with patch("app.deps.get_db_connection", return_value=conn), \
         patch.object(muni.requests, "get", side_effect=_upstream(10.0)):
        r = TestClient(app).get("/api/weather/municipality/00000/forecast?days=1")
    assert r.status_code == 200, r.text
    assert r.json()["forecast"][0]["wind_speed_ms"] == 10.0
