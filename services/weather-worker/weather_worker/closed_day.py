"""Closed-day (final, previous-day) weather for one parcel.

The running ``WeatherObserved`` of a parcel is rewritten every cycle with the
still-moving values of TODAY. A day that has ended is different data: it is
final, and a crop model needs one value per calendar day. It is published on a
SEPARATE per-parcel entity (``...:parcel-<id>-daily``) so that

* a telemetry notification never mixes running and closed-day attributes (the
  telemetry worker stamps a whole notification with the first attribute
  ``observedAt`` it finds), and
* every attribute carries ``observedAt`` = the day it describes.

The entity is written with upsert/replace, so an attribute that is missing for a
day is absent from the entity instead of keeping the value of an earlier day.
Missing data stays missing: nothing here substitutes a default.

``publish_closed_day`` is the reusable entry point (a later historical backfill
calls it with past days).

Attribute set (all flat scalar Properties, ``observedAt`` = ``<day>T23:59:59Z``):

=================  ====  ==================================================
attribute          unit  meaning
=================  ====  ==================================================
tempMin / tempMax  CEL   daily minimum / maximum air temperature at 2 m
precipitation      MMT   daily precipitation sum
et0                MMT   FAO-56 reference evapotranspiration (provider)
solarRadiation     D54   daily MEAN global horizontal radiation (W m-2);
                         MJ m-2 d-1 = value * 0.0864
vapourPressure     KPA   mean actual vapour pressure
windSpeed2m        MTS   mean wind speed at 2 m
=================  ====  ==================================================
"""

import logging
import math
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

import requests

sys.path.insert(0, "/app")
from common.ngsi_headers import inject_fiware_headers

logger = logging.getLogger(__name__)

# Orion throttles a subscription (not an entity): two writes to any WeatherObserved
# closer than the subscription's throttling window lose a notification. The
# telemetry subscription for WeatherObserved uses 1 s; keep a margin.
MIN_WRITE_INTERVAL_S = 1.5

DAY_CLOSE_TIME = "23:59:59Z"

# Watts per m2 (mean over the day) -> MJ per m2 per day.
W_M2_TO_MJ_M2_DAY = 0.0864

# FAO-56 eq. 47: u2 = uz * 4.87 / ln(67.8 * z - 5.42), z = 10 m anemometer height.
_WIND_10M_TO_2M = 4.87 / math.log(67.8 * 10.0 - 5.42)

# values key -> (NGSI-LD attribute, UN/CEFACT unitCode from common/unit_codes.py)
ATTRIBUTES: Dict[str, Tuple[str, str]] = {
    "tmin_c": ("tempMin", "CEL"),
    "tmax_c": ("tempMax", "CEL"),
    "precip_mm": ("precipitation", "MMT"),
    "et0_mm": ("et0", "MMT"),
    "radiation_mj_m2": ("solarRadiation", "D54"),
    "vapour_pressure_kpa": ("vapourPressure", "KPA"),
    "wind2m_ms": ("windSpeed2m", "MTS"),
}

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def closed_day_entity_id(tenant_id: str, parcel_id: str) -> str:
    bare = parcel_id.split(":")[-1] if ":" in parcel_id else parcel_id
    return f"urn:ngsi-ld:WeatherObserved:{tenant_id}:parcel-{bare}-daily"


def _vapour_pressure_kpa(dewpoint_c: float) -> float:
    """Actual vapour pressure from dew point: FAO-56 eq. 14 evaluated at Tdew."""
    return 0.6108 * math.exp(17.27 * dewpoint_c / (dewpoint_c + 237.3))


def _day_hours(hourly: Dict[str, Any], key: str, day: str) -> Optional[list]:
    """The 24 hourly values of ``day`` for ``key``, or None unless all 24 exist."""
    times = hourly.get("time") or []
    series = hourly.get(key) or []
    vals = [
        series[i] if i < len(series) else None
        for i, t in enumerate(times)
        if isinstance(t, str) and t[:10] == day
    ]
    if len(vals) != 24 or any(v is None for v in vals):
        return None
    return [float(v) for v in vals]


def _daily(daily: Dict[str, Any], key: str, idx: int) -> Optional[float]:
    arr = daily.get(key) or []
    if idx < len(arr) and arr[idx] is not None:
        return float(arr[idx])
    return None


