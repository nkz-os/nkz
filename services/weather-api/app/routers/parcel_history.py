"""
GET /api/weather/parcel/{parcel_id}/history
    ?start=YYYY-MM-DD&end=YYYY-MM-DD&resolution=daily|hourly&vars=<csv>

A parcel's daily or hourly weather from 1991 on, from the ERA5 reanalysis (Open-Meteo
archive), temperature and dew point corrected to the parcel's elevation. Nothing is
interpolated: a missing hour stays null and a day without every hour is null.

Years are fetched newest first inside a deadline that starts when the request arrives
(shorter than the gateway proxy timeout). A cold year starts only if the time left covers
twice the last measured fetch. A year the deadline does not reach is listed in
``incomplete_years`` (with ``Retry-After``); later calls find the finished years in the
cache. If the cache cannot hold them (disabled, circuit open, last write failed) a request
that cannot finish answers 503 instead: the promise to retry would be false. A partial year
is never returned as if it were complete. At most two history requests run at once.

Day definition: a daily value belongs to the parcel's local civil day (DST days have 23 or
25 hours). Hourly precipitation and radiation are stamped at the END of their hour, so the
hour stamped at local 00:00 belongs to the previous day. The AEMET / DWD validation
statistics compared UTC 00-24 days: a small mismatch for daily precipitation and radiation.
"""

import logging
import math
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.auth import require_auth
from app.routers.parcel_daily import _fetch_parcel_entity, _num, _parcel_elevation, _parse_day
from app.routers.parcels import _normalize_parcel_id, _resolve_parcel_location
from app.services.history import archive, cache as cache_mod
from app.services.history.aggregate import daily_from_hourly, hourly_rows, local_date
from app.services.history.deadline import Deadline, DeadlineExceeded
from app.services.history.validation import validation_for
from app.services.history.year import history_year, peek_cell

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["parcels"])

MIN_DATE = date(1991, 1, 1)
MAX_HOURLY_DAYS = 366
DEADLINE_S = 20            # the gateway proxy gives up at 30 s
MIN_FETCH_BUDGET_S = 1.0
RETRY_AFTER_S = 30
LATEST_TTL_S = 6 * 3600
LATEST_LENIENT_DAYS = 30        # an end this far in the past does not need `latest_available` to be answered

_monotonic = time.monotonic
# Concurrency cap: each cold request holds a worker thread and up to 2 archive fetches.
_SEM = threading.BoundedSemaphore(2)
DAY_DEFINITION = ("parcel local civil day; hourly precipitation and radiation stamped at local 00:00 "
                  "belong to the previous day")

DAILY_UNITS = {
    "tmin_c": "degC", "tmax_c": "degC", "tmean_c": "degC", "precip_mm": "mm", "et0_mm": "mm",
    "radiation_mj_m2": "MJ m-2", "vapour_pressure_kpa": "kPa", "rh_mean_pct": "%", "wind2m_ms": "m s-1",
}
HOURLY_UNITS = {
    "t_c": "degC", "td_c": "degC", "rh_pct": "%", "precip_mm": "mm", "radiation_w_m2": "W m-2",
    "wind2m_ms": "m s-1",
}
# daily variable -> hourly provenance entry it derives from
_DAILY_FROM = {
    "tmin_c": "t_c", "tmax_c": "t_c", "tmean_c": "t_c", "precip_mm": "precip_mm", "et0_mm": "t_c",
    "radiation_mj_m2": "radiation_w_m2", "vapour_pressure_kpa": "td_c", "rh_mean_pct": "rh_pct",
    "wind2m_ms": "wind2m_ms",
}

# (cell key) -> (expires on the monotonic clock, latest available day). One worker per process.
_LATEST_CACHE: Dict[Tuple[float, float, float], Tuple[float, Optional[date]]] = {}


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _error(msg: str, status: int) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _not_ready() -> JSONResponse:
    """The first answer needs fetches the deadline cannot cover."""
    if _retry_promise():
        return JSONResponse({"detail": "history is being prepared, retry"}, status_code=503,
                            headers={"Retry-After": str(RETRY_AFTER_S)})
    return JSONResponse({"detail": "history cache unavailable"}, status_code=503)


def _attribution(year: int) -> str:
    return f"Contains modified Copernicus Climate Change Service information {year}; Open-Meteo (CC BY 4.0)"


