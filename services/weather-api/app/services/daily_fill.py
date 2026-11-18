"""On-demand Open-Meteo fill for days a parcel's closed-day series lacks.

Nothing is stored: recent days come from the forecast model's own analysis
(the same model the live series uses), older days from the reanalysis
archive. A day the source cannot provide stays unfilled.

Tmin of the past-day fill is not corrected for altitude (owner decision). Forecast and
running days are NOT changed (risk's frost warning is calibrated on them). For an inland parcel it is read from a second call with ``elevation=nan``,
the raw model cell, over the same local days; every other variable keeps the
elevated call. A coastal parcel (``CellInfo.coastal``, the /history rule) keeps
the elevated call's Tmin. If the raw call fails or its cell cannot be
determined, Tmin is null with reason ``tmin_raw_unavailable`` (``tmin_raw_timeout`` when
the caller's budget has no room for the call or it timed out): never the lapse-corrected value. Method date: stored closed days switched on 2026-11-17;
filled days are computed on the fly, so this definition applies to every
filled day, past ones included.
"""
import logging
import math
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from app.config import settings
from app.services.history.year import CellInfo

logger = logging.getLogger(__name__)

MODEL_WINDOW_DAYS = 92
WIND_10M_TO_2M = 0.748  # FAO-56 log profile, 10 m -> 2 m
_TIMEOUTS = {"model_analysis": 30, "reanalysis": 120}
_DAILY_VARS = (
    "temperature_2m_min,temperature_2m_max,precipitation_sum,"
    "et0_fao_evapotranspiration,shortwave_radiation_sum,wind_speed_10m_mean,"
    "dew_point_2m_mean"
)
# Forecast: the automatic selection and ECMWF IFS. The frost warning uses the
# colder of the two per day (`tmin_coldest_c`), which is a warning input, not an
# expected temperature.
FORECAST_MODELS = ("best_match", "ecmwf_ifs025")
_AUTO, _ECMWF = FORECAST_MODELS
_OPEN_METEO_MAX_FORECAST_DAYS = 16
TMIN_RAW_UNAVAILABLE = "tmin_raw_unavailable"
TMIN_RAW_TIMEOUT = "tmin_raw_timeout"
_MIN_RAW_BUDGET_S = 1.0  # less than this left of the caller's budget: the raw call is not attempted
_monotonic = time.monotonic
_HOURS_MIN_FOR_DAY = 23  # a 23 h day exists (DST); fewer hours is an incomplete day


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


def _vapour_pressure_kpa(dew_point_c: Optional[float]) -> Optional[float]:
    """Actual vapour pressure (FAO-56 eq. 14 / Tetens) from the daily mean dew point."""
    if dew_point_c is None:
        return None
    return round(0.6108 * math.exp(17.27 * dew_point_c / (dew_point_c + 237.3)), 3)


def _at(daily: Dict[str, list], key: str, i: int) -> Optional[float]:
    col = daily.get(key)
    return _num(col[i]) if isinstance(col, list) and i < len(col) else None


