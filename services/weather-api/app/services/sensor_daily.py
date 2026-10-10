"""IoT sensor readings folded into a parcel's daily weather series.

Platform precedence per variable: IoT sensor > parcel weather (model) > fill. Each
variable of each day says where it came from (``sources``). Pure functions; I/O lives
in the router.

- Days are the parcel's local calendar days (its time zone; UTC without one).
- Coverage is counted in local hours with at least one valid reading. A variable that
  does not reach its coverage is not used that day; the next level stays.
- Several devices on one parcel: the mean of those that pass.
- Precipitation follows the SDM meaning: each reading is the rain of its interval. A
  gauge that reports a running counter must be converted at ingestion.
- Wind is not used: the device model has no anemometer height, and converting to 2 m
  without it would be a guess. ET0 therefore stays the model's.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from statistics import mean
from typing import Dict, Iterable, List, Optional

_W_M2_TO_MJ_M2_DAY = 0.0864
_MIN_HOURS = {"airTemperature": 20, "relativeHumidity": 20, "precipitation": 24, "solarRadiation": 24}
_RANGES = {"airTemperature": (-50.0, 60.0), "relativeHumidity": (0.0, 100.0), "precipitation": (0.0, 500.0),
           "solarRadiation": (0.0, 500.0)}  # solarRadiation: daily mean, W m-2
# A sensor temperature this far from the model of the same day is a fault, not weather.
_MAX_MODEL_GAP_C = 10.0

SENSOR_FIELDS = ("tmin_c", "tmax_c", "precip_mm", "radiation_mj_m2", "vapour_pressure_kpa")


@dataclass(frozen=True)
class Reading:
    device: str
    observed_at: datetime  # timezone-aware
    variable: str          # SDM name: airTemperature, relativeHumidity, precipitation, solarRadiation
    value: float


def _es(t: float) -> float:
    """Saturation vapour pressure, kPa (FAO-56 eq. 11)."""
    return 0.6108 * math.exp(17.27 * t / (t + 237.3))


def _in(variable: str, value: float) -> bool:
    lo, hi = _RANGES[variable]
    return lo <= value <= hi


def _device_day(by_hour: Dict[str, Dict[int, List[float]]]) -> Dict[str, float]:
    """Daily fields of one device for one local day, from {variable: {hour: [values]}}."""
    out: Dict[str, float] = {}
    covered = {v: hours for v, hours in by_hour.items() if len(hours) >= _MIN_HOURS.get(v, 25)}

    temps = covered.get("airTemperature")
    if temps:
        values = [x for vals in temps.values() for x in vals]
        tmin, tmax = min(values), max(values)
        if _in("airTemperature", tmin) and _in("airTemperature", tmax):
            out["tmin_c"], out["tmax_c"] = tmin, tmax

    rain = covered.get("precipitation")
    if rain:
        total = sum(x for vals in rain.values() for x in vals)
        if _in("precipitation", total):
            out["precip_mm"] = round(total, 2)

    rad = covered.get("solarRadiation")
    if rad:
        daily_mean = mean(mean(vals) for vals in rad.values())
        if _in("solarRadiation", daily_mean):
            out["radiation_mj_m2"] = round(daily_mean * _W_M2_TO_MJ_M2_DAY, 4)

    rh = covered.get("relativeHumidity")
    if rh and "tmin_c" in out:
        rh_mean = mean(mean(vals) for vals in rh.values())
        if _in("relativeHumidity", rh_mean):
            # FAO-56 eq. 19 (mean relative humidity)
            out["vapour_pressure_kpa"] = round(rh_mean / 100.0 * (_es(out["tmax_c"]) + _es(out["tmin_c"])) / 2, 4)
    return out


def aggregate(readings: Iterable[Reading], tz: tzinfo) -> Dict[date, Dict[str, float]]:
    """{local day: {daily field: value}}, averaged over the devices that qualify."""
    grid: Dict[tuple, Dict[str, Dict[int, List[float]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for r in readings:
        if r.variable not in _MIN_HOURS or r.value is None:
            continue
        local = r.observed_at.astimezone(tz)
        grid[(r.device, local.date())][r.variable][local.hour].append(float(r.value))

    per_day: Dict[date, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for (_, day), by_var in grid.items():
        for field, value in _device_day(by_var).items():
            per_day[day][field].append(value)
    return {day: {f: round(mean(v), 4) for f, v in fields.items()} for day, fields in per_day.items()}


def merge(days: List[dict], sensor: Dict[date, Dict[str, float]]) -> int:
    """Override the fields of `days` that a sensor covered; add `sources` per field.

    Only days that already have a base series are touched: a day with sensor values
    alone is not a complete day. Returns how many days took at least one sensor value.
    """
    used = 0
    for rec in days:
        base = rec.get("source")
        rec["sources"] = {f: (base if rec.get(f) is not None else None)
                          for f in rec if f not in ("date", "source", "sources")}
        values = sensor.get(date.fromisoformat(rec["date"])) if base else None
        took = False
        for field, value in (values or {}).items():
            model = rec.get(field)
            if field in ("tmin_c", "tmax_c") and model is not None and abs(value - model) > _MAX_MODEL_GAP_C:
                continue
            rec[field] = value
            rec["sources"][field] = "iot_sensor"
            took = True
        used += took
    return used


def tz_or_utc(name: Optional[str]) -> tzinfo:
    from datetime import timezone
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    if isinstance(name, str) and name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return timezone.utc
