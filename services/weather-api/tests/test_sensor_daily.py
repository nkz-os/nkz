"""IoT sensor readings folded into the parcel daily series (sensor > parcel weather > fill)."""
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_wa = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _wa)

from app.services import sensor_daily as sd  # noqa: E402

MADRID = ZoneInfo("Europe/Madrid")
DAY = date(2026, 7, 10)


def _hourly(device, var, values, day=DAY, tz=MADRID, minute=30):
    """One reading per local hour of `day` (values[h] for hour h; None = no reading)."""
    out = []
    for h, v in enumerate(values):
        if v is None:
            continue
        local = datetime(day.year, day.month, day.day, h, minute, tzinfo=tz)
        out.append(sd.Reading(device, local.astimezone(timezone.utc), var, v))
    return out


def _temps(lo=12.0, hi=30.0):
    # cool at dawn, warm mid-afternoon
    return [lo + (hi - lo) * max(0.0, math.sin(math.pi * (h - 6) / 18)) for h in range(24)]


def test_temperature_extremes_from_a_covered_local_day():
    days = sd.aggregate(_hourly("d1", "airTemperature", _temps()), MADRID)
    assert days[DAY]["tmin_c"] == 12.0
    assert days[DAY]["tmax_c"] == 30.0


def test_readings_are_grouped_by_the_parcel_local_day():
    # 23:30 local on the 10th is 21:30 UTC; 00:30 local on the 11th is 22:30 UTC on the 10th.
    late = _hourly("d1", "airTemperature", [10.0] * 24)
    next_day = _hourly("d1", "airTemperature", [40.0] + [None] * 23, day=DAY + timedelta(days=1))
    days = sd.aggregate(late + next_day, MADRID)
    assert days[DAY]["tmax_c"] == 10.0


def test_poorly_covered_day_is_not_used():
    values = _temps()[:15] + [None] * 9  # 15 of 24 hours
    assert "tmin_c" not in sd.aggregate(_hourly("d1", "airTemperature", values), MADRID).get(DAY, {})


def test_precipitation_needs_every_hour_and_is_summed():
    full = sd.aggregate(_hourly("d1", "precipitation", [0.5] * 24), MADRID)
    assert full[DAY]["precip_mm"] == 12.0
    gap = sd.aggregate(_hourly("d1", "precipitation", [0.5] * 23 + [None]), MADRID)
    assert "precip_mm" not in gap.get(DAY, {})


def test_radiation_is_the_daily_mean_in_mj():
    values = [0.0] * 6 + [500.0] * 12 + [0.0] * 6  # W m-2
    days = sd.aggregate(_hourly("d1", "solarRadiation", values), MADRID)
    assert days[DAY]["radiation_mj_m2"] == round(250.0 * 0.0864, 4)


def test_vapour_pressure_from_humidity_and_temperature():
    readings = _hourly("d1", "airTemperature", _temps(10.0, 30.0)) + _hourly("d1", "relativeHumidity", [50.0] * 24)
    days = sd.aggregate(readings, MADRID)
    es = lambda t: 0.6108 * math.exp(17.27 * t / (t + 237.3))  # noqa: E731
    assert days[DAY]["vapour_pressure_kpa"] == round(0.5 * (es(30.0) + es(10.0)) / 2, 4)


def test_several_devices_are_averaged():
    a = _hourly("d1", "airTemperature", [10.0] * 23 + [20.0])
    b = _hourly("d2", "airTemperature", [12.0] * 23 + [24.0])
    days = sd.aggregate(a + b, MADRID)
    assert days[DAY]["tmin_c"] == 11.0 and days[DAY]["tmax_c"] == 22.0


def test_implausible_values_are_dropped():
    hot = _hourly("d1", "airTemperature", [75.0] * 24)
    assert "tmax_c" not in sd.aggregate(hot, MADRID).get(DAY, {})
    flood = _hourly("d1", "precipitation", [30.0] * 24)  # 720 mm
    assert "precip_mm" not in sd.aggregate(flood, MADRID).get(DAY, {})


def test_merge_overrides_only_based_days_and_checks_the_model():
    days = [
        {"date": "2026-07-10", "tmin_c": 14.0, "tmax_c": 29.0, "precip_mm": 0.0, "source": "parcel_weather"},
        {"date": "2026-07-11", "tmin_c": None, "tmax_c": None, "precip_mm": None, "source": None},
    ]
    sensor = {DAY: {"tmin_c": 12.0, "tmax_c": 30.0}, DAY + timedelta(days=1): {"tmin_c": 13.0, "tmax_c": 31.0}}
    used = sd.merge(days, sensor)
    assert days[0]["tmin_c"] == 12.0 and days[0]["sources"]["tmin_c"] == "iot_sensor"
    assert days[0]["sources"]["precip_mm"] == "parcel_weather"
    assert days[1]["tmin_c"] is None and days[1]["sources"]["tmin_c"] is None  # no base day: stays missing
    assert used == 1