def compute_closed_day_values(data: Dict[str, Any], day: str) -> Dict[str, Optional[float]]:
    """Reduce an Open-Meteo response to the closed-day values of ``day``.

    Hourly-derived values (vapour pressure, wind) need all 24 hours of the day;
    with fewer they are None. Open-Meteo hourly wind is km/h.
    """
    out: Dict[str, Optional[float]] = {k: None for k in ATTRIBUTES}
    daily = data.get("daily") or {}
    days = daily.get("time") or []
    if day not in days:
        return out
    i = days.index(day)
    out["tmax_c"] = _daily(daily, "temperature_2m_max", i)
    out["tmin_c"] = _daily(daily, "temperature_2m_min", i)
    out["precip_mm"] = _daily(daily, "precipitation_sum", i)
    out["et0_mm"] = _daily(daily, "et0_fao_evapotranspiration", i)
    out["radiation_mj_m2"] = _daily(daily, "shortwave_radiation_sum", i)

    hourly = data.get("hourly") or {}
    dew = _day_hours(hourly, "dew_point_2m", day)
    if dew is not None:
        out["vapour_pressure_kpa"] = sum(_vapour_pressure_kpa(t) for t in dew) / 24.0
    wind = _day_hours(hourly, "wind_speed_10m", day)
    if wind is not None:
        out["wind2m_ms"] = (sum(wind) / 24.0 / 3.6) * _WIND_10M_TO_2M
    return out


def build_closed_day_entity(
    tenant_id: str,
    parcel_id: str,
    location: Tuple[float, ...],
    day: str,
    values: Dict[str, Optional[float]],
    *,
    context_url: str,
) -> Dict[str, Any]:
    """NGSI-LD body of the closed-day entity. Missing values are omitted."""
    observed_at = f"{day}T{DAY_CLOSE_TIME}"
    entity: Dict[str, Any] = {
        "@context": [context_url],
        "id": closed_day_entity_id(tenant_id, parcel_id),
        "type": "WeatherObserved",
        "location": {
            "type": "GeoProperty",
            "value": {"type": "Point", "coordinates": list(location)},
        },
        "locatedAt": {"type": "Relationship", "object": parcel_id},
    }
    for key, (attr, unit) in ATTRIBUTES.items():
        v = values.get(key)
        if v is None:
            continue
        if key == "radiation_mj_m2":
            v = v / W_M2_TO_MJ_M2_DAY
        entity[attr] = {
            "type": "Property",
            "value": float(v),
            "unitCode": unit,
            "observedAt": observed_at,
        }
    return entity


class WritePacer:
    """Guarantees a minimum gap between consecutive writes (subscription throttling)."""

    def __init__(
        self,
        min_interval_s: float = MIN_WRITE_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._min = min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._last: Optional[float] = None

    def touch(self) -> None:
        """Record that a write to the same subscription just happened elsewhere."""
        self._last = self._clock()

    def wait(self) -> None:
        if self._last is not None:
            gap = self._min - (self._clock() - self._last)
            if gap > 0:
                self._sleep(gap)
        self._last = self._clock()


def publish_closed_day(
    tenant_id: str,
    parcel_id: str,
    location: Tuple[float, ...],
    day: str,
    values: Dict[str, Optional[float]],
    *,
    orion_url: str = "",
    context_url: str = "",
    http: Any = requests,
    pacer: Optional[WritePacer] = None,
) -> bool:
    """Publish one closed day (``YYYY-MM-DD``) of one parcel. Idempotent.

    ``values`` uses the keys of ``ATTRIBUTES``; None / absent keys are not
    published. Returns True when Orion accepted the write, False when there was
    nothing to publish or the write failed (logged, never raised). A malformed or
    future ``day`` raises ValueError: a day that has not ended is not final.

    Pass a shared ``pacer`` when calling in a loop (backfill): it spaces the writes
    so the WeatherObserved subscription never drops a notification.
    """
    if not isinstance(day, str) or not _DAY_RE.match(day):
        raise ValueError(f"day must be YYYY-MM-DD, got {day!r}")
    try:
        parsed = datetime.strptime(day, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"invalid day {day!r}") from e
    if parsed.date() > datetime.utcnow().date():
        raise ValueError(f"day {day} is in the future; only closed days are final")

    orion_url = orion_url or os.getenv("ORION_URL", "http://orion-ld-service:1026")
    context_url = context_url or os.getenv("CONTEXT_URL", "")

    entity = build_closed_day_entity(
        tenant_id, parcel_id, location, day, values, context_url=context_url
    )
    if not any(attr in entity for attr, _ in ATTRIBUTES.values()):
        logger.info("closed day %s parcel %s: no values, nothing published", day, parcel_id)
        return False

    payload = [entity]
    try:
        if pacer is not None:
            pacer.wait()
        # replace: an attribute absent for this day must not survive from another day.
        resp = http.post(
            f"{orion_url}/ngsi-ld/v1/entityOperations/upsert?options=replace",
            json=payload,
            headers=inject_fiware_headers({}, tenant=tenant_id, body=payload),
            timeout=15,
        )
        if resp.status_code in (200, 201, 204):
            return True
        logger.warning(
            "closed day %s parcel %s tenant %s: upsert returned %s",
            day, parcel_id, tenant_id, resp.status_code,
        )
        return False
    except Exception as e:  # never silence silently: logged with context
        logger.warning("closed day %s parcel %s: write failed: %s", day, parcel_id, e)
        return False