def _latest_available(lat: float, lon: float, elev: float, today: date, deadline: Deadline) -> Optional[date]:
    """Last local day on which every served variable is complete, cached 6 h per cell and elevation."""
    key = (round(lat, 2), round(lon, 2), round(elev))
    hit = _LATEST_CACHE.get(key)
    now = _monotonic()
    if hit and hit[0] > now:
        return hit[1]
    latest: Optional[date] = None
    for year in (today.year, today.year - 1):
        doc = history_year(lat, lon, elev, year, today=today, deadline=deadline)
        if doc.get("latest_available"):
            latest = date.fromisoformat(doc["latest_available"])
            break
    _LATEST_CACHE[key] = (_monotonic() + LATEST_TTL_S, latest)
    return latest


def _retry_promise() -> bool:
    """True only if a retry will find what this request finished: the cache works and THIS request
    read or wrote it successfully (per request, not process-wide)."""
    c = cache_mod.get_cache()
    return bool(c.request_ok())


def _runs(years: List[int]) -> List[List[int]]:
    out: List[List[int]] = []
    for y in sorted(years):
        if out and out[-1][-1] == y - 1:
            out[-1].append(y)
        else:
            out.append([y])
    return out


@router.get("/parcel/{parcel_id}/history")
def get_parcel_history(
    parcel_id: str,
    start: str = Query(..., description="First day, YYYY-MM-DD"),
    end: str = Query(..., description="Last day (inclusive), YYYY-MM-DD"),
    resolution: str = Query("daily", description="daily | hourly"),
    vars: Optional[str] = Query(None, description="Comma-separated variables (default: all of the resolution)"),
    tenant_id: str = Depends(require_auth),
):
    deadline = Deadline(DEADLINE_S, lambda: _monotonic())
    if not _SEM.acquire(blocking=False):
        return JSONResponse({"detail": "too many history requests in progress"}, status_code=503,
                            headers={"Retry-After": str(RETRY_AFTER_S)})
    try:
        cache_mod.get_cache().begin_request()
        return _serve(parcel_id, start, end, resolution, vars, tenant_id, deadline)
    finally:
        _SEM.release()


