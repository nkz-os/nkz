"""Fill plan by window, Open-Meteo mapping, and failure → nothing filled."""
import os, sys
from datetime import date, datetime, timezone
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
         patch.object(df.settings, "openmeteo_daily_models", "ecmwf_ifs025"):
        get = MagicMock(return_value=_resp({"daily": {"time": []}}))
        df.fetch_open_meteo_daily("model_analysis", 1, 1, None, date(2026, 9, 1), date(2026, 9, 3), http_get=get)
    kw = get.call_args.kwargs
    assert get.call_args.args[0] == "http://om/v1/forecast" and kw["timeout"] == 30
    assert kw["params"]["start_date"] == "2026-09-01" and kw["params"]["end_date"] == "2026-09-03"
    assert kw["params"]["models"] == "ecmwf_ifs025" and "elevation" not in kw["params"]


def test_malformed_daily_returns_empty():
    for payload in ({"daily": "oops"}, {"daily": {"time": ["2026-10-01"], "temperature_2m_min": "x"}}, ["not", "a", "dict"]):
        get = MagicMock(return_value=_resp(payload))
        out = df.fetch_open_meteo_daily("model_analysis", 1, 1, None, date(2026, 10, 1), date(2026, 10, 1), http_get=get)
        assert out == {} or all(v is None for rec in out.values() for v in rec.values())


def test_timeout_override_is_used():
    with patch.object(df.settings, "openmeteo_api_url", "http://om/v1"):
        get = MagicMock(return_value=_resp({"daily": {"time": []}}))
        df.fetch_open_meteo_daily("model_analysis", 1, 1, None, date(2026, 9, 1), date(2026, 9, 1), http_get=get, timeout=7)
    assert get.call_args.kwargs["timeout"] == 7


def test_fill_model_analysis_uses_daily_models_setting():
    # Empty OPENMETEO_DAILY_MODELS = the service's automatic selection: no `models` sent,
    # even when the legacy OPENMETEO_MODELS is set.
    with patch.object(df.settings, "openmeteo_api_url", "http://om/v1"), \
         patch.object(df.settings, "openmeteo_models", "ecmwf_ifs025"), \
         patch.object(df.settings, "openmeteo_daily_models", ""):
        get = MagicMock(return_value=_resp({"daily": {"time": []}}))
        df.fetch_open_meteo_daily("model_analysis", 1, 1, None, date(2026, 9, 1), date(2026, 9, 1), http_get=get)
    assert "models" not in get.call_args.kwargs["params"]


# ---- forecast -------------------------------------------------------------

AUTO_TMIN, ECMWF_TMIN = -1.5, -3.0
NOW = datetime(2026, 11, 4, 23, 30, tzinfo=timezone.utc)  # parcel already on 2026-11-05


def _forecast_payload(ecmwf_tmin=None, hourly=None):
    days = ["2026-11-05", "2026-11-06", "2026-11-07"]
    ecmwf = ecmwf_tmin if ecmwf_tmin is not None else [ECMWF_TMIN] * 3
    if hourly is None:
        hourly = [5.0] * 72
    hours = [f"{d}T{h:02d}:00" for d in days for h in range(24)]
    return {
        "utc_offset_seconds": 3600,
        "daily": {
            "time": days,
            "temperature_2m_min_best_match": [AUTO_TMIN] * 3,
            "temperature_2m_max_best_match": [12.0] * 3,
            "precipitation_sum_best_match": [0.4] * 3,
            "et0_fao_evapotranspiration_best_match": [1.1] * 3,
            "shortwave_radiation_sum_best_match": [7.0] * 3,
            "wind_speed_10m_mean_best_match": [2.0] * 3,
            "temperature_2m_min_ecmwf_ifs025": ecmwf,
            "temperature_2m_max_ecmwf_ifs025": [11.0] * 3,
        },
        "hourly": {"time": hours, "temperature_2m_best_match": hourly},
    }


def _forecast(payload, status=200):
    get = MagicMock(return_value=_resp(payload, status))
    with patch.object(df.settings, "openmeteo_api_url", "http://om/v1"):
        out = df.fetch_open_meteo_forecast(10.0, 10.0, 300.0, 2, now=NOW, http_get=get)
    return out, get


