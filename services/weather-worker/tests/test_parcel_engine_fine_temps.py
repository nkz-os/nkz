"""Day min/max of the running day come from the automatic model; the rest stays ECMWF."""

import copy
import logging
import os
import sys
from unittest.mock import MagicMock, patch

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

import weather_worker.parcel_engine as pe  # noqa: E402

D0, D1 = "2026-11-05", "2026-11-06"


def _engine():
    return pe.ParcelWeatherEngine(
        orion_url="http://orion.test", context_url="http://ctx.test/c.json",
        openmeteo_url="http://om.test/v1",
    )


def _ecmwf():
    return {
        "elevation": 500.0,
        "daily": {
            "time": [D0, D1],
            "temperature_2m_min": [4.0, 5.0],
            "temperature_2m_max": [14.0, 15.0],
            "temperature_2m_mean": [9.0, 10.0],
            "precipitation_sum": [1.5, 0.0],
            "et0_fao_evapotranspiration": [2.2, 2.4],
        },
        "hourly": {"time": [], "temperature_2m": []},
    }


def _fine(tmin=(-1.0, 2.0), tmax=(12.0, 13.0), days=(D0, D1)):
    return {
        "elevation": 480.0,
        "daily": {
            "time": list(days),
            "temperature_2m_min": list(tmin),
            "temperature_2m_max": list(tmax),
        },
    }


def test_fine_temps_replace_ecmwf_min_max_only():
    data = _ecmwf()
    snapshot = copy.deepcopy(data)
    out = _engine()._merge_fine_temps(data, _fine())
    assert out["daily"]["temperature_2m_min"][0] == -1.0
    assert out["daily"]["temperature_2m_max"][0] == 12.0
    for k in ("temperature_2m_mean", "precipitation_sum", "et0_fao_evapotranspiration", "time"):
        assert out["daily"][k] == snapshot["daily"][k]
    assert out["elevation"] == 500.0 and out["hourly"] == snapshot["hourly"]
    assert data == snapshot  # input not mutated (shared across parcels)
    obs = _engine()._parse_openmeteo_response(out, 42.8, -1.6)
    assert obs[0]["temp_min"] == -1.0 and obs[0]["temp_max"] == 12.0
    assert obs[0]["temp_avg"] == 9.0 and obs[0]["precip_mm"] == 1.5 and obs[0]["eto_mm"] == 2.2


def test_only_the_running_day_is_replaced():
    out = _engine()._merge_fine_temps(_ecmwf(), _fine())
    assert out["daily"]["temperature_2m_min"][1] == 5.0
    assert out["daily"]["temperature_2m_max"][1] == 15.0


def test_fine_fetch_sends_no_models_param():
    eng = _engine()
    eng.openmeteo_models = "ecmwf_ifs025"
    resp = MagicMock(status_code=200)
    resp.json.return_value = _fine()
    with patch.object(pe.requests, "get", return_value=resp) as g:
        out = eng._fetch_fine_temps(42.8, -1.6)
    assert out == _fine()
    assert "models" not in g.call_args.kwargs["params"]
    assert g.call_args.kwargs["timeout"] == 30
    p = g.call_args.kwargs["params"]
    assert p["daily"] == ["temperature_2m_min", "temperature_2m_max"]
    assert p["timezone"] == "Europe/Madrid" and p["start_date"] and p["end_date"]


def test_fine_fetch_failure_keeps_ecmwf_and_warns(caplog):
    eng = _engine()
    with patch.object(pe.requests, "get", side_effect=TimeoutError("slow")), \
         caplog.at_level(logging.DEBUG, logger=pe.logger.name):
        fine = eng._fetch_fine_temps(42.8, -1.6)
    assert fine is None
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warns and not any("42.8" in r.getMessage() for r in warns)
    data = _ecmwf()
    assert eng._merge_fine_temps(data, fine) == _ecmwf()


def test_fine_fetch_http_error_returns_none():
    eng = _engine()
    resp = MagicMock(status_code=503, text="busy")
    with patch.object(pe.requests, "get", return_value=resp):
        assert eng._fetch_fine_temps(42.8, -1.6) is None


def test_partial_fine_day_keeps_ecmwf_for_that_day():
    for fine in (_fine(tmin=(None, 2.0)), _fine(tmax=(None, 13.0))):
        out = _engine()._merge_fine_temps(_ecmwf(), fine)
        assert out["daily"]["temperature_2m_min"][0] == 4.0
        assert out["daily"]["temperature_2m_max"][0] == 14.0


def test_fine_response_for_other_dates_keeps_ecmwf():
    out = _engine()._merge_fine_temps(_ecmwf(), _fine(days=("2026-11-01", D1)))
    assert out["daily"]["temperature_2m_min"][0] == 4.0


def test_fine_min_above_ecmwf_mean_does_not_break_parse():
    out = _engine()._merge_fine_temps(_ecmwf(), _fine(tmin=(11.0, 2.0)))
    obs = _engine()._parse_openmeteo_response(out, 42.8, -1.6)
    assert obs[0]["temp_min"] == 11.0 and obs[0]["temp_avg"] == 9.0


def test_run_once_fetches_fine_temps_once_per_cluster():
    eng = _engine()
    parcels = [
        {"id": f"urn:ngsi-ld:AgriParcel:p{i}", "_tenant": "t-1", "_centroid": (-1.6, 42.8), "_altitude": 500.0}
        for i in (1, 2)
    ]
    written = []
    with patch.object(eng, "_fetch_all_parcels", return_value=[dict(p) for p in parcels]), \
         patch.object(eng, "_extract_centroid_and_altitude",
                      side_effect=lambda p: ((-1.6, 42.8), 500.0)), \
         patch.object(eng, "_ensure_parcel_timezones", return_value={}), \
         patch.object(eng, "_fetch_openmeteo", return_value=_ecmwf()) as f1, \
         patch.object(eng, "_fetch_fine_temps", return_value=_fine()) as f2, \
         patch.object(eng, "_compute_terrain_attributes", return_value=(0.0, 0.0)), \
         patch.object(eng, "_write_weather_observed", return_value={"created": 1}), \
         patch.object(eng, "_write_weather_forecast",
                      side_effect=lambda **k: written.append(k["daily"]) or True), \
         patch.object(eng, "_publish_closed_days", return_value={}), \
         patch.object(eng, "_get_active_tenants", return_value=[]), \
         patch.object(pe.time, "sleep"):
        eng.run_once()
    assert f1.call_count == 1 and f2.call_count == 1
    assert len(written) == 2
    # parcel at the grid altitude: no lapse shift, so the fine values arrive as is
    assert all(d["temp_min"] == -1.0 and d["temp_max"] == 12.0 for d in written)
