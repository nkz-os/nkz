"""
GET /api/weather/coordinates — coordinate-based Open-Meteo forecast.

Replaces the deprecated municipality-based forecast endpoint for
international tenants. No DB dependency — pure lat/lon → Open-Meteo.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import requests
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.auth import require_auth_optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["coordinates-forecast"])

OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


@router.get("/coordinates")
def get_coordinates_forecast(
    lat: float = Query(..., ge=-90, le=90, description="Latitude"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude"),
    name: Optional[str] = Query(None, description="Display name for the location"),
    days: int = Query(7, le=14, description="Forecast days (max 14)"),
    tenant_id: str = Depends(require_auth_optional),
):
    """
    Direct Open-Meteo forecast for arbitrary coordinates — no DB, no catalog.

    Use this for:
    - Tenant home location (widget superior)
    - Any coordinate-based forecast query
    - International locations (no catalog_municipalities dependency)

    Returns the same JSON structure as the deprecated municipality forecast.
    """
    try:
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
            "timezone": "auto",
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

        # Build clean forecast response (same structure as municipality_forecast)
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

        return {
            "coordinates": {"latitude": lat, "longitude": lon},
            "location_name": name,
            "elevation_m": raw.get("elevation"),
            "forecast_days": days,
            "forecast": forecast,
            "source": "OPEN-METEO",
            "cached": False,
        }

    except requests.exceptions.Timeout:
        return JSONResponse(
            {"error": "Open-Meteo request timed out"}, status_code=504
        )
    except Exception as e:
        logger.error(f"Error in coordinates forecast: {e}", exc_info=True)
        return JSONResponse(
            {"error": "Failed to fetch coordinates forecast"}, status_code=500
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
