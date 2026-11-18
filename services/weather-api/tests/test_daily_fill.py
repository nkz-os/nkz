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
        get = MagicMock(return_value=_resp({**_cell(), "daily": DAILY}))
        out = df.fetch_open_meteo_daily("reanalysis", 42.6, -2.1, 580.0,
                                        date(2026, 6, 1), date(2026, 6, 2), http_get=get)
    assert list(out) == ["2026-06-01"]
    d = out["2026-06-01"]
    assert d["tmin_c"] == 10.9 and d["precip_mm"] == 0.0 and d["radiation_mj_m2"] == 26.76
    assert d["wind2m_ms"] == round(2.52 * 0.748, 3) and d["vapour_pressure_kpa"] is None
    url, kw = get.call_args_list[0].args[0], get.call_args_list[0].kwargs
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
        "latitude": 42.6, "longitude": -2.1, "elevation": 450.0,
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
    kw = get.call_args_list[0].kwargs
    assert get.call_args_list[0].args[0] == "http://om/v1/forecast"
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
    assert get.call_args_list[0].kwargs["params"]["forecast_days"] == 16
    assert "elevation" not in get.call_args_list[0].kwargs["params"]


def test_forecast_zero_elevation_is_not_sent():
    get = MagicMock(return_value=_resp(_forecast_payload()))
    with patch.object(df.settings, "openmeteo_api_url", "http://om/v1"):
        df.fetch_open_meteo_forecast(10.0, 10.0, 0.0, 2, now=NOW, http_get=get)
    assert "elevation" not in get.call_args_list[0].kwargs["params"]


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


def _vp(td):
    import math
    return round(0.6108 * math.exp(17.27 * td / (td + 237.3)), 3)


def test_dew_point_10c_gives_1_228_kpa():
    assert _vp(10.0) == 1.228


def test_filled_day_has_vapour_pressure():
    daily = {**DAILY, "dew_point_2m_mean": [10.0, None]}
    get = MagicMock(return_value=_resp({**_cell(), "daily": daily}))
    out = df.fetch_open_meteo_daily("model_analysis", 1, 1, None, date(2026, 6, 1), date(2026, 6, 2), http_get=get)
    assert out["2026-06-01"]["vapour_pressure_kpa"] == 1.228
    assert "dew_point_2m_mean" in get.call_args_list[0].kwargs["params"]["daily"]


def test_missing_dew_point_gives_null_vapour_pressure():
    get = MagicMock(return_value=_resp({**_cell(), "daily": DAILY}))
    out = df.fetch_open_meteo_daily("model_analysis", 1, 1, None, date(2026, 6, 1), date(2026, 6, 2), http_get=get)
    assert out["2026-06-01"]["vapour_pressure_kpa"] is None


def test_forecast_day_has_vapour_pressure_from_dew_point():
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    daily = {"time": ["2026-10-09", "2026-10-10"], "utc_offset_seconds": 0}
    for name, v in (("temperature_2m_min", 8.0), ("temperature_2m_max", 20.0), ("precipitation_sum", 0.0),
                    ("et0_fao_evapotranspiration", 2.0), ("shortwave_radiation_sum", 12.0),
                    ("wind_speed_10m_mean", 2.0)):
        daily[f"{name}_best_match"] = [v, v]
        daily[f"{name}_ecmwf_ifs025"] = [v, v]
    daily["dew_point_2m_mean_best_match"] = [10.0, None]
    daily["dew_point_2m_mean_ecmwf_ifs025"] = [3.0, 3.0]
    body = {**_cell(), "daily": daily, "hourly": {}, "utc_offset_seconds": 0}
    get = MagicMock(return_value=_resp(body))
    with patch.object(df.settings, "openmeteo_api_url", "http://om/v1"):
        out = df.fetch_open_meteo_forecast(42.6, -2.1, 580.0, 1, now=now, http_get=get)
    assert out["days"]["2026-10-09"]["vapour_pressure_kpa"] == 1.228
    assert out["days"]["2026-10-10"]["vapour_pressure_kpa"] is None
    assert "dew_point_2m_mean" in get.call_args_list[0].kwargs["params"]["daily"]


# ---- Tmin from the raw model cell (elevation=nan), same rule as the worker ---

RAW_TMIN, CORR_TMIN = -4.0, -1.0


def _cell(lat=42.6, lon=-2.1, elev=450.0):
    return {"latitude": lat, "longitude": lon, "elevation": elev}


def _routed(corrected, raw, fail_raw=False):
    """http_get answering the elevated call with `corrected` and the elevation=nan call with `raw`."""
    def get(url, params=None, timeout=None):
        if params.get("elevation") == "nan":
            if fail_raw:
                raise TimeoutError("slow")
            return _resp(raw)
        return _resp(corrected)
    return MagicMock(side_effect=get)


def _daily_bodies(corr_cell=None, raw_cell=None, raw_tmin=RAW_TMIN):
    corr = {**(corr_cell or _cell()), "daily": {
        "time": ["2026-06-01"], "temperature_2m_min": [CORR_TMIN], "temperature_2m_max": [27.0],
        "precipitation_sum": [0.0], "et0_fao_evapotranspiration": [5.0],
        "shortwave_radiation_sum": [20.0], "wind_speed_10m_mean": [2.0]}}
    raw = {**(raw_cell or _cell()), "daily": {"time": ["2026-06-01"], "temperature_2m_min": [raw_tmin]}}
    return corr, raw


def _fill(get, kind="model_analysis"):
    with patch.object(df.settings, "openmeteo_api_url", "http://om/v1"), \
         patch.object(df.settings, "openmeteo_archive_url", "http://om/v1"):
        return df.fetch_open_meteo_daily(kind, 42.6, -2.1, 580.0, date(2026, 6, 1), date(2026, 6, 1), http_get=get)


