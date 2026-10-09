"""Fill plan by window, Open-Meteo mapping, and failure → nothing filled."""
import os, sys
from datetime import date
from unittest.mock import MagicMock, patch
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import app.services.daily_fill as df

TODAY = date(2026, 10, 9)


def test_plan_fill_windows():
    days = [date(2026, 1, 1), date(2026, 7, 10), date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 10)]
    p = df.plan_fill(days, TODAY)
    assert p["reanalysis"] == [date(2026, 1, 1)]          # older than 92 days
    assert p["model_analysis"] == [date(2026, 7, 10), date(2026, 10, 8)]  # 7-09 is today-92
    assert p["not_closed"] == [date(2026, 10, 9), date(2026, 10, 10)]


def _resp(payload, status=200):
    r = MagicMock(); r.status_code = status; r.json.return_value = payload
    return r


DAILY = {"time": ["2026-06-01", "2026-06-02"],
         "temperature_2m_min": [10.9, None], "temperature_2m_max": [27.0, None],
         "precipitation_sum": [0.0, None], "et0_fao_evapotranspiration": [5.41, None],
         "shortwave_radiation_sum": [26.76, None], "wind_speed_10m_mean": [2.52, None]}


def test_fetch_maps_fields_and_drops_empty_days():
    with patch.object(df.settings, "openmeteo_archive_url", "http://om/v1"), \
         patch.object(df.settings, "openmeteo_archive_models", "era5_seamless"):
        get = MagicMock(return_value=_resp({"daily": DAILY}))
        out = df.fetch_open_meteo_daily("reanalysis", 42.6, -2.1, 580.0,
                                        date(2026, 6, 1), date(2026, 6, 2), http_get=get)
    assert list(out) == ["2026-06-01"]
    d = out["2026-06-01"]
    assert d["tmin_c"] == 10.9 and d["precip_mm"] == 0.0 and d["radiation_mj_m2"] == 26.76
    assert d["wind2m_ms"] == round(2.52 * 0.748, 3) and d["vapour_pressure_kpa"] is None
    url, kw = get.call_args.args[0], get.call_args.kwargs
    assert url == "http://om/v1/archive" and kw["timeout"] == 120
    assert kw["params"]["models"] == "era5_seamless" and kw["params"]["elevation"] == 580.0


def test_archive_disabled_marks_unavailable():
    with patch.object(df.settings, "openmeteo_archive_url", ""):
        get = MagicMock()
        assert df.fetch_open_meteo_daily("reanalysis", 1, 1, None, date(2026, 1, 1), date(2026, 1, 2), http_get=get) == {}
        get.assert_not_called()


def test_fetch_error_payload_returns_empty():
    get = MagicMock(return_value=_resp({"error": True, "reason": "out of range"}, 400))
    assert df.fetch_open_meteo_daily("model_analysis", 1, 1, None, date(2026, 10, 1), date(2026, 10, 2), http_get=get) == {}
    get2 = MagicMock(side_effect=TimeoutError("slow"))
    assert df.fetch_open_meteo_daily("model_analysis", 1, 1, None, date(2026, 10, 1), date(2026, 10, 2), http_get=get2) == {}


def test_model_analysis_uses_forecast_past_days():
    with patch.object(df.settings, "openmeteo_api_url", "http://om/v1"), \
         patch.object(df.settings, "openmeteo_models", "ecmwf_ifs025"):
        get = MagicMock(return_value=_resp({"daily": {"time": []}}))
        df.fetch_open_meteo_daily("model_analysis", 1, 1, None, date(2026, 9, 1), date(2026, 9, 3), http_get=get)
    kw = get.call_args.kwargs
    assert get.call_args.args[0] == "http://om/v1/forecast" and kw["timeout"] == 30
    assert kw["params"]["start_date"] == "2026-09-01" and kw["params"]["end_date"] == "2026-09-03"
    assert kw["params"]["models"] == "ecmwf_ifs025" and "elevation" not in kw["params"]