def test_fetch_forecast_uses_automatic_selection_and_marks_today():
    out, get = _forecast(_forecast_payload())
    kw = get.call_args.kwargs
    assert get.call_args.args[0] == "http://om/v1/forecast"
    assert kw["params"]["models"] == "best_match,ecmwf_ifs025"
    assert kw["params"]["timezone"] == "auto" and kw["params"]["past_days"] == 0
    assert kw["params"]["forecast_days"] == 3 and kw["params"]["elevation"] == 300.0
    assert out["days"]["2026-11-06"]["tmin_c"] == AUTO_TMIN
    assert out["days"]["2026-11-06"]["tmin_coldest_c"] == min(AUTO_TMIN, ECMWF_TMIN)
    assert out["days"]["2026-11-06"]["wind2m_ms"] == round(2.0 * 0.748, 3)
    assert out["local_today"] == "2026-11-05"
    assert out["issued_at"] == NOW.isoformat()
    assert out["days"]["2026-11-05"]["kind"] == "running"
    assert out["days"]["2026-11-06"]["kind"] == "forecast"
    assert out["days"]["2026-11-06"]["source"] == "model_forecast"


def test_hours_below_zero_counts_hourly_values_at_or_below_zero():
    hourly = [5.0] * 72
    hourly[24 + 4], hourly[24 + 5], hourly[24 + 6] = -0.5, 0.0, -2.0
    out, _ = _forecast(_forecast_payload(hourly=hourly))
    assert out["days"]["2026-11-06"]["hours_below_zero"] == 3.0
    assert out["days"]["2026-11-07"]["hours_below_zero"] == 0.0


def test_hours_below_zero_is_null_when_a_day_has_missing_hours():
    hourly = [5.0] * 72
    hourly[30] = None
    out, _ = _forecast(_forecast_payload(hourly=hourly))
    assert out["days"]["2026-11-06"]["hours_below_zero"] is None
    assert out["days"]["2026-11-07"]["hours_below_zero"] == 0.0


def test_fetch_forecast_failure_returns_empty():
    out, _ = _forecast({"error": True}, status=500)
    assert out == {}
    get = MagicMock(side_effect=TimeoutError("slow"))
    assert df.fetch_open_meteo_forecast(1, 1, None, 2, now=NOW, http_get=get) == {}
    # no utc offset: the local day cannot be told, so nothing is served
    payload = _forecast_payload(); del payload["utc_offset_seconds"]
    assert _forecast(payload)[0] == {}


def test_coldest_is_null_if_one_model_missing():
    out, _ = _forecast(_forecast_payload(ecmwf_tmin=[ECMWF_TMIN, None, None]))
    assert out["days"]["2026-11-05"]["tmin_coldest_c"] == min(AUTO_TMIN, ECMWF_TMIN)
    day = out["days"]["2026-11-07"]
    assert day["tmin_coldest_c"] is None          # never the single-model value
    assert day["tmin_c"] == AUTO_TMIN             # automatic values still served


def test_forecast_days_request_is_capped_at_open_meteo_limit():
    get = MagicMock(return_value=_resp({"daily": {"time": []}, "utc_offset_seconds": 0}))
    df.fetch_open_meteo_forecast(1, 1, None, 16, now=NOW, http_get=get)
    assert get.call_args.kwargs["params"]["forecast_days"] == 16
    assert "elevation" not in get.call_args.kwargs["params"]


def test_forecast_zero_elevation_is_not_sent():
    get = MagicMock(return_value=_resp(_forecast_payload()))
    with patch.object(df.settings, "openmeteo_api_url", "http://om/v1"):
        df.fetch_open_meteo_forecast(10.0, 10.0, 0.0, 2, now=NOW, http_get=get)
    assert "elevation" not in get.call_args.kwargs["params"]


def test_daily_fill_zero_elevation_is_not_sent():
    get = MagicMock(return_value=_resp({"daily": {"time": []}}))
    with patch.object(df.settings, "openmeteo_api_url", "http://om/v1"):
        df.fetch_open_meteo_daily("model_analysis", 10.0, 10.0, 0.0,
                                  date(2026, 11, 1), date(2026, 11, 2), http_get=get)
    assert "elevation" not in get.call_args.kwargs["params"]


def test_forecast_malformed_body_returns_empty_not_raises():
    payload = _forecast_payload()
    payload["daily"]["time"] = [["x"]]
    out, _ = _forecast(payload)
    assert out == {}


def test_local_now_uses_offset_at_utc_boundary():
    out, _ = _forecast(_forecast_payload())
    assert out["local_now"] == "2026-11-05T00:30"
    assert out["local_now"][:10] == out["local_today"]
