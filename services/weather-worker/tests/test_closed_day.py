"""Closed-day (final, previous-day) weather publication for a parcel.

The running WeatherObserved carries today's still-moving values. The closed day
goes on a SEPARATE per-parcel entity so a notification never mixes the two and
every attribute carries observedAt = the day it describes.
"""

import math
import os
import sys
from unittest.mock import MagicMock

import pytest

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

import weather_worker.closed_day as cd

DAY = "2026-10-07"
PARCEL = "urn:ngsi-ld:AgriParcel:abc-123"


def _response(dew=10.0, wind_kmh=18.0, hours=24, daily_over=None):
    daily = {
        "time": [DAY],
        "temperature_2m_max": [24.5],
        "temperature_2m_min": [11.2],
        "precipitation_sum": [3.4],
        "et0_fao_evapotranspiration": [3.9],
        "shortwave_radiation_sum": [15.5],
    }
    daily.update(daily_over or {})
    times = [f"{DAY}T{h:02d}:00" for h in range(hours)]
    return {
        "elevation": 500.0,
        "utc_offset_seconds": 7200,
        "daily": daily,
        "hourly": {
            "time": times,
            "dew_point_2m": [dew] * hours,
            "wind_speed_10m": [wind_kmh] * hours,
        },
    }


def test_compute_values_from_openmeteo():
    v = cd.compute_closed_day_values(_response(), DAY)
    assert v["tmin_c"] == 11.2
    assert v["tmax_c"] == 24.5
    assert v["precip_mm"] == 3.4
    assert v["et0_mm"] == 3.9
    assert v["radiation_mj_m2"] == 15.5
    # FAO-56 eq. 14 with Tdew=10 C
    assert v["vapour_pressure_kpa"] == pytest.approx(
        0.6108 * math.exp(17.27 * 10 / (10 + 237.3)), abs=1e-3
    )
    # 18 km/h = 5 m/s at 10 m; FAO-56 eq. 47 factor 4.87/ln(67.8*10-5.42)
    assert v["wind2m_ms"] == pytest.approx(5.0 * 4.87 / math.log(67.8 * 10 - 5.42), abs=1e-3)


def test_vapour_pressure_is_mean_of_hourly_not_of_mean_dewpoint():
    r = _response()
    r["hourly"]["dew_point_2m"] = [0.0] * 12 + [20.0] * 12
    v = cd.compute_closed_day_values(r, DAY)
    e0 = 0.6108
    e20 = 0.6108 * math.exp(17.27 * 20 / 257.3)
    assert v["vapour_pressure_kpa"] == pytest.approx((e0 + e20) / 2, abs=1e-3)


def test_incomplete_hours_leave_hourly_derived_values_missing():
    v = cd.compute_closed_day_values(_response(hours=23), DAY)
    assert v["vapour_pressure_kpa"] is None
    assert v["wind2m_ms"] is None
    assert v["tmax_c"] == 24.5  # daily block unaffected


def test_null_hour_counts_as_missing():
    r = _response()
    r["hourly"]["dew_point_2m"][5] = None
    v = cd.compute_closed_day_values(r, DAY)
    assert v["vapour_pressure_kpa"] is None
    assert v["wind2m_ms"] is not None


def test_missing_daily_field_stays_none_and_unknown_day_is_all_none():
    v = cd.compute_closed_day_values(_response(daily_over={"et0_fao_evapotranspiration": [None]}), DAY)
    assert v["et0_mm"] is None
    other = cd.compute_closed_day_values(_response(), "2026-10-06")
    assert all(x is None for x in other.values())


def test_entity_shape():
    values = {"tmin_c": 11.2, "tmax_c": 24.5, "precip_mm": 3.4, "et0_mm": 3.9,
              "radiation_mj_m2": 15.5, "vapour_pressure_kpa": 1.2, "wind2m_ms": 3.7}
    e = cd.build_closed_day_entity("t-1", PARCEL, (-1.6, 42.8), DAY, values, context_url="http://ctx.test/c.json")
    assert e["id"] == "urn:ngsi-ld:WeatherObserved:t-1:parcel-abc-123-daily"
    assert e["type"] == "WeatherObserved"
    assert e["locatedAt"]["object"] == PARCEL
    for attr, unit in (("tempMin", "CEL"), ("tempMax", "CEL"), ("precipitation", "MMT"),
                       ("et0", "MMT"), ("vapourPressure", "KPA"), ("windSpeed2m", "MTS"),
                       ("solarRadiation", "D54")):
        assert e[attr]["type"] == "Property"
        assert e[attr]["unitCode"] == unit
        assert e[attr]["observedAt"] == f"{DAY}T23:59:59Z"
    # radiation is stored as daily-mean W/m2 (verified unit code); 15.5 MJ/m2/d = 179.4 W/m2
    assert e["solarRadiation"]["value"] == pytest.approx(15.5 / 0.0864, abs=1e-3)
    assert e["tempMin"]["value"] == 11.2