def test_inland_fill_tmin_comes_from_the_raw_cell_and_tmax_is_unchanged():
    get = _routed(*_daily_bodies())
    out = _fill(get)["2026-06-01"]
    assert out["tmin_c"] == RAW_TMIN
    assert out["tmax_c"] == 27.0 and out["et0_mm"] == 5.0
    assert "tmin_reason" not in out
    nan_call = [c for c in get.call_args_list if c.kwargs["params"].get("elevation") == "nan"]
    assert len(nan_call) == 1 and get.call_count == 2
    p = nan_call[0].kwargs["params"]
    assert p["timezone"] == "auto" and p["start_date"] == "2026-06-01" and p["daily"] == "temperature_2m_min"


def test_inland_fill_raw_call_uses_same_endpoint_and_models_archive():
    with patch.object(df.settings, "openmeteo_archive_models", "era5_seamless"):
        get = _routed(*_daily_bodies())
        _fill(get, "reanalysis")
    for c in get.call_args_list:
        assert c.args[0] == "http://om/v1/archive" and c.kwargs["params"]["models"] == "era5_seamless"


def test_coastal_by_cell_mismatch_keeps_the_corrected_tmin():
    get = _routed(*_daily_bodies(raw_cell=_cell(lat=42.8)))
    assert _fill(get)["2026-06-01"]["tmin_c"] == CORR_TMIN


def test_coastal_by_sea_grid_cell_keeps_the_corrected_tmin():
    get = _routed(*_daily_bodies(raw_cell=_cell(elev=0.0)))
    assert _fill(get)["2026-06-01"]["tmin_c"] == CORR_TMIN


def test_raw_call_failure_gives_null_tmin_and_reason_not_the_corrected_value():
    get = _routed(*_daily_bodies(), fail_raw=True)
    out = _fill(get)["2026-06-01"]
    assert out["tmin_c"] is None and out["tmin_reason"] == df.TMIN_RAW_UNAVAILABLE == "tmin_raw_unavailable"
    assert out["tmax_c"] == 27.0


def test_undeterminable_cell_gives_null_tmin_and_reason():
    corr, raw = _daily_bodies()
    del raw["latitude"]
    out = _fill(_routed(corr, raw))["2026-06-01"]
    assert out["tmin_c"] is None and out["tmin_reason"] == "tmin_raw_unavailable"


def test_raw_cell_without_that_day_gives_null_tmin_and_reason():
    corr, raw = _daily_bodies(raw_tmin=None)
    out = _fill(_routed(corr, raw))["2026-06-01"]
    assert out["tmin_c"] is None and out["tmin_reason"] == "tmin_raw_unavailable"


def test_no_raw_request_when_the_main_call_gives_nothing():
    get = MagicMock(return_value=_resp({"daily": {"time": []}}))
    assert _fill(get) == {}
    assert get.call_count == 1


def _fc_bodies(corr_cell=None, raw_cell=None):
    corr = {**_forecast_payload(), **(corr_cell or _cell())}
    days = ["2026-11-05", "2026-11-06", "2026-11-07"]
    hours = [f"{d}T{h:02d}:00" for d in days for h in range(24)]
    hourly = [5.0] * 72
    hourly[24 + 4] = -1.0
    raw = {**(raw_cell or _cell()), "utc_offset_seconds": 3600, "daily": {
        "time": days, "temperature_2m_min_best_match": [-5.0] * 3, "temperature_2m_min_ecmwf_ifs025": [-6.0] * 3},
        "hourly": {"time": hours, "temperature_2m_best_match": hourly}}
    return corr, raw


def _fc(get):
    with patch.object(df.settings, "openmeteo_api_url", "http://om/v1"):
        return df.fetch_open_meteo_forecast(10.0, 10.0, 300.0, 2, now=NOW, http_get=get)


def test_forecast_inland_tmin_coldest_and_frost_hours_come_from_the_raw_call():
    get = _routed(*_fc_bodies())
    day = _fc(get)["days"]["2026-11-06"]
    assert day["tmin_c"] == -5.0 and day["tmin_coldest_c"] == -6.0
    assert day["hours_below_zero"] == 1.0                     # raw hourly, unshifted
    assert day["tmax_c"] == 12.0 and day["precip_mm"] == 0.4  # other variables stay on the elevated call
    p = [c for c in get.call_args_list if c.kwargs["params"].get("elevation") == "nan"][0].kwargs["params"]
    assert p["models"] == "best_match,ecmwf_ifs025" and p["hourly"] == "temperature_2m" and p["timezone"] == "auto"


def test_forecast_coastal_keeps_todays_values():
    day = _fc(_routed(*_fc_bodies(raw_cell=_cell(elev=-1.0))))["days"]["2026-11-06"]
    assert day["tmin_c"] == AUTO_TMIN and day["tmin_coldest_c"] == min(AUTO_TMIN, ECMWF_TMIN)
    assert day["hours_below_zero"] == 0.0


def test_forecast_raw_failure_nulls_tmin_fields_with_reason():
    out = _fc(_routed(*_fc_bodies(), fail_raw=True))
    day = out["days"]["2026-11-06"]
    assert day["tmin_c"] is None and day["tmin_coldest_c"] is None and day["hours_below_zero"] is None
    assert day["tmin_reason"] == "tmin_raw_unavailable" and day["tmax_c"] == 12.0


def test_forecast_no_raw_request_when_the_main_call_fails():
    get = MagicMock(return_value=_resp({"error": True}, 500))
    assert _fc(get) == {}
    assert get.call_count == 1
