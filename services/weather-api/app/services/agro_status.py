"""
Agronomic status calculation for parcels.

Fuses sensor data with weather observations (from weather-worker) to calculate
spraying, workability, and irrigation semaphores. No direct Open-Meteo dependency
— all weather data comes from the pre-ingested weather_observations table.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Optional

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


def _calc_water_balance(
    precip_3d: Optional[float], et0_3d: Optional[float]
) -> Optional[float]:
    """Calculate 3-day water balance (precipitation - ET0)."""
    if precip_3d is not None and et0_3d is not None:
        return round(precip_3d - et0_3d, 2)
    return None


def _extract_float(value, default=0.0):
    """Safely extract a float from a nested NGSI-LD attribute dict."""
    if isinstance(value, dict):
        v = value.get("value")
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    if value is not None:
        try:
            return float(value)
        except (ValueError, TypeError):
            pass
    return default


def calculate_agro_status(
    lat: float,
    lon: float,
    parcel_entity: dict,
    weather_observation: dict,
    weather_3d: Optional[list] = None,
    sensor_data: Optional[dict] = None,
    parcel_altitude_m: float = 0.0,
    station_altitude_m: float = 0.0,
    parcel_aspect_deg: float = 0.0,
    parcel_slope_deg: float = 0.0,
) -> dict:
    """
    Calculate agronomic status with semaphores for a parcel.

    Uses weather data from the weather-worker (weather_observations table)
    rather than calling Open-Meteo directly — no external API dependency.

    Returns a dict with weather, semaphores, and metrics.
    """
    # 1. Extract current conditions from weather observation
    raw_temperature = weather_observation.get("temp_avg")
    raw_humidity = weather_observation.get("humidity_avg")

    # 1.5 — Apply spatial downscaling for parcel-specific microclimate
    downscaling_applied = False
    if raw_temperature is not None and (
        abs(parcel_altitude_m - station_altitude_m) > 10 or parcel_slope_deg >= 1.0
    ):
        try:
            from common.weather_utils.spatial_downscaler import (
                correct_temperature_altitude,
                recalculate_delta_t,
            )

            if abs(parcel_altitude_m - station_altitude_m) > 10:
                raw_temperature = correct_temperature_altitude(
                    raw_temperature, station_altitude_m, parcel_altitude_m
                )
                downscaling_applied = True

            if raw_humidity is not None:
                _ = recalculate_delta_t(raw_temperature, raw_humidity)

        except ImportError:
            logger.debug("Spatial downscaler not available for agro-status")
        except Exception as exc:
            logger.warning(f"Agro-status downscaling error: {exc}")

    # 2. Aggregate 3-day precipitation and ET0 from history
    precip_3d = 0.0
    et0_3d = 0.0
    if weather_3d:
        for obs in weather_3d:
            precip_3d += obs.get("precip_mm") or 0
            et0_val = obs.get("eto_mm")
            if et0_val is not None:
                et0_3d += et0_val

    weather_data = {
        "temperature": raw_temperature,
        "humidity": raw_humidity,
        "wind_speed": weather_observation.get("wind_speed_ms"),
        "wind_direction": weather_observation.get("wind_direction_deg"),
        "pressure": weather_observation.get("pressure_hpa"),
        "precipitation": weather_observation.get("precip_mm") or 0,
        "eto_today": weather_observation.get("eto_mm"),
        "precipitation_3d": round(precip_3d, 2),
        "eto_3d": round(et0_3d, 2) if et0_3d else None,
        "wind_gusts": weather_observation.get("wind_gusts_ms"),
        "observed_at": weather_observation.get(
            "observed_at", datetime.now(timezone.utc)
        ),
    }

    # 3. Fuse sensor and weather data (Sensor > weather observation)
    fused = {
        "temperature": weather_data.get("temperature"),
        "humidity": weather_data.get("humidity"),
        "wind_speed": weather_data.get("wind_speed"),
        "wind_direction": weather_data.get("wind_direction"),
        "pressure": weather_data.get("pressure"),
        "precipitation": weather_data.get("precipitation", 0),
        "precipitation_3d": weather_data.get("precipitation_3d", 0),
        "eto_today": weather_data.get("eto_today"),
        "eto_3d": weather_data.get("eto_3d"),
        "wind_gusts": weather_data.get("wind_gusts"),
        "sources": {
            "temperature": "WEATHER-OBS",
            "humidity": "WEATHER-OBS",
            "wind_speed": "WEATHER-OBS",
            "wind_direction": "WEATHER-OBS",
            "pressure": "WEATHER-OBS",
            "precipitation": "WEATHER-OBS",
            "wind_gusts": "WEATHER-OBS",
        },
        "source_confidence": "WEATHER-OBS",
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

    # 4. Calculate water balance
    fused["water_balance"] = _calc_water_balance(
        fused.get("precipitation_3d"), fused.get("eto_3d")
    )

    # 5. Calculate Delta-T
    delta_t = None
    if fused.get("temperature") is not None and fused.get("humidity") is not None:
        delta_t = _calc_delta_t(fused["temperature"], fused["humidity"])

    # 6. Semaphores
    semaphores = {
        "spraying": "unknown",
        "workability": "unknown",
        "irrigation": "unknown",
    }

    wind_speed_ms = fused.get("wind_speed") or 0
    wind_speed_kmh = wind_speed_ms * 3.6
    wind_gusts_ms = fused.get("wind_gusts") or 0
    wind_gusts_kmh = wind_gusts_ms * 3.6
    precip = fused.get("precipitation") or 0

    # 6a. Spraying semaphore
    if delta_t is not None and wind_speed_kmh is not None:
        if wind_gusts_kmh > 25:
            semaphores["spraying"] = "not_suitable"
        elif wind_speed_kmh < 15 and 2 <= delta_t <= 8:
            semaphores["spraying"] = "optimal"
        elif wind_speed_kmh > 20 or delta_t > 10 or (precip and precip > 0.5):
            semaphores["spraying"] = "not_suitable"
        else:
            semaphores["spraying"] = "caution"

    # 6b. Workability semaphore
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

    # 6c. Irrigation semaphore
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
            "wind_gusts": fused.get("wind_gusts"),
        },
        "source_confidence": fused.get("source_confidence", "WEATHER-OBS"),
        "downscaling": "applied" if downscaling_applied else "unavailable",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
