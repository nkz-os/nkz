"""On-demand Open-Meteo fill for days a parcel's closed-day series lacks.

Nothing is stored: recent days come from the forecast model's own analysis
(the same model the live series uses), older days from the reanalysis
archive. A day the source cannot provide stays unfilled.
"""
import logging
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

import requests

from app.config import settings

logger = logging.getLogger(__name__)

MODEL_WINDOW_DAYS = 92
WIND_10M_TO_2M = 0.748  # FAO-56 log profile, 10 m -> 2 m
_TIMEOUTS = {"model_analysis": 30, "reanalysis": 120}
_DAILY_VARS = (
    "temperature_2m_min,temperature_2m_max,precipitation_sum,"
    "et0_fao_evapotranspiration,shortwave_radiation_sum,wind_speed_10m_mean"
)


def plan_fill(days: List[date], today: date) -> Dict[str, List[date]]:
    horizon = today - timedelta(days=MODEL_WINDOW_DAYS)
    plan: Dict[str, List[date]] = {"model_analysis": [], "reanalysis": [], "not_closed": []}
    for d in sorted(days):
        if d >= today:
            plan["not_closed"].append(d)
        elif d >= horizon:
            plan["model_analysis"].append(d)
        else:
            plan["reanalysis"].append(d)
    return plan


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _at(daily: Dict[str, list], key: str, i: int) -> Optional[float]:
    col = daily.get(key) or []
    return _num(col[i]) if i < len(col) else None


def fetch_open_meteo_daily(
    kind: str,
    lat: float,
    lon: float,
    elevation: Optional[float],
    d0: date,
    d1: date,
    http_get: Callable = requests.get,
) -> Dict[str, Dict[str, Optional[float]]]:
    if kind == "reanalysis":
        base, models, path = settings.openmeteo_archive_url, settings.openmeteo_archive_models, "archive"
    else:
        base, models, path = settings.openmeteo_api_url, settings.openmeteo_models, "forecast"
    if not base:
        return {}
    params: Dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "start_date": d0.isoformat(),
        "end_date": d1.isoformat(),
        "daily": _DAILY_VARS,
        "timezone": "auto",
        "wind_speed_unit": "ms",
    }
    if models:
        params["models"] = models
    if elevation is not None:
        params["elevation"] = elevation
    try:
        resp = http_get(f"{base}/{path}", params=params, timeout=_TIMEOUTS[kind])
        if resp.status_code != 200:
            logger.warning("open-meteo %s fill HTTP %s", kind, resp.status_code)
            return {}
        daily = (resp.json() or {}).get("daily") or {}
    except Exception as exc:  # noqa: BLE001 — a failed fill leaves the days unfilled
        logger.warning("open-meteo %s fill failed: %s", kind, exc)
        return {}

    out: Dict[str, Dict[str, Optional[float]]] = {}
    for i, day in enumerate(daily.get("time") or []):
        wind10 = _at(daily, "wind_speed_10m_mean", i)
        rec = {
            "tmin_c": _at(daily, "temperature_2m_min", i),
            "tmax_c": _at(daily, "temperature_2m_max", i),
            "precip_mm": _at(daily, "precipitation_sum", i),
            "et0_mm": _at(daily, "et0_fao_evapotranspiration", i),
            "radiation_mj_m2": _at(daily, "shortwave_radiation_sum", i),
            "vapour_pressure_kpa": None,
            "wind2m_ms": round(wind10 * WIND_10M_TO_2M, 3) if wind10 is not None else None,
        }
        if any(v is not None for v in rec.values()):
            out[day] = rec
    return out