def test_merge_rejects_temperatures_far_from_the_model():
    days = [{"date": "2026-07-10", "tmin_c": 14.0, "tmax_c": 29.0, "source": "parcel_weather"}]
    sd.merge(days, {DAY: {"tmin_c": 13.0, "tmax_c": 45.0}})
    assert days[0]["tmax_c"] == 29.0 and days[0]["sources"]["tmax_c"] == "parcel_weather"
    assert days[0]["tmin_c"] == 13.0 and days[0]["sources"]["tmin_c"] == "iot_sensor"


# ---- medium filter (only open-air sensors feed the parcel weather) ---------

def _dev(ctx=None, overrides=None, kv=False, iri=False):
    e = {"id": "urn:ngsi-ld:Device:t1:d1", "type": "Device"}
    ck = "https://nkz-os.org/ns/observationContext" if iri else "observationContext"
    ok = "https://nkz-os.org/ns/channelOverrides" if iri else "channelOverrides"
    if ctx is not None:
        e[ck] = ctx if kv else {"type": "Property", "value": ctx}
    if overrides is not None:
        e[ok] = overrides if kv else {"type": "Property", "value": overrides}
    return e


def test_medium_rule_ambient_air_device_counts_every_attribute():
    assert sd.medium_rule(_dev({"medium": "ambientAir", "heightAboveGround": 2.0})) == (True, {})
    assert sd.medium_rule(_dev({"medium": "ambientAir"}, kv=True)) == (True, {})
    assert sd.medium_rule(_dev({"medium": "ambientAir"}, iri=True)) == (True, {})


def test_medium_rule_rejects_other_media_and_unset_context():
    under = {"medium": "ambientAirUnderStructure", "structure": "urn:ngsi-ld:AgriSolarPark:t1:p"}
    assert sd.medium_rule(_dev(under)) is None
    assert sd.medium_rule(_dev({"medium": "enclosedAir", "enclosure": "urn:ngsi-ld:Greenhouse:t1:g"})) is None
    assert sd.medium_rule(_dev()) is None  # no declared medium: never assumed open air


def test_medium_rule_channel_overrides_win_per_attribute():
    rule = sd.medium_rule(_dev({"medium": "ambientAir"},
                               {"temperature": {"observationContext": {"medium": "equipment"}},
                                "relativeHumidity": {"observesFeature": "urn:ngsi-ld:AgriParcel:x"}}))
    assert rule == (True, {"temperature": False})
    only_one = sd.medium_rule(_dev({"medium": "equipment"},
                                   {"airTemperature": {"observationContext": {"medium": "ambientAir"}}}))
    assert only_one == (False, {"airTemperature": True})


def test_ambient_air_only_keeps_allowed_readings():
    t = datetime(2026, 7, 10, 10, tzinfo=timezone.utc)
    readings = [sd.Reading("d1", t, "airTemperature", 20.0), sd.Reading("d1", t, "temperature", 35.0),
                sd.Reading("d2", t, "airTemperature", 25.0), sd.Reading("d3", t, "airTemperature", 21.0)]
    rules = {"d1": (True, {"temperature": False}), "d3": (False, {"airTemperature": True})}
    kept = sd.ambient_air_only(readings, rules)
    assert [(r.device, r.variable) for r in kept] == [("d1", "airTemperature"), ("d3", "airTemperature")]


def test_real_extremes_win_over_means():
    readings = (_hourly("d1", "airTemperature", [12.0] * 24)
                + _hourly("d1", "airTemperatureMin", [9.0] + [None] * 23)
                + _hourly("d1", "airTemperatureMax", [None] * 14 + [15.0] + [None] * 9))
    day = sd.aggregate(readings, MADRID)[DAY]
    assert day["tmin_c"] == 9.0
    assert day["tmax_c"] == 15.0


def test_extremes_without_coverage_use_today_rule():
    readings = (_hourly("d1", "airTemperature", [12.0] * 10 + [None] * 14)
                + _hourly("d1", "airTemperatureMin", [9.0] * 24)
                + _hourly("d1", "airTemperatureMax", [15.0] * 24))
    assert "tmin_c" not in sd.aggregate(readings, MADRID).get(DAY, {})


def test_extremes_out_of_range_are_dropped():
    readings = (_hourly("d1", "airTemperature", [12.0] * 24)
                + _hourly("d1", "airTemperatureMax", [75.0] + [None] * 23))
    day = sd.aggregate(readings, MADRID)[DAY]
    assert "tmax_c" not in day
    assert day["tmin_c"] == 12.0


def test_only_temperature_extremes_enter_the_aggregate():
    readings = (_hourly("d1", "airTemperature", [12.0] * 24)
                + _hourly("d1", "airTemperatureStdDev", [1.0] * 24)
                + _hourly("d1", "relativeHumidityMax", [99.0] * 24)
                + _hourly("d1", "precipitationMax", [5.0] * 24))
    day = sd.aggregate(readings, MADRID)[DAY]
    assert set(day) == {"tmin_c", "tmax_c"}


def test_override_on_base_applies_to_derived():
    rules = {"d1": (True, {"airTemperature": False})}
    readings = (_hourly("d1", "airTemperature", [12.0] * 24)
                + _hourly("d1", "airTemperatureMin", [9.0] * 24))
    assert sd.ambient_air_only(readings, rules) == []
    kept = sd.ambient_air_only(readings, {"d1": (True, {})})
    assert len(kept) == 48