def _serve(parcel_id: str, start: str, end: str, resolution: str, vars: Optional[str], tenant_id: str,
           deadline: Deadline):
    d0, d1 = _parse_day(start), _parse_day(end)
    if d0 is None or d1 is None:
        return _error("start and end must be valid YYYY-MM-DD dates", 400)
    if d1 < d0:
        return _error("end must not be before start", 400)
    if d0 < MIN_DATE:
        return _error(f"start must not be before {MIN_DATE.isoformat()}", 400)
    if resolution not in ("daily", "hourly"):
        return _error("resolution must be daily or hourly", 400)
    units = DAILY_UNITS if resolution == "daily" else HOURLY_UNITS
    if vars is None or not vars.strip():
        wanted = list(units)
    else:
        wanted = [v.strip() for v in vars.split(",") if v.strip()]
        unknown = [v for v in wanted if v not in units]
        if unknown or not wanted:
            return _error(f"unknown variable(s) for {resolution} resolution; allowed: {', '.join(units)}", 400)
    if resolution == "hourly" and (d1 - d0).days + 1 > MAX_HOURLY_DAYS:
        return _error(f"hourly span too long (max {MAX_HOURLY_DAYS} days)", 400)
    today = _today()
    if d1 > today:
        return _error("end is after the latest available day", 400)

    parcel_urn = _normalize_parcel_id(parcel_id)
    entity = _fetch_parcel_entity(parcel_urn, tenant_id)
    if not entity:
        return _error("parcel not found", 404)
    try:
        loc = _resolve_parcel_location(entity)
    except Exception:  # noqa: BLE001 — unusable geometry is a data problem of the parcel
        loc = None
    if loc is None:
        return _error("parcel has no usable location", 422)
    lon, lat = loc
    elev = _parcel_elevation(entity)
    if elev is None or not math.isfinite(elev) or not elev > 0:
        return _error("parcel elevation is missing or not above 0 m; set it on the parcel to get history", 422)

    def fail(exc: Exception, what: str):
        logger.error("history %s failed parcel=%s tenant=%s: %s", what, parcel_urn, tenant_id, exc)
        return _error("weather archive temporarily unavailable", 502)

    try:
        latest = _latest_available(lat, lon, elev, today, deadline)
    except DeadlineExceeded:
        if d1 >= today - timedelta(days=LATEST_LENIENT_DAYS):
            return _not_ready()
        logger.warning("history latest_available skipped: deadline parcel=%s tenant=%s", parcel_urn, tenant_id)
        latest = None
    except (archive.ArchiveError, ValueError) as exc:
        if d1 >= today - timedelta(days=LATEST_LENIENT_DAYS):
            return fail(exc, "latest_available")
        logger.error("history latest_available failed parcel=%s tenant=%s: %s", parcel_urn, tenant_id, exc)
        latest = None
    if latest is not None and d1 > latest:
        return _error(f"end is after the latest available day ({latest.isoformat()})", 400)

    docs: Dict[int, dict] = {}
    incomplete: List[int] = []
    years_desc = list(range(d1.year, d0.year - 1, -1))
    for i, year in enumerate(years_desc):
        if deadline.remaining() < MIN_FETCH_BUDGET_S:
            incomplete.extend(years_desc[i:])
            break
        try:
            docs[year] = history_year(lat, lon, elev, year, today=today, deadline=deadline)
        except DeadlineExceeded:
            incomplete.append(year)      # a cold year does not fit; an older one may still be cached
            continue
        except (archive.ArchiveError, ValueError) as exc:
            if not docs:
                return fail(exc, f"year {year}")
            logger.error("history year %s failed parcel=%s tenant=%s: %s", year, parcel_urn, tenant_id, exc)
            incomplete.extend(years_desc[i:])
            break

    # Dec 31 precipitation/radiation close with the next year's first stamp.
    support: Dict[int, dict] = {}
    if resolution == "daily" and (d1.month, d1.day) == (12, 31) and d1.year in docs and d1.year + 1 <= today.year:
        if deadline.remaining() >= MIN_FETCH_BUDGET_S:
            try:
                sdoc = history_year(lat, lon, elev, d1.year + 1, today=today, deadline=deadline)
                if sdoc.get("latest_available"):
                    support[d1.year + 1] = sdoc
                else:
                    logger.warning("history closing year has no complete day: dropped parcel=%s", parcel_urn)
            except DeadlineExceeded:
                logger.warning("history closing year skipped: deadline parcel=%s", parcel_urn)
            except (archive.ArchiveError, ValueError) as exc:
                logger.error("history closing year failed parcel=%s tenant=%s: %s", parcel_urn, tenant_id, exc)

    incomplete.sort()
    if incomplete and not _retry_promise():
        return JSONResponse({"detail": "history cache unavailable"}, status_code=503)
    cap = latest if latest is not None else today
    rows: List[Dict[str, Any]] = []
    all_docs = {**support, **docs}
    for run in _runs(list(all_docs)):
        run_docs = [all_docs[y] for y in run]
        if resolution == "daily":
            part = [r for r in daily_from_hourly(run_docs, lat=lat, elev=elev)
                    if d0.isoformat() <= r["date"] <= d1.isoformat() and r["date"] <= cap.isoformat()]
        else:
            tz = run_docs[0]["tz"]
            part = []
            for r in hourly_rows(run_docs):
                ts = int(datetime.strptime(r["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
                if d0 <= local_date(ts, tz) <= min(d1, cap):
                    part.append(r)
        rows.extend(part)
    key = "date" if resolution == "daily" else "time"
    rows.sort(key=lambda r: r[key])
    rows = [{k: r[k] for k in (key, *wanted)} for r in rows]

    ref = docs[max(docs)] if docs else (next(iter(support.values())) if support else None)
    prov: Dict[str, Any] = {}
    for v in wanted:
        src = _DAILY_FROM[v] if resolution == "daily" else v
        if ref is not None:
            p = dict(ref["provenance"][src])
            if v == "et0_mm":
                p = {"source": "era5", "altitude_method": "fao56_penman_monteith_from_" + p["altitude_method"]}
            if resolution == "daily":
                p["day_definition"] = DAY_DEFINITION
            prov[v] = p
        else:
            prov[v] = {"source": "era5", "altitude_method": None}
            if resolution == "daily":
                prov[v]["day_definition"] = DAY_DEFINITION

    if ref is not None:
        coastal = bool(ref.get("coastal"))
    else:
        info = peek_cell(lat, lon, elev)
        coastal = bool(info and info.coastal)
    body: Dict[str, Any] = {
        "parcel_id": parcel_urn,
        "start": start,
        "end": end,
        "resolution": resolution,
        "latest_available": latest.isoformat() if latest else None,
        ("days" if resolution == "daily" else "hours"): rows,
        "units": {v: units[v] for v in wanted},
        "provenance": prov,
        "validation": {v: validation_for(lat, lon, v, resolution, coastal=coastal) for v in wanted},
        "attribution": _attribution(today.year),
    }
    headers = {}
    if incomplete:
        body["incomplete_years"] = incomplete
        headers["Retry-After"] = str(RETRY_AFTER_S)
    return JSONResponse(body, headers=headers)
