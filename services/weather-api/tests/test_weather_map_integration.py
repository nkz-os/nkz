"""P1/P2: weather-map integration + altitude-aware station selection."""

import os
import sys
from unittest.mock import MagicMock, patch

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")
for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


def _stats_response(metrics):
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"metrics": metrics, "parcel_id": "urn:ngsi-ld:AgriParcel:x"}
    return m


def test_fetch_weather_map_stats_empty_when_unconfigured():
    from app.routers.parcels import _fetch_weather_map_stats
    with patch("app.routers.parcels.settings.weather_map_url", ""):
        assert _fetch_weather_map_stats("x", "montiko") == {}


def test_fetch_weather_map_stats_maps_fields():
    from app.routers.parcels import _fetch_weather_map_stats
    resp = _stats_response({
        "temperature_avg": {"mean": 18.5},
        "temperature_min": {"mean": 9.2},
        "eto": {"mean": 4.1},
        "soil_moisture": {"mean": 22.0},
    })
    with patch("app.routers.parcels.settings.weather_map_url", "http://wm:8080"):
        with patch("app.routers.parcels.requests.get", return_value=resp) as get:
            out = _fetch_weather_map_stats("urn:ngsi-ld:AgriParcel:x", "montiko")

    assert out["temp_avg"] == 18.5
    assert out["temp_min"] == 9.2
    assert out["eto_mm"] == 4.1
    assert out["soil_moisture_0_10cm"] == 22.0  # weather-map is already percent
    assert get.call_args.kwargs["headers"]["X-Tenant-ID"] == "montiko"
    assert "temperature_avg" in get.call_args.kwargs["params"]["metrics"]


def test_fetch_weather_map_stats_fails_safe():
    from app.routers.parcels import _fetch_weather_map_stats
    with patch("app.routers.parcels.settings.weather_map_url", "http://wm:8080"):
        with patch("app.routers.parcels.requests.get", side_effect=RuntimeError("down")):
            assert _fetch_weather_map_stats("x", "montiko") == {}

        bad = MagicMock()
        bad.status_code = 500
        with patch("app.routers.parcels.requests.get", return_value=bad):
            assert _fetch_weather_map_stats("x", "montiko") == {}


def test_altitude_weight_constant_defined():
    from app.routers.parcels import ALTITUDE_WEIGHT_KM_PER_100M
    # 100 m altitude == 10 km horizontal (documented heuristic).
    assert ALTITUDE_WEIGHT_KM_PER_100M == 10.0
