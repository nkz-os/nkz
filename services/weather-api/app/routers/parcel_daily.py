"""
GET /api/weather/parcel/{parcel_id}/daily?start=YYYY-MM-DD&end=YYYY-MM-DD

One record per calendar day of the parcel's CLOSED-day weather series (published by
weather-worker on a per-parcel ``...-daily`` WeatherObserved and persisted to
``telemetry_events`` through the Orion subscription). A day without a record is
listed in ``missing_days`` and its fields are null: nothing is interpolated or
carried forward. With ``fill=open_meteo`` such days are requested on demand from
Open-Meteo (model analysis for recent days, reanalysis before) and tagged with
their ``source``; nothing is stored.

``tmin_coldest_c`` (forecast days only) is the colder of two models. It is a
warning input for frost, not an expected temperature: never show it as a forecast.
"""

import json
import time
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
from app.services import sensor_daily
from app.services.daily_fill import fetch_open_meteo_daily, fetch_open_meteo_forecast, plan_fill
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
    "hours_below_zero": "frostHours",
}

_UNITS = {
    "tmin_c": "degC",
    "tmax_c": "degC",
    "precip_mm": "mm d-1",
    "et0_mm": "mm d-1",
    "radiation_mj_m2": "MJ m-2 d-1",
    "vapour_pressure_kpa": "kPa",
    "wind2m_ms": "m s-1",
    "hours_below_zero": "h",
    # coldest of two models: a warning input for frost, not an expected temperature
    "tmin_coldest_c": "degC",
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
MAX_FORECAST_DAYS = 16
# The whole fill must finish inside the gateway's wait for this service; a day
# the budget does not reach is reported, not guessed.
FILL_DEADLINE_S = 25.0
_monotonic = time.monotonic


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


def _parcel_devices(parcel_urn: str, tenant_id: str) -> List[str]:
    """Short ids of the IoT devices whose controlledAsset is the parcel (raises when the broker fails)."""
    resp = requests.get(
        f"{settings.orion_url}/ngsi-ld/v1/entities",
        params={"type": "Device", "q": f'controlledAsset=="{parcel_urn}"', "limit": 100},
        headers=_orion_headers(tenant_id),
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"device lookup returned {resp.status_code}")
    return sorted({e["id"].split(":")[-1] for e in resp.json() if e.get("id")})


def _sensor_readings(tenant_id: str, devices: List[str], lo: datetime, hi: datetime) -> List[sensor_daily.Reading]:
    """Valid, calibrated readings of the devices in [lo, hi) (raises when the store fails)."""
    conn = get_db_connection(tenant_id)
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT device_id, observed_at, payload->'measurements' AS measurements
            FROM telemetry_events
            WHERE tenant_id = %s
              AND entity_type = 'DeviceMeasurement'
              AND device_id = ANY(%s)
              AND quality_flag = 'valid'
              AND observed_at >= %s AND observed_at < %s
            """,
            (tenant_id, devices, lo, hi),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    out: List[sensor_daily.Reading] = []
    for row in rows:
        m = row.get("measurements") or {}
        if isinstance(m, str):
            m = json.loads(m)
        ts = row["observed_at"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        for name, value in m.items():
            v = _num(value)
            if v is not None:
                out.append(sensor_daily.Reading(row["device_id"], ts, name, v))
    return out


def _apply_sensors(tenant_id: str, parcel_urn: str, d0: date, d1: date, days: List[Dict[str, Any]],
                   entity: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Fold the parcel's IoT sensors into `days` (precedence sensor > parcel weather > fill).

    A sensor failure never takes the series down: it is served without them and says so.
    """
    try:
        devices = _parcel_devices(parcel_urn, tenant_id)
        if devices:
            entity = entity or _fetch_parcel_entity(parcel_urn, tenant_id) or {}
            tz_attr = entity.get("timeZone")
            tz = sensor_daily.tz_or_utc(tz_attr.get("value") if isinstance(tz_attr, dict) else tz_attr)
            # Local days can start up to 14 h either side of UTC midnight.
            lo = datetime(d0.year, d0.month, d0.day, tzinfo=timezone.utc) - timedelta(hours=14)
            hi = datetime(d1.year, d1.month, d1.day, tzinfo=timezone.utc) + timedelta(days=1, hours=14)
            sensor_daily.merge(days, sensor_daily.aggregate(_sensor_readings(tenant_id, devices, lo, hi), tz))
        else:
            sensor_daily.merge(days, {})
        return {"sensor_devices": devices, "sensors_unavailable": False}
    except Exception as e:  # noqa: BLE001 — the model series does not depend on sensors
        logger.warning("IoT sensors unavailable for %s tenant=%s: %s", parcel_urn, tenant_id, e)
        sensor_daily.merge(days, {})
        return {"sensor_devices": [], "sensors_unavailable": True}


def _parcel_elevation(entity: Dict[str, Any]) -> Optional[float]:
    elev = entity.get("elevation")
    value = elev.get("value") if isinstance(elev, dict) else elev
    return _num(value)


@router.get("/parcel/{parcel_id}/daily")
def get_parcel_daily(
    parcel_id: str,
    start: str = Query(..., description="First day, YYYY-MM-DD"),
    end: str = Query(..., description="Last day (inclusive), YYYY-MM-DD"),
    fill: str = Query("none", description="none | open_meteo"),
    base_temp: Optional[float] = Query(None, description="GDD base temperature, degC"),
    upper_cutoff: Optional[float] = Query(None, description="GDD upper cutoff, degC"),
    forecast_days: int = Query(0, description="Days of model forecast after the parcel's local today, 0-16"),
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

    if not 0 <= forecast_days <= MAX_FORECAST_DAYS:
        return _error(f"forecast_days must be between 0 and {MAX_FORECAST_DAYS}", 400)

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
    today = _today()
    deadline = _monotonic() + FILL_DEADLINE_S
    unfilled = [date.fromisoformat(d["date"]) for d in days if d["source"] is None]
    entity: Optional[Dict[str, Any]] = None
    loc = None
    if forecast_days > 0 or (fill == "open_meteo" and unfilled):
        entity = _fetch_parcel_entity(parcel_urn, tenant_id)
        try:
            loc = _resolve_parcel_location(entity) if entity else None
        except Exception as e:  # noqa: BLE001 — unusable geometry means no fill
            logger.warning("parcel location unusable for fill %s: %s", parcel_urn, e)
            loc = None

    # Fills are keyed on the UTC day in both modes and run first: which closed
    # days get filled, and so `totals`, must not depend on `forecast_days`.
    if fill == "open_meteo" and unfilled:
        plan = plan_fill(unfilled, today)
        for d in plan["not_closed"]:
            reasons[d.isoformat()] = "not_closed"
        filled: Dict[str, Dict[str, Any]] = {}
        if loc is not None:
            lon, lat = loc
            elev = _parcel_elevation(entity)
            for kind in ("model_analysis", "reanalysis"):
                wanted = {d.isoformat() for d in plan[kind]}
                remaining = deadline - _monotonic()
                if not wanted or remaining <= 1.0:
                    continue
                # One request per kind over its whole span: scattered gaps must
                # not turn into one request each. Only the asked days are kept.
                got = fetch_open_meteo_daily(
                    kind, lat, lon, elev, min(plan[kind]), max(plan[kind]),
                    timeout=remaining,
                )
                for k, v in got.items():
                    if k in wanted:
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

    # The forecast gets what is left of the budget; none left means unavailable.
    forecast: Dict[str, Any] = {}
    if forecast_days > 0 and loc is not None:
        remaining = deadline - _monotonic()
        if remaining > 1.0:
            lon, lat = loc
            forecast = fetch_open_meteo_forecast(
                lat, lon, _parcel_elevation(entity), forecast_days, timeout=remaining,
            )
    # The parcel's own calendar day when the forecast told it, UTC otherwise.
    local_today = date.fromisoformat(forecast["local_today"]) if forecast else today

    # Sensors only describe finished days: forecast days are appended after this.
    sensors = _apply_sensors(tenant_id, parcel_urn, d0, d1, days, entity)

    if forecast_days > 0:
        # A day without data from the parcel's today on is the forecast's. A day
        # that has data is finished (stored rows and fills only exist for those)
        # and stays, so the closed series is the same as without `forecast_days`.
        for d in days:
            if d["source"] is None and date.fromisoformat(d["date"]) >= local_today:
                reasons.pop(d["date"], None)
        days = [d for d in days if d["source"] is not None or date.fromisoformat(d["date"]) < local_today]
        have = {d["date"] for d in days}
        first = max(d0, local_today)
        last = max(d1, local_today + timedelta(days=forecast_days))
        for i in range((last - first).days + 1):
            key = (first + timedelta(days=i)).isoformat()
            if key in have:
                continue
            model_day = forecast.get("days", {}).get(key)
            rec = {"date": key, **{f: None for f in _FIELDS}, "source": None, "tmin_coldest_c": None}
            if model_day is not None:
                rec.update(model_day)
                rec.pop("kind", None)
                rec["sources"] = {f: ("model_forecast" if rec[f] is not None else None) for f in _FIELDS}
            else:
                reasons[key] = "forecast_unavailable"
                rec["sources"] = {f: None for f in _FIELDS}
            days.append(rec)
        days.sort(key=lambda d: d["date"])

    for rec in days:
        day = date.fromisoformat(rec["date"])
        # A non-null source means a finished day: stored rows and fills only exist
        # for those, and dating it would drop a real closed day from totals around
        # local midnight.
        if rec["source"] not in (None, "model_forecast"):
            rec["kind"] = "closed"
        else:
            rec["kind"] = "closed" if day < local_today else ("running" if day == local_today else "forecast")

    missing = [d["date"] for d in days if d["source"] is None]
    closed = [d for d in days if d["kind"] == "closed" and d["source"] is not None]

    extra: Dict[str, Any] = {}
    if forecast_days > 0:
        extra = {
            "forecast_issued_at": forecast.get("issued_at"),
            "local_today": forecast.get("local_today"),
        }
    source = _SOURCE if fill == "none" else f"{_SOURCE}; gaps: Open-Meteo model analysis (recent) and reanalysis archive"
    if forecast_days > 0:
        source += "; running and forecast days: Open-Meteo model forecast (not stored)"

    return {
        "parcel_id": parcel_urn,
        "start": start,
        "end": end,
        "days": days,
        "missing_days": missing,
        "missing_reasons": reasons,
        "totals": compute_totals(closed, base_temp, upper_cutoff),
        "units": _UNITS,
        **sensors,
        **extra,
        "source": source,
    }
