"""
Agronomic status calculation for parcels.

Fuses sensor data with weather observations (from weather-worker) to calculate
spraying, workability, and irrigation semaphores. No direct Open-Meteo dependency
— all weather data comes from the pre-ingested weather_observations table.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _calc_delta_t(temp_celsius: float, humidity_percent: float) -> Optional[float]:
    """Calculate Delta-T (wet-bulb depression) — delegates to unified psychrometrics."""
    try:
        from common.weather_utils.psychrometrics import calculate_delta_t

        return calculate_delta_t(temp_celsius, humidity_percent) or None
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


def _saxton_rawls_2006(
    sand_pct: float, clay_pct: float, organic_carbon_pct: float
) -> dict:
    """Saxton & Rawls (2006) pedotransfer functions.

    Computes field capacity, wilting point, and saturated hydraulic conductivity
    from soil texture (sand %, clay %, organic carbon %).

    Returns dict with ksat (mm/h), field_capacity (cm3/cm3), wilting_point (cm3/cm3).
    """
    s = sand_pct / 100.0
    c = clay_pct / 100.0
    om = (organic_carbon_pct * 1.724) / 100.0

    # Wilting point (-1500 kPa)
    theta_1500t = (
        -0.024 * s
        + 0.487 * c
        + 0.006 * om
        + 0.005 * s * om
        - 0.013 * c * om
        + 0.068 * s * c
        + 0.031
    )
    theta_1500 = theta_1500t + 0.14 * theta_1500t - 0.02

    # Field capacity (-33 kPa)
    theta_33t = (
        -0.251 * s
        + 0.195 * c
        + 0.011 * om
        + 0.006 * s * om
        - 0.027 * c * om
        + 0.452 * s * c
        + 0.299
    )
    theta_33 = theta_33t + 1.283 * theta_33t**2 - 0.374 * theta_33t - 0.015

    # Saturated -33 kPa (for Ksat computation)
    theta_s33t = (
        0.278 * s
        + 0.034 * c
        + 0.022 * om
        - 0.018 * s * om
        - 0.027 * c * om
        - 0.584 * s * c
        + 0.078
    )
    theta_s33 = theta_s33t + 0.636 * theta_s33t - 0.107

    lam = max(theta_33 - theta_1500, 0.001)
    diff = max(theta_s33 - theta_33, 0.001)
    ksat = 1930.0 * diff ** (3.0 - lam)

    return {
        "ksat": round(ksat, 2),
        "field_capacity": round(theta_33, 3),
        "wilting_point": round(theta_1500, 3),
    }


def _usda_texture_class(sand: float, clay: float) -> str:
    """USDA soil texture classification from sand and clay percentages."""
    silt = 100.0 - sand - clay
    if silt + 1.5 * clay < 15:
        return "sand"
    if silt + 1.5 * clay >= 15 and silt + 2 * clay < 30:
        return "loamy_sand"
    if clay >= 7 and clay <= 20 and sand > 52 and silt + 2 * clay >= 30:
        return "sandy_loam"
    if clay >= 7 and clay <= 27 and silt >= 28 and silt <= 50 and sand <= 52:
        return "loam"
    if silt >= 50 and clay >= 12 and clay <= 27:
        return "silt_loam"
    if silt >= 80 and clay < 12:
        return "silt"
    if clay >= 20 and clay <= 35 and silt < 28 and sand > 45:
        return "sandy_clay_loam"
    if clay >= 27 and clay <= 40 and sand >= 20 and sand <= 45:
        return "clay_loam"
    if clay >= 27 and clay <= 40 and silt >= 40:
        return "silty_clay_loam"
    if clay >= 35 and sand > 45:
        return "sandy_clay"
    if clay >= 40 and silt >= 40:
        return "silty_clay"
    if clay >= 40 and sand <= 45 and silt < 40:
        return "clay"
    return "loam"


def _scs_hydrologic_group(ksat: float) -> str:
    """Classify soil into SCS hydrologic group based on saturated conductivity."""
    if ksat > 36:
        return "A"
    if ksat > 3.6:
        return "B"
    if ksat > 0.36:
        return "C"
    return "D"


def _estimate_recovery_hours(hydrologic_group: str, precip_3d: float) -> int:
    """Estimate hours until soil is trafficable after rain, by hydrologic group."""
    base = {"A": 6, "B": 18, "C": 36, "D": 60}
    hours = base.get(hydrologic_group, 24)
    # Additional delay per mm of recent rain (capped)
    extra = min(precip_3d * 0.5, 48)
    return int(hours + extra)


def _extract_crop_stage(parcel_entity: dict) -> Optional[str]:
    """Extract crop growth stage from AgriParcel entity, normalized to lowercase."""
    cs = parcel_entity.get("cropStatus", {})
    if isinstance(cs, dict):
        val = cs.get("value", "")
        if val:
            return str(val).lower().strip()
    return None


def _crop_spraying_sensitivity(stage: Optional[str]) -> str:
    """Return spraying sensitivity level based on crop phenological stage.

    flowering/fruiting = high sensitivity (avoid spraying)
    vegetative/tillering = normal sensitivity
    """
    if not stage:
        return "normal"
    high_sensitivity = (
        "flowering",
        "bloom",
        "floracion",
        "fruit",
        "fruiting",
        "fructificacion",
        "heading",
        "espigado",
    )
    for kw in high_sensitivity:
        if kw in stage:
            return "high"
    return "normal"


def _texture_workability(
    soil_moisture: Optional[float],
    field_capacity: float,
    wilting_point: float,
    recent_precip: float = 0.0,
    humidity: float = 0.0,
) -> str:
    """Compute workability semaphore using texture-aware thresholds.

    When soil_moisture is available (sensor), thresholds are relative to FC and PWP.
    Falls back to precipitation/humidity heuristic when no sensor data.
    """
    margin = 0.05  # 5% volumetric buffer from FC and PWP

    if soil_moisture is not None:
        too_wet = field_capacity - margin
        too_dry = wilting_point + margin

        if soil_moisture > too_wet:
            return "too_wet"
        if soil_moisture < too_dry:
            return "too_dry"
        if too_dry <= soil_moisture <= too_wet:
            return "optimal"
        return "caution"

    # No sensor — fallback to precipitation/humidity heuristic
    if recent_precip > 5 or humidity > 80:
        return "too_wet"
    if recent_precip == 0 and humidity < 40:
        return "too_dry"
    if 1 <= recent_precip <= 5 and 40 <= humidity <= 80:
        return "optimal"
    return "caution"


def calculate_agro_status(
    lat: float,
    lon: float,
    parcel_entity: dict,
    weather_observation: dict,
    weather_3d: Optional[list] = None,
    sensor_data: Optional[dict] = None,
    soil_texture: Optional[dict] = None,
    parcel_altitude_m: float = 0.0,
    station_altitude_m: float = 0.0,
    parcel_aspect_deg: float = 0.0,
    parcel_slope_deg: float = 0.0,
    nearby_stations: Optional[list] = None,
) -> dict:
    """
    Calculate agronomic status with semaphores for a parcel.

    Uses weather data from the weather-worker (weather_observations table)
    rather than calling Open-Meteo directly — no external API dependency.

    When soil_texture is provided (sand, clay, organic_carbon from AgriSoil),
    workability thresholds are texture-aware via Saxton-Rawls 2006 PTF.
    Falls back to generic thresholds otherwise.

    Returns a dict with weather, semaphores, and metrics.
    """
    # 1. Extract current conditions from weather observation
    raw_temperature = weather_observation.get("temp_avg")
    raw_humidity = weather_observation.get("humidity_avg")

    # 1.5 — Apply spatial downscaling for parcel-specific microclimate
    # Uses the unified downscale_for_parcel() from common module — same as
    # GET /parcel/{id} endpoint, ensuring consistent temperature/radiation/delta-T
    # across all per-parcel weather endpoints.
    downscaling_applied = False
    need_downscaling = (
        abs(parcel_altitude_m - station_altitude_m) > 10 or parcel_slope_deg >= 1.0
    )
    if need_downscaling or nearby_stations:
        try:
            from common.weather_utils.spatial_downscaler import downscale_for_parcel

            obs_dt = weather_observation.get("observed_at")
            doy = obs_dt.timetuple().tm_yday if hasattr(obs_dt, "timetuple") else None

            corrected = downscale_for_parcel(
                weather_data=weather_observation,
                parcel_lat=lat,
                parcel_lon=lon,
                parcel_altitude_m=parcel_altitude_m,
                station_altitude_m=station_altitude_m,
                parcel_aspect_deg=parcel_aspect_deg,
                parcel_slope_deg=parcel_slope_deg,
                doy=doy,
                nearby_stations=nearby_stations,
            )
            # Apply corrected values back to the extracted variables
            raw_temperature = corrected.get("temp_avg", raw_temperature)
            raw_humidity = corrected.get("humidity_avg", raw_humidity)
            # Also update the weather_observation dict so downstream uses corrected values
            weather_observation.update(
                {k: v for k, v in corrected.items() if k in weather_observation}
            )
            downscaling_applied = True

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
    precip_prob = weather_observation.get("precip_probability")
    spraying_reason = None

    # Phenological stage awareness
    crop_stage = _extract_crop_stage(parcel_entity)
    crop_sensitivity = _crop_spraying_sensitivity(crop_stage)

    # Temperature inversion detection (day/night ΔT > 15°C + low wind)
    temp_min_24h = weather_observation.get("temp_min")
    temp_current = fused.get("temperature")
    inversion_risk = False
    if temp_min_24h is not None and temp_current is not None:
        delta_t_daynight = temp_current - temp_min_24h
        if delta_t_daynight > 15 and wind_speed_kmh < 5:
            inversion_risk = True

    if delta_t is not None and wind_speed_kmh is not None:
        if wind_gusts_kmh > 25:
            semaphores["spraying"] = "not_suitable"
            spraying_reason = "wind_gusts"
        elif inversion_risk:
            semaphores["spraying"] = "not_suitable"
            spraying_reason = "inversion_risk"
        elif wind_speed_kmh < 15 and 2 <= delta_t <= 8:
            semaphores["spraying"] = "optimal"
        elif wind_speed_kmh > 20 or delta_t > 10 or (precip and precip > 0.5):
            semaphores["spraying"] = "not_suitable"
            spraying_reason = (
                "wind_speed"
                if wind_speed_kmh > 20
                else "delta_t"
                if delta_t > 10
                else "precipitation"
            )
        else:
            semaphores["spraying"] = "caution"

        # Degrade spraying based on rain risk and crop sensitivity
        if (
            semaphores["spraying"] == "optimal"
            and precip_prob is not None
            and precip_prob > 50
        ):
            semaphores["spraying"] = "caution"
            spraying_reason = "rain_risk"

        if semaphores["spraying"] == "optimal" and crop_sensitivity == "high":
            semaphores["spraying"] = "caution"
            spraying_reason = "crop_sensitive"

    # 6b. Workability semaphore — texture-aware when soil data available
    soil_moisture = None
    if sensor_data and sensor_data.get("payload"):
        p = sensor_data["payload"]
        soil_moisture = p.get("soil_moisture") or p.get("moisture")

    recent_precip = fused.get("precipitation_3d", 0)
    humidity = fused.get("humidity") or 0

    # Compute texture-aware thresholds if soil data available
    fc = None  # field capacity
    wp = None  # wilting point
    ksat = None  # saturated hydraulic conductivity
    texture_applied = False

    if soil_texture and soil_texture.get("sand") and soil_texture.get("clay"):
        try:
            oc = soil_texture.get("organic_carbon", 0.5) or 0.5
            ptf = _saxton_rawls_2006(
                float(soil_texture["sand"]),
                float(soil_texture["clay"]),
                float(oc),
            )
            fc = ptf["field_capacity"]
            wp = ptf["wilting_point"]
            ksat = ptf["ksat"]
            texture_applied = True
        except Exception as exc:
            logger.warning(f"Saxton-Rawls PTF failed, using generic thresholds: {exc}")

    if texture_applied and fc is not None and wp is not None:
        semaphores["workability"] = _texture_workability(
            soil_moisture, fc, wp, recent_precip, humidity
        )
    else:
        # Generic fallback thresholds
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

    # 7. Post-rain workability recovery prediction
    recovery_hours = None
    hydrologic_group = None
    if texture_applied and ksat is not None:
        hydrologic_group = _scs_hydrologic_group(ksat)
        if semaphores["workability"] == "too_wet" and recent_precip > 0:
            recovery_hours = _estimate_recovery_hours(hydrologic_group, recent_precip)

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
            "precip_probability": precip_prob,
            "spraying_reason": spraying_reason,
        },
        "soil": {
            "texture_applied": texture_applied,
            "field_capacity": fc,
            "wilting_point": wp,
            "ksat": ksat,
            "hydrologic_group": hydrologic_group,
            "recovery_hours": recovery_hours,
            "texture_class": soil_texture.get("texture_class")
            if soil_texture
            else None,
        }
        if soil_texture
        else None,
        "crop": {
            "stage": crop_stage,
            "spraying_sensitivity": crop_sensitivity,
        }
        if crop_stage
        else None,
        "inversion_risk": inversion_risk,
        "source_confidence": fused.get("source_confidence", "WEATHER-OBS"),
        "downscaling": "applied" if downscaling_applied else "unavailable",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