def _raw_cell_body(
    url: str, params: Dict[str, Any], daily: str, hourly: Optional[str],
    http_get: Callable, timeout: float,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Same request with ``elevation=nan`` (raw model cell) asking only Tmin (+ hourly T).

    Returns (body, reason): body None on failure, with reason `tmin_raw_timeout` when the call
    timed out and `tmin_raw_unavailable` otherwise.
    """
    raw_params = {**params, "elevation": "nan", "daily": daily}
    raw_params.pop("hourly", None)
    if hourly:
        raw_params["hourly"] = hourly
    try:
        resp = http_get(url, params=raw_params, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("open-meteo raw-cell HTTP %s", resp.status_code)
            return None, TMIN_RAW_UNAVAILABLE
        body = resp.json()
        return (body, TMIN_RAW_UNAVAILABLE) if isinstance(body, dict) else (None, TMIN_RAW_UNAVAILABLE)
    except requests.exceptions.Timeout as exc:
        logger.warning("open-meteo raw-cell call timed out: %s", exc)
        return None, TMIN_RAW_TIMEOUT
    except Exception as exc:  # noqa: BLE001 — no raw cell means no Tmin, never a corrected one
        logger.warning("open-meteo raw-cell call failed: %s", exc)
        return None, TMIN_RAW_UNAVAILABLE


def _raw_tmin_applies(main: Dict[str, Any], raw: Optional[Dict[str, Any]]) -> Optional[bool]:
    """True: use the raw cell's Tmin (inland). False: coastal, keep the elevated one. None: cell unknown."""
    if raw is None:
        return None
    try:
        cell = CellInfo(
            float(raw["latitude"]), float(raw["longitude"]), _num(raw.get("elevation")),
            float(main["latitude"]), float(main["longitude"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return not cell.coastal


def fetch_open_meteo_daily(
    kind: str,
    lat: float,
    lon: float,
    elevation: Optional[float],
    d0: date,
    d1: date,
    http_get: Callable = requests.get,
    timeout: Optional[float] = None,
) -> Dict[str, Dict[str, Optional[float]]]:
    if kind == "reanalysis":
        base, models, path = settings.openmeteo_archive_url, settings.openmeteo_archive_models, "archive"
    else:
        base, models, path = settings.openmeteo_api_url, settings.openmeteo_daily_models, "forecast"
    if not base:
        return {}
    started = _monotonic()
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
    # 0 means "unknown" (as in the worker); sea-level 0 would bias frost inputs
    if elevation is not None and elevation > 0:
        params["elevation"] = elevation
    try:
        resp = http_get(
            f"{base}/{path}", params=params,
            timeout=timeout if timeout is not None else _TIMEOUTS[kind],
        )
        if resp.status_code != 200:
            logger.warning("open-meteo %s fill HTTP %s", kind, resp.status_code)
            return {}
        body = resp.json()
        daily = body.get("daily") if isinstance(body, dict) else None
    except Exception as exc:  # noqa: BLE001 — a failed fill leaves the days unfilled
        logger.warning("open-meteo %s fill failed: %s", kind, exc)
        return {}

    if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
        return {}
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for i, day in enumerate(daily["time"]):
        wind10 = _at(daily, "wind_speed_10m_mean", i)
        rec = {
            "tmin_c": _at(daily, "temperature_2m_min", i),
            "tmax_c": _at(daily, "temperature_2m_max", i),
            "precip_mm": _at(daily, "precipitation_sum", i),
            "et0_mm": _at(daily, "et0_fao_evapotranspiration", i),
            "radiation_mj_m2": _at(daily, "shortwave_radiation_sum", i),
            "vapour_pressure_kpa": _vapour_pressure_kpa(_at(daily, "dew_point_2m_mean", i)),
            "wind2m_ms": round(wind10 * WIND_10M_TO_2M, 3) if wind10 is not None else None,
        }
        if any(v is not None for v in rec.values()):
            out[day] = rec
    if not out:
        return out

    # Tmin from the raw cell unless coastal; one extra call, only when days are filled, and
    # only within what is left of the caller's budget (never past FILL_DEADLINE_S).
    budget = timeout if timeout is not None else _TIMEOUTS[kind]
    left = budget - (_monotonic() - started)
    if left < _MIN_RAW_BUDGET_S:
        raw, fail_reason = None, TMIN_RAW_TIMEOUT
    else:
        raw, fail_reason = _raw_cell_body(f"{base}/{path}", params, "temperature_2m_min", None, http_get, left)
    inland = _raw_tmin_applies(body, raw)
    raw_by_day: Dict[str, Optional[float]] = {}
    if inland:
        raw_daily = raw.get("daily") if isinstance(raw.get("daily"), dict) else {}
        for i, day in enumerate(raw_daily.get("time") or []):
            if isinstance(day, str):
                raw_by_day[day] = _at(raw_daily, "temperature_2m_min", i)
    for day, rec in out.items():
        if inland is False:
            continue
        tmin = raw_by_day.get(day) if inland else None
        rec["tmin_c"] = tmin
        if tmin is None:
            rec["tmin_reason"] = fail_reason if raw is None else TMIN_RAW_UNAVAILABLE
    return out



def _hours_below_zero(hourly: Dict[str, list], key: str) -> Dict[str, Optional[float]]:
    """Hours with T <= 0 degC per local day; None when the day's hours are incomplete."""
    times, col = hourly.get("time"), hourly.get(key)
    if not isinstance(times, list) or not isinstance(col, list):
        return {}
    per_day: Dict[str, List[Optional[float]]] = {}
    for t, v in zip(times, col):
        if isinstance(t, str):
            per_day.setdefault(t[:10], []).append(_num(v))
    return {
        day: (float(sum(1 for v in vals if v <= 0)) if len(vals) >= _HOURS_MIN_FOR_DAY and None not in vals else None)
        for day, vals in per_day.items()
    }


def fetch_open_meteo_forecast(
    lat: float,
    lon: float,
    elevation: Optional[float],
    forecast_days: int,
    now: Optional[datetime] = None,
    http_get: Callable = requests.get,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Local today plus `forecast_days` days from the model forecast; {} on any failure.

    Nothing is stored. Days carry the automatic selection's values; the day's
    `kind` is `running` for the parcel's local today and `forecast` after it.
    `tmin_coldest_c` is the colder of the two models, null unless both gave a value.
    """
    base = settings.openmeteo_api_url
    if not base:
        return {}
    now = now or datetime.now(timezone.utc)
    params: Dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "daily": _DAILY_VARS,
        "hourly": "temperature_2m",
        "models": ",".join(FORECAST_MODELS),
        "timezone": "auto",
        "wind_speed_unit": "ms",
        "past_days": 0,
        # today + N days; Open-Meteo allows at most 16 days in all
        "forecast_days": min(forecast_days + 1, _OPEN_METEO_MAX_FORECAST_DAYS),
    }
    # 0 means "unknown" (as in the worker); sea-level 0 would bias frost inputs
    if elevation is not None and elevation > 0:
        params["elevation"] = elevation
    try:
        resp = http_get(f"{base}/forecast", params=params, timeout=timeout if timeout is not None else 30)
        if resp.status_code != 200:
            logger.warning("open-meteo forecast HTTP %s", resp.status_code)
            return {}
        body = resp.json()
        daily = body.get("daily") if isinstance(body, dict) else None
        offset = body.get("utc_offset_seconds") if isinstance(body, dict) else None
        hourly = body.get("hourly") if isinstance(body, dict) and isinstance(body.get("hourly"), dict) else {}
    except Exception as exc:  # noqa: BLE001 — a failed forecast leaves the days unavailable
        logger.warning("open-meteo forecast failed: %s", exc)
        return {}
    try:
        if not isinstance(daily, dict) or not isinstance(daily.get("time"), list) or _num(offset) is None:
            return {}

        local_dt = now + timedelta(seconds=_num(offset))
        local_today = local_dt.date().isoformat()
        local_now = local_dt.strftime("%Y-%m-%dT%H:%M")
        frost_hours = _hours_below_zero(hourly, f"temperature_2m_{_AUTO}")
        out: Dict[str, Dict[str, Any]] = {}
        for i, day in enumerate(daily["time"]):
            col = lambda name, model=_AUTO: _at(daily, f"{name}_{model}", i)  # noqa: E731
            wind10 = col("wind_speed_10m_mean")
            tmin, tmin_ecmwf = col("temperature_2m_min"), col("temperature_2m_min", _ECMWF)
            rec: Dict[str, Any] = {
                "tmin_c": tmin,
                "tmax_c": col("temperature_2m_max"),
                "precip_mm": col("precipitation_sum"),
                "et0_mm": col("et0_fao_evapotranspiration"),
                "radiation_mj_m2": col("shortwave_radiation_sum"),
                "vapour_pressure_kpa": _vapour_pressure_kpa(col("dew_point_2m_mean")),
                "wind2m_ms": round(wind10 * WIND_10M_TO_2M, 3) if wind10 is not None else None,
                "hours_below_zero": frost_hours.get(day),
                "tmin_coldest_c": min(tmin, tmin_ecmwf) if tmin is not None and tmin_ecmwf is not None else None,
            }
            if any(v is not None for v in rec.values()):
                rec["kind"] = "running" if day == local_today else "forecast"
                rec["source"] = "model_forecast"
                out[day] = rec
        return {"days": out, "local_today": local_today, "local_now": local_now, "issued_at": now.isoformat()}
    except Exception as exc:  # noqa: BLE001 — a malformed body leaves the days unavailable
        logger.warning("open-meteo forecast unparsable: %s", type(exc).__name__)
        return {}
