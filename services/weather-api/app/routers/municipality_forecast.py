"""
GET /api/weather/municipality/{ine_code}/forecast — direct Open-Meteo proxy.

DEPRECATED: use GET /api/weather/coordinates?lat=&lon= for international
locations, or GET /api/weather/parcel/{id}/forecast for parcel-specific.
Kept for backward compatibility with existing Spain-only tenants.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import requests
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.auth import require_auth_optional
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["municipality-forecast"])

OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


@router.get("/municipality/{ine_code}/forecast")
def get_municipality_forecast(
    ine_code: str,
    tenant_id: str = Depends(require_auth_optional),
    days: int = Query(7, le=14, description="Forecast days (max 14)"),
):
    """
    Direct Open-Meteo forecast for a municipality — no persistence.

    DEPRECATED: Use GET /api/weather/coordinates for international
    locations, or GET /api/weather/parcel/{id}/forecast for parcels.
    This endpoint depends on catalog_municipalities (Spain-only).
    """
    if not tenant_id:
        tenant_id = "default"

    try:
        # 1. Resolve municipality coordinates
        from app.deps import get_db_connection
        from psycopg2.extras import RealDictCursor

        conn = get_db_connection(tenant_id)
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                SELECT name, province, latitude, longitude, aemet_id
                FROM catalog_municipalities
                WHERE ine_code = %s
                LIMIT 1
                """,
                (ine_code,),
            )
            muni = cur.fetchone()
        finally:
            cur.close()
            conn.close()

        if not muni or not muni.get("latitude") or not muni.get("longitude"):
            return JSONResponse(
                {"error": f"Municipality {ine_code} not found or missing coordinates"},
                status_code=404,
            )

        lat = float(muni["latitude"])
        lon = float(muni["longitude"])

        # 2. Fetch forecast from Open-Meteo
        today = datetime.utcnow().strftime("%Y-%m-%d")
        end = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")

        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": today,
            "end_date": end,
            "daily": [
                "temperature_2m_max",
                "temperature_2m_min",
                "weather_code",
                "precipitation_sum",
                "precipitation_probability_max",
                "wind_speed_10m_max",
                "wind_direction_10m_dominant",
                "et0_fao_evapotranspiration",
                "shortwave_radiation_sum",
            ],
            "timezone": "Europe/Madrid",
        }

        resp = requests.get(OPENMETEO_FORECAST_URL, params=params, timeout=10)
        if resp.status_code != 200:
            return JSONResponse(
                {"error": f"Open-Meteo returned {resp.status_code}"},
                status_code=502,
            )

        raw = resp.json()
        daily = raw.get("daily", {})
        dates = daily.get("time", [])

        # 3. Build clean forecast response
        forecast = []
        for i, date_str in enumerate(dates):
            forecast.append(
                {
                    "date": date_str,
                    "temp_max": _safe_idx(daily, "temperature_2m_max", i),
                    "temp_min": _safe_idx(daily, "temperature_2m_min", i),
                    "weather_code": _safe_idx(daily, "weather_code", i),
                    "precip_mm": _safe_idx(daily, "precipitation_sum", i),
                    "precip_probability": _safe_idx(
                        daily, "precipitation_probability_max", i
                    ),
                    "wind_speed_ms": _safe_idx(daily, "wind_speed_10m_max", i),
                    "wind_direction_deg": _safe_idx(
                        daily, "wind_direction_10m_dominant", i
                    ),
                    "eto_mm": _safe_idx(daily, "et0_fao_evapotranspiration", i),
                    "solar_rad_w_m2": _div_if(
                        _safe_idx(daily, "shortwave_radiation_sum", i), 0.0864
                    ),
                }
            )

        return JSONResponse(
            content={
                "municipality_code": ine_code,
                "municipality_name": muni.get("name"),
                "province": muni.get("province"),
                "coordinates": {"latitude": lat, "longitude": lon},
                "elevation_m": raw.get("elevation"),
                "forecast_days": days,
                "forecast": forecast,
                "source": "OPEN-METEO",
                "cached": False,
                "deprecated": True,
            },
            headers={"Deprecation": "true"},
        )

    except requests.exceptions.Timeout:
        return JSONResponse(
            {"error": "Open-Meteo request timed out"}, status_code=504
        )
    except Exception as e:
        logger.error(f"Error in municipality forecast: {e}", exc_info=True)
        return JSONResponse(
            {"error": "Failed to fetch municipality forecast"}, status_code=500
        )


def _safe_idx(daily: dict, key: str, index: int) -> Optional[float]:
    """Safely get a value from Open-Meteo daily dict by index."""
    arr = daily.get(key, [])
    if arr and index < len(arr) and arr[index] is not None:
        return float(arr[index])
    return None


def _div_if(value: Optional[float], divisor: float) -> Optional[float]:
    """Divide value by divisor if not None."""
    if value is None:
        return None
    return round(value / divisor, 2)
