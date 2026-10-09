"""
GET /api/weather/parcel/{parcel_id}/daily?start=YYYY-MM-DD&end=YYYY-MM-DD

One record per calendar day of the parcel's CLOSED-day weather series (published by
weather-worker on a per-parcel ``...-daily`` WeatherObserved and persisted to
``telemetry_events`` through the Orion subscription). A day without a record is
listed in ``missing_days`` and its fields are null: nothing is filled, interpolated
or carried forward.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
import requests
from psycopg2.extras import RealDictCursor

from app.auth import require_auth
from app.deps import get_db_connection
from app.config import settings
from app.routers.parcels import _normalize_parcel_id, _orion_headers, _resolve_parcel_location
from app.services.daily_fill import fetch_open_meteo_daily, plan_fill
from app.services.daily_totals import compute_totals

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["parcels"])

MAX_RANGE_DAYS = 400

# W m-2 (daily mean, as stored) -> MJ m-2 d-1
_W_M2_TO_MJ_M2_DAY = 0.0864

# response field -> stored attribute (compact name in telemetry measurements)
_FIELDS = {
    "tmin_c": "tempMin",
    "tmax_c": "tempMax",
    "precip_mm": "precipitation",
    "et0_mm": "et0",
    "radiation_mj_m2": "solarRadiation",
    "vapour_pressure_kpa": "vapourPressure",
    "wind2m_ms": "windSpeed2m",
}

_UNITS = {
    "tmin_c": "degC",
    "tmax_c": "degC",
    "precip_mm": "mm d-1",
    "et0_mm": "mm d-1",
    "radiation_mj_m2": "MJ m-2 d-1",
    "vapour_pressure_kpa": "kPa",
    "wind2m_ms": "m s-1",
}

_SOURCE = (
    "telemetry_events: closed-day WeatherObserved published by weather-worker "
    "(Open-Meteo model day; tmin/tmax lapse-corrected to parcel altitude)"
)


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _error(msg: str, status: int) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _parse_day(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


_FILL_MODES = ("none", "open_meteo")


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _fetch_parcel_entity(parcel_urn: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    try:
        resp = requests.get(
            f"{settings.orion_url}/ngsi-ld/v1/entities/{parcel_urn}",
            headers=_orion_headers(tenant_id),
            timeout=10,
        )
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:  # noqa: BLE001 — no location means no fill, not an error
        logger.warning("parcel lookup for fill failed %s: %s", parcel_urn, e)
        return None


def _parcel_elevation(entity: Dict[str, Any]) -> Optional[float]:
    elev = entity.get("elevation")
    value = elev.get("value") if isinstance(elev, dict) else elev
    return _num(value)


def _spans(days: List[date]) -> List[tuple]:
    """Contiguous (first, last) runs, so each run is one Open-Meteo request."""
    runs: List[tuple] = []
    for d in days:
        if runs and (d - runs[-1][1]).days == 1:
            runs[-1] = (runs[-1][0], d)
        else:
            runs.append((d, d))
    return runs


@router.get("/parcel/{parcel_id}/daily")
def get_parcel_daily(
    parcel_id: str,
    start: str = Query(..., description="First day, YYYY-MM-DD"),
    end: str = Query(..., description="Last day (inclusive), YYYY-MM-DD"),
    fill: str = Query("none", description="none | open_meteo"),
    base_temp: Optional[float] = Query(None, description="GDD base temperature, degC"),
    upper_cutoff: Optional[float] = Query(None, description="GDD upper cutoff, degC"),
    tenant_id: str = Depends(require_auth),
):
    d0, d1 = _parse_day(start), _parse_day(end)
    if d0 is None or d1 is None:
        return _error("start and end must be valid YYYY-MM-DD dates", 400)
    if d1 < d0:
        return _error("end must not be before start", 400)
    n_days = (d1 - d0).days + 1
    if n_days > MAX_RANGE_DAYS:
        return _error(f"range too long: {n_days} days (max {MAX_RANGE_DAYS})", 400)
    if fill not in _FILL_MODES:
        return _error(f"fill must be one of {', '.join(_FILL_MODES)}", 400)

    parcel_urn = _normalize_parcel_id(parcel_id)
    bare = parcel_urn.split(":")[-1]
    pattern = f"%:parcel-{_escape_like(bare)}-daily"

    lo = datetime(d0.year, d0.month, d0.day, tzinfo=timezone.utc)
    hi = datetime(d1.year, d1.month, d1.day, tzinfo=timezone.utc) + timedelta(days=1)

    conn = None
    try:
        conn = get_db_connection(tenant_id)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT observed_at, payload->'measurements' AS measurements
            FROM telemetry_events
            WHERE tenant_id = %s
              AND entity_type = 'WeatherObserved'
              AND entity_id LIKE %s ESCAPE '\\'
              AND observed_at >= %s AND observed_at < %s
            ORDER BY observed_at ASC
            """,
            (tenant_id, pattern, lo, hi),
        )
        rows = cur.fetchall()
    except Exception as e:
        logger.error("daily series query failed tenant=%s parcel=%s: %s", tenant_id, parcel_urn, e)
        return _error("daily weather series unavailable", 503)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    by_day: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        ts = row["observed_at"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        m = row.get("measurements") or {}
        if isinstance(m, str):
            try:
                m = json.loads(m)
            except json.JSONDecodeError:
                m = {}
        by_day[ts.astimezone(timezone.utc).strftime("%Y-%m-%d")] = m  # last row wins

    days: List[Dict[str, Any]] = []
    for i in range(n_days):
        key = (d0 + timedelta(days=i)).strftime("%Y-%m-%d")
        m = by_day.get(key)
        rec: Dict[str, Any] = {"date": key}
        for field, attr in _FIELDS.items():
            v = _num(m.get(attr)) if m is not None else None
            if v is not None and field == "radiation_mj_m2":
                v = round(v * _W_M2_TO_MJ_M2_DAY, 4)
            rec[field] = v
        rec["source"] = "parcel_weather" if m is not None else None
        days.append(rec)

    reasons: Dict[str, str] = {}
    unfilled = [date.fromisoformat(d["date"]) for d in days if d["source"] is None]
    today = _today()
    if fill == "open_meteo" and unfilled:
        plan = plan_fill(unfilled, today)
        for d in plan["not_closed"]:
            reasons[d.isoformat()] = "not_closed"
        entity = _fetch_parcel_entity(parcel_urn, tenant_id)
        loc = _resolve_parcel_location(entity) if entity else None
        filled: Dict[str, Dict[str, Any]] = {}
        if loc is not None:
            lon, lat = loc
            elev = _parcel_elevation(entity)
            for kind in ("model_analysis", "reanalysis"):
                for first, last in _spans(plan[kind]):
                    for k, v in fetch_open_meteo_daily(kind, lat, lon, elev, first, last).items():
                        filled[k] = {**v, "source": kind}
        by_key = {d["date"]: d for d in days}
        for d in plan["model_analysis"] + plan["reanalysis"]:
            k = d.isoformat()
            if k in filled:
                by_key[k].update(filled[k])
            else:
                reasons[k] = "fill_unavailable"
    else:
        for d in unfilled:
            reasons[d.isoformat()] = "not_closed" if d >= today else "no_data"

    missing = [d["date"] for d in days if d["source"] is None]
    closed = [d for d in days if d["source"] is not None]

    return {
        "parcel_id": parcel_urn,
        "start": start,
        "end": end,
        "days": days,
        "missing_days": missing,
        "missing_reasons": reasons,
        "totals": compute_totals(closed, base_temp, upper_cutoff),
        "units": _UNITS,
        "source": _SOURCE if fill == "none" else f"{_SOURCE}; gaps: Open-Meteo model analysis (recent) and reanalysis archive",
    }