def test_entity_omits_missing_never_fills():
    e = cd.build_closed_day_entity("t", PARCEL, (0, 0), DAY, {"tmin_c": 1.0, "tmax_c": None},
                                   context_url="")
    assert "tempMin" in e and "tempMax" not in e and "et0" not in e


def test_entity_has_no_running_value_attributes():
    e = cd.build_closed_day_entity("t", PARCEL, (0, 0), DAY, {"tmin_c": 1.0}, context_url="")
    for running in ("temperature", "gddAccumulated", "windSpeed", "tempCurrent"):
        assert running not in e


def test_entity_carries_discriminator_and_date_observed():
    """Review #1043: dailySummary==true distinguishes the daily entity and
    dateObserved is required by the WeatherObserved model (crop-health orders
    by it)."""
    e = cd.build_closed_day_entity("t", PARCEL, (0, 0), DAY, {"tmin_c": 1.0}, context_url="")
    assert e["dailySummary"] == {"type": "Property", "value": True}
    assert e["dateObserved"]["value"]["@value"] == f"{DAY}T00:00:00Z"


def _ok(status=204):
    r = MagicMock()
    r.status_code = status
    return r


def test_publish_replaces_entity_and_returns_true():
    http = MagicMock()
    http.post.return_value = _ok(204)
    ok = cd.publish_closed_day("t", PARCEL, (0, 0), DAY, {"tmin_c": 1.0},
                               orion_url="http://orion.test", context_url="http://ctx.test/c.json",
                               http=http)
    assert ok is True
    url = http.post.call_args.args[0]
    assert url == "http://orion.test/ngsi-ld/v1/entityOperations/upsert?options=replace"
    payload = http.post.call_args.kwargs["json"]
    assert isinstance(payload, list) and payload[0]["tempMin"]["observedAt"] == f"{DAY}T23:59:59Z"
    assert http.post.call_args.kwargs["headers"]["Content-Type"] == "application/ld+json"


def test_publish_nothing_when_all_values_missing():
    http = MagicMock()
    assert cd.publish_closed_day("t", PARCEL, (0, 0), DAY, {"tmin_c": None},
                                 orion_url="http://o", context_url="", http=http) is False
    http.post.assert_not_called()


@pytest.mark.parametrize("bad", ["2026-13-01", "yesterday", "2026-10-07T00:00", ""])
def test_publish_rejects_malformed_day(bad):
    with pytest.raises(ValueError):
        cd.publish_closed_day("t", PARCEL, (0, 0), bad, {"tmin_c": 1.0},
                              orion_url="http://o", context_url="", http=MagicMock())


def test_publish_rejects_future_day():
    with pytest.raises(ValueError):
        cd.publish_closed_day("t", PARCEL, (0, 0), "2999-01-01", {"tmin_c": 1.0},
                              orion_url="http://o", context_url="", http=MagicMock())


def test_publish_false_on_http_error_and_on_exception():
    http = MagicMock()
    http.post.return_value = _ok(207)
    assert cd.publish_closed_day("t", PARCEL, (0, 0), DAY, {"tmin_c": 1.0},
                                 orion_url="http://o", context_url="", http=http) is False
    http.post.side_effect = RuntimeError("down")
    assert cd.publish_closed_day("t", PARCEL, (0, 0), DAY, {"tmin_c": 1.0},
                                 orion_url="http://o", context_url="", http=http) is False


def test_pacer_enforces_min_spacing():
    now = [100.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    p = cd.WritePacer(min_interval_s=1.5, clock=lambda: now[0], sleep=sleep)
    p.wait()           # first call never sleeps
    now[0] += 0.5
    p.wait()           # 0.5 elapsed -> sleeps 1.0
    now[0] += 5.0
    p.wait()           # plenty elapsed -> no sleep
    assert slept == [pytest.approx(1.0)]


def test_publish_uses_pacer_before_post():
    order = []
    pacer = MagicMock()
    pacer.wait.side_effect = lambda: order.append("wait")
    http = MagicMock()
    http.post.side_effect = lambda *a, **k: (order.append("post"), _ok(204))[1]
    cd.publish_closed_day("t", PARCEL, (0, 0), DAY, {"tmin_c": 1.0},
                          orion_url="http://o", context_url="", http=http, pacer=pacer)
    assert order == ["wait", "post"]
