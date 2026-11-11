"""Local civil-day aggregation of hourly history (stdlib only).

Hourly stamps are UTC epoch seconds. Days are the parcel's local civil days (zoneinfo), so
DST days have 23 or 25 hours. Temperature, dew point, RH and wind are instantaneous at the
stamp; precipitation and radiation are stamped at the END of their hour, so the stamp is
moved back 1 h before the day is assigned. A variable with fewer valid hours than the day
has is None for that day (never filled, never treated as 0).
"""
import math
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Union
from zoneinfo import ZoneInfo

from app.services.history import fao56

UTC = timezone.utc
_INSTANT = ("t", "td", "rh", "wind10")
_INTERVAL_END = ("precip", "rad")


@lru_cache(maxsize=64)
def _zone(tz: str) -> ZoneInfo:
    return ZoneInfo(tz)


def local_date(ts: int, tz: str) -> date:
    """Local civil date of a UTC instant."""
    return datetime.fromtimestamp(ts, _zone(tz)).date()


def expected_hours(day: date, tz: str) -> int:
    """Hours in the local civil day (23 / 24 / 25)."""
    z = _zone(tz)
    start = datetime(day.year, day.month, day.day, tzinfo=z).astimezone(UTC)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=z).astimezone(UTC)
    return int(round((end - start).total_seconds() / 3600.0))


def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def _merge(years: Union[dict, Sequence[dict]]) -> dict:
    docs = [years] if isinstance(years, dict) else sorted(years, key=lambda d: d["times"][0] if d["times"] else 0)
    first = docs[0]
    merged = {"tz": first.get("tz")}
    merged["has_latest"] = "latest_available" in docs[-1]
    merged["latest_available"] = docs[-1].get("latest_available")
    for name in ("times",) + _INSTANT + _INTERVAL_END:
        merged[name] = [v for d in docs for v in d[name]]
    return merged


def _r(v: Optional[float], n: int) -> Optional[float]:
    return None if v is None else round(v, n)


def _buckets(h: dict, tz: str):
    buckets: Dict[date, Dict[str, list]] = {}
    days_seen = set()
    for i, ts in enumerate(h["times"]):
        d_inst = local_date(ts, tz)
        days_seen.add(d_inst)
        d_end = local_date(ts - 3600, tz)
        for var in _INSTANT:
            v = h[var][i]
            if _ok(v):
                buckets.setdefault(d_inst, {}).setdefault(var, []).append(float(v))
        for var in _INTERVAL_END:
            v = h[var][i]
            if _ok(v):
                buckets.setdefault(d_end, {}).setdefault(var, []).append(float(v))
    return buckets, days_seen


def latest_complete_day(years: Union[dict, Sequence[dict]], tz: Optional[str] = None) -> Optional[date]:
    """Last local day on which EVERY served variable is complete (None if there is none).

    Precipitation and radiation are interval-end stamped, so a day needs the next day's 00:00
    stamp too. Dec 31 is the exception: its closing stamp belongs to the next year's document.
    """
    h = _merge(years)
    tz = tz or h["tz"]
    buckets, days_seen = _buckets(h, tz)
    for d in sorted(days_seen, reverse=True):
        need = expected_hours(d, tz)
        b = buckets.get(d, {})
        vars_needed = _INSTANT + (() if (d.month, d.day) == (12, 31) else _INTERVAL_END)
        if all(len(b.get(v, ())) == need for v in vars_needed):
            return d
    return None


def daily_from_hourly(
    years: Union[dict, Sequence[dict]],
    tz: Optional[str] = None,
    lat: Optional[float] = None,
    elev: Optional[float] = None,
) -> List[dict]:
    """Daily rows for every local day that has an instantaneous stamp in the data.

    Pass adjacent years together: the last hour of Dec 31 for precipitation/radiation is the
    first stamp of the next year. Days after the last document's `latest_available` are not
    emitted (a document whose latest_available is None yields no days). `lat` / `elev` are the
    REQUESTING parcel's (ET0 is None without them); nothing is read from the cached document.
    """
    h = _merge(years)
    tz = tz or h["tz"]
    buckets, days_seen = _buckets(h, tz)
    latest = h.get("latest_available")
    if h["has_latest"] and latest is None:
        return []
    latest_d = date.fromisoformat(latest) if latest else None
    out: List[dict] = []
    for d in sorted(days_seen):
        if latest_d is not None and d > latest_d:
            continue
        need = expected_hours(d, tz)
        b = buckets.get(d, {})

        def full(var):
            vals = b.get(var)
            return vals if vals is not None and len(vals) == need else None

        t, td, rh, wind, pr, rad = (full(v) for v in ("t", "td", "rh", "wind10", "precip", "rad"))
        tmax = max(t) if t else None
        tmin = min(t) if t else None
        tmean = sum(t) / len(t) if t else None
        ea = fao56.vapour_pressure_kpa(sum(td) / len(td)) if td else None
        u2 = fao56.wind_to_2m(sum(wind) / len(wind)) if wind else None
        rs = sum(rad) * 3600.0 / 1e6 if rad else None
        et0 = None
        if None not in (tmax, tmin, ea, u2, rs) and lat is not None and elev is not None:
            et0 = fao56.et0_fao56_daily(tmax, tmin, ea, u2, rs, lat, elev, d.timetuple().tm_yday)
        out.append({
            "date": d.isoformat(),
            "tmin_c": _r(tmin, 2),
            "tmax_c": _r(tmax, 2),
            "tmean_c": _r(tmean, 2),
            "precip_mm": _r(sum(pr) if pr else None, 2),
            "et0_mm": _r(et0, 2),
            "radiation_mj_m2": _r(rs, 2),
            "vapour_pressure_kpa": _r(ea, 3),
            "rh_mean_pct": _r(sum(rh) / len(rh) if rh else None, 1),
            "wind2m_ms": _r(u2, 2),
        })
    return out


def hourly_rows(years: Union[dict, Sequence[dict]]) -> List[dict]:
    """Hourly rows labelled by their UTC stamp (precip/radiation: end of the hour)."""
    h = _merge(years)
    rows = []
    for i, ts in enumerate(h["times"]):
        w = h["wind10"][i]
        rows.append({
            "time": datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "t_c": _r(h["t"][i], 2),
            "td_c": _r(h["td"][i], 2),
            "rh_pct": _r(h["rh"][i], 1),
            "precip_mm": _r(h["precip"][i], 2),
            "radiation_w_m2": _r(h["rad"][i], 1),
            "wind2m_ms": _r(fao56.wind_to_2m(w) if _ok(w) else None, 2),
        })
    return rows
