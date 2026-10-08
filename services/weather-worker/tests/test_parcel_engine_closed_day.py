"""ParcelWeatherEngine closed-day pass: once per local day per parcel, idempotent."""

import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

import weather_worker.parcel_engine as pe
from test_closed_day import _response  # noqa: E402


def _engine():
    return pe.ParcelWeatherEngine(orion_url="http://orion.test", context_url="http://ctx.test/c.json")


def _cluster():
    return [{"id": "urn:ngsi-ld:AgriParcel:p1", "_tenant": "t-1", "_centroid": (-1.6, 42.8), "_altitude": 500.0},
            {"id": "urn:ngsi-ld:AgriParcel:p2", "_tenant": "t-1", "_centroid": (-1.61, 42.81), "_altitude": 500.0}]


class _FixedDT(datetime):
    @classmethod
    def utcnow(cls):
        return cls(2026, 10, 8, 10, 0, 0)  # +7200s offset -> local date 2026-10-08, yesterday 2026-10-07


def test_pass_publishes_yesterday_once_per_parcel():
    eng = _engine()
    published = []
    with patch.object(pe, "datetime", _FixedDT), \
         patch.object(eng, "_fetch_openmeteo_closed_day", return_value=_response()) as fetch, \
         patch("weather_worker.closed_day.publish_closed_day",
               side_effect=lambda *a, **k: published.append((a, k)) or True), \
         patch("weather_worker.closed_day.WritePacer", return_value=MagicMock()):
        s1 = eng._publish_closed_days([_cluster()])
        s2 = eng._publish_closed_days([_cluster()])
    assert s1["closed_day_published"] == 2
    assert s2["closed_day_published"] == 0  # already done for that day
    assert fetch.call_count == 1  # second pass needs no fetch at all
    days = {a[3] for a, _ in published}
    assert days == {"2026-10-07"}


def test_failed_publish_is_retried_next_cycle():
    eng = _engine()
    results = iter([False, False, True, True])
    with patch.object(pe, "datetime", _FixedDT), \
         patch.object(eng, "_fetch_openmeteo_closed_day", return_value=_response()), \
         patch("weather_worker.closed_day.publish_closed_day", side_effect=lambda *a, **k: next(results)), \
         patch("weather_worker.closed_day.WritePacer", return_value=MagicMock()):
        s1 = eng._publish_closed_days([_cluster()])
        s2 = eng._publish_closed_days([_cluster()])
    assert s1["closed_day_published"] == 0 and s1["closed_day_errors"] == 2
    assert s2["closed_day_published"] == 2


def test_fetch_failure_counts_error_and_publishes_nothing():
    eng = _engine()
    with patch.object(pe, "datetime", _FixedDT), \
         patch.object(eng, "_fetch_openmeteo_closed_day", return_value=None), \
         patch("weather_worker.closed_day.publish_closed_day") as pub, \
         patch("weather_worker.closed_day.WritePacer", return_value=MagicMock()):
        s = eng._publish_closed_days([_cluster()])
    pub.assert_not_called()
    assert s["closed_day_errors"] >= 1 and s["closed_day_published"] == 0


def test_yesterday_not_in_response_publishes_nothing():
    eng = _engine()
    resp = _response()
    resp["daily"]["time"] = ["2026-10-01"]
    with patch.object(pe, "datetime", _FixedDT), \
         patch.object(eng, "_fetch_openmeteo_closed_day", return_value=resp), \
         patch("weather_worker.closed_day.publish_closed_day") as pub, \
         patch("weather_worker.closed_day.WritePacer", return_value=MagicMock()):
        s = eng._publish_closed_days([_cluster()])
    pub.assert_not_called()
    assert s["closed_day_published"] == 0


def test_fetch_requests_past_days_and_auto_timezone():
    eng = _engine()
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"daily": {}}
    with patch.object(pe.requests, "get", return_value=resp) as g:
        eng._fetch_openmeteo_closed_day(42.8, -1.6)
    params = g.call_args.kwargs["params"]
    assert params["past_days"] == 2 and params["forecast_days"] == 1
    assert params["timezone"] == "auto"
    assert "dew_point_2m" in params["hourly"] and "wind_speed_10m" in params["hourly"]
    assert "shortwave_radiation_sum" in params["daily"]


def test_temps_lapse_corrected_to_parcel_altitude_other_values_untouched():
    eng = _engine()
    base = {"tmin_c": 10.0, "tmax_c": 20.0, "et0_mm": 3.0, "radiation_mj_m2": 15.0}
    parcel = {"_altitude": 600.0}
    out = eng._downscale_closed_day_temps(base, parcel, 42.8, -1.6, 100.0)
    assert out["tmin_c"] < 10.0 and out["tmax_c"] < 20.0  # 500 m higher -> colder
    assert out["et0_mm"] == 3.0 and out["radiation_mj_m2"] == 15.0
    # parcel altitude unknown -> grid values kept, not corrected from sea level
    assert eng._downscale_closed_day_temps(base, {}, 42.8, -1.6, 100.0) == base
