"""
Agronomic status calculation for parcels.
Extracted from entity-manager weather blueprint (agro-status endpoint).

Fuses sensor data with Open-Meteo current conditions to calculate
spraying, workability, and irrigation semaphores.
"""

import logging
import math
from datetime import datetime
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


def _calc_delta_t(temp_celsius: float, humidity_percent: float) -> Optional[float]:
    """Calculate Delta-T (wet-bulb depression) using Magnus formula."""
    try:
        a = 17.27
        b = 237.7
        alpha = ((a * temp_celsius) / (b + temp_celsius)) + math.log(
            humidity_percent / 100.0
        )
        dew_point = (b * alpha) / (a - alpha)
        wet_bulb = temp_celsius - (temp_celsius - dew_point) * 0.4
        return round(temp_celsius - wet_bulb, 2)
    except Exception:
        return None


def _calc_water_balance(precip_3d: Optional[float], et0_3d: Optional[float]) -> Optional[float]:
    """Calculate 3-day water balance (precipitation - ET0)."""
    if precip_3d is not None and et0_3d is not None:
        return round(precip_3d - et0_3d, 2)
    return None


def calculate_agro_status(
    lat: float,
    lon: float,
    parcel_entity: dict,
    sensor_data: Optional[dict] = None,
    openmeteo_api_url: str = "https://api.open-meteo.com/v1",
) -> dict:
    """
    Calculate agronomic status with semaphores for a parcel.

    Returns a dict with weather, semaphores, and metrics.
    """
    # 1. Fetch Open-Meteo current + daily data
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,pressure_msl,precipitation",
        "hourly": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration",
        "timezone": "Europe/Madrid",
        "forecast_days": 7,
    }

    response = requests.get(openmeteo_api_url, params=params, timeout=10)
    if response.status_code != 200:
        raise Exception(f"Open-Meteo API returned {response.status_code}")

    data = response.json()
    current = data.get("current", {})
    daily = data.get("daily", {})

    openmeteo_data = {
        "temperature": current.get("temperature_2m"),
        "humidity": current.get("relative_humidity_2m"),
        "wind_speed": current.get("wind_speed_10m"),
        "wind_direction": current.get("wind_direction_10m"),
        "pressure": current.get("pressure_msl"),
        "precipitation": current.get("precipitation", 0),
        "et0_today": daily.get("et0_fao_evapotranspiration", [0])[0]
        if daily.get("et0_fao_evapotranspiration")
        else None,
        "precipitation_3d": sum(daily.get("precipitation_sum", [0])[:3])
        if daily.get("precipitation_sum")
        else 0,
        "et0_3d": sum(daily.get("et0_fao_evapotranspiration", [0])[:3])
        if daily.get("et0_fao_evapotranspiration")
        else None,
        "observed_at": datetime.utcnow().isoformat() + "Z",
    }

    # 2. Fuse sensor and Open-Meteo data (Sensor > Open-Meteo)
    fused = {
        "temperature": openmeteo_data.get("temperature"),
        "humidity": openmeteo_data.get("humidity"),
        "wind_speed": openmeteo_data.get("wind_speed"),
        "wind_direction": openmeteo_data.get("wind_direction"),
        "pressure": openmeteo_data.get("pressure"),
        "precipitation": openmeteo_data.get("precipitation", 0),
        "precipitation_3d": openmeteo_data.get("precipitation_3d", 0),
        "et0_today": openmeteo_data.get("et0_today"),
        "et0_3d": openmeteo_data.get("et0_3d"),
        "sources": {
            "temperature": "OPEN-METEO",
            "humidity": "OPEN-METEO",
            "wind_speed": "OPEN-METEO",
            "wind_direction": "OPEN-METEO",
            "pressure": "OPEN-METEO",
            "precipitation": "OPEN-METEO",
        },
        "source_confidence": "OPEN-METEO",
    }

    if sensor_data and sensor_data.get("payload"):
        payload = sensor_data["payload"]
        if "temperature" in payload or "temp" in payload:
            fused["temperature"] = payload.get("temperature") or payload.get("temp")
            fused["sources"]["temperature"] = "SENSOR_REAL"
        if "humidity" in payload:
            fused["humidity"] = payload.get("humidity")
            fused["sources"]["humidity"] = "SENSOR_REAL"
        if "wind_speed" in payload:
            fused["wind_speed"] = payload.get("wind_speed")
            fused["sources"]["wind_speed"] = "SENSOR_REAL"
        if "wind_direction" in payload:
            fused["wind_direction"] = payload.get("wind_direction")
            fused["sources"]["wind_direction"] = "SENSOR_REAL"
        if "pressure" in payload:
            fused["pressure"] = payload.get("pressure")
            fused["sources"]["pressure"] = "SENSOR_REAL"
        fused["source_confidence"] = "SENSOR_REAL"
        fused["sensor"] = {
            "external_id": sensor_data["external_id"],
            "name": sensor_data["name"],
            "distance_m": sensor_data["distance_m"],
            "last_observation": sensor_data["observed_at"],
        }

    # 3. Calculate water balance
    fused["water_balance"] = _calc_water_balance(
        fused.get("precipitation_3d"), fused.get("et0_3d")
    )

    # 4. Calculate Delta-T
    delta_t = None
    if fused.get("temperature") is not None and fused.get("humidity") is not None:
        delta_t = _calc_delta_t(fused["temperature"], fused["humidity"])

    # 5. Semaphores
    semaphores = {"spraying": "unknown", "workability": "unknown", "irrigation": "unknown"}

    wind_speed_ms = fused.get("wind_speed") or 0
    wind_speed_kmh = wind_speed_ms * 3.6
    precip = fused.get("precipitation") or 0

    # Spraying semaphore
    if delta_t is not None and wind_speed_kmh is not None:
        if wind_speed_kmh < 15 and 2 <= delta_t <= 8:
            semaphores["spraying"] = "optimal"
        elif wind_speed_kmh > 20 or delta_t > 10 or (precip and precip > 0.5):
            semaphores["spraying"] = "not_suitable"
        else:
            semaphores["spraying"] = "caution"

    # Workability semaphore
    soil_moisture = None
    if sensor_data and sensor_data.get("payload"):
        p = sensor_data["payload"]
        soil_moisture = p.get("soil_moisture") or p.get("moisture")

    recent_precip = fused.get("precipitation_3d", 0)
    humidity = fused.get("humidity") or 0

    if soil_moisture is not None:
        if 15 <= soil_moisture <= 25:
            semaphores["workability"] = "optimal"
        elif soil_moisture > 25:
            semaphores["workability"] = "too_wet"
        elif soil_moisture < 10:
            semaphores["workability"] = "too_dry"
        else:
            semaphores["workability"] = "caution"
    else:
        if recent_precip > 5 or humidity > 80:
            semaphores["workability"] = "too_wet"
        elif recent_precip == 0 and humidity < 40:
            semaphores["workability"] = "too_dry"
        elif 1 <= recent_precip <= 5 and 40 <= humidity <= 80:
            semaphores["workability"] = "optimal"
        else:
            semaphores["workability"] = "caution"

    # Irrigation semaphore
    water_balance = fused.get("water_balance")
    if water_balance is not None:
        if water_balance > 0:
            semaphores["irrigation"] = "satisfied"
        elif water_balance < -5:
            semaphores["irrigation"] = "deficit"
        else:
            semaphores["irrigation"] = "alert"

    parcel_name = "Unnamed"
    name_attr = parcel_entity.get("name", {})
    if isinstance(name_attr, dict):
        parcel_name = name_attr.get("value", "Unnamed")

    return {
        "parcel_id": parcel_entity.get("id", ""),
        "parcel_name": parcel_name,
        "centroid": {"latitude": lat, "longitude": lon},
        "weather": fused,
        "semaphores": semaphores,
        "metrics": {
            "temperature": fused.get("temperature"),
            "humidity": fused.get("humidity"),
            "delta_t": delta_t,
            "water_balance": fused.get("water_balance"),
            "wind_speed": fused.get("wind_speed"),
        },
        "source_confidence": fused.get("source_confidence", "OPEN-METEO"),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
