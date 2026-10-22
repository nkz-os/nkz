"""
Agronomic status calculation for parcels.

Fuses sensor data with weather observations (from weather-worker) to calculate
spraying, workability, and irrigation semaphores. No direct Open-Meteo dependency
— all weather data comes from the parcel's own WeatherObserved series
(telemetry_events), pre-ingested by weather-worker.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _calc_delta_t(temp_celsius: float, humidity_percent: float) -> Optional[float]:
    """Calculate Delta-T (wet-bulb depression) — delegates to unified psychrometrics."""
    try:
        from weather_utils.psychrometrics import calculate_delta_t

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


def _validated_water_content(value, name: str) -> float:
    """Return `value` as a volumetric water content, or raise.

    Volumetric water content is a fraction of soil volume: it lives in (0, 1).
    Anything else is a unit error or a nodata sentinel that survived ingestion,
    and both look like ordinary floats until something divides by them.
    """
    import math

    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} is not a number: {value!r}")
    if not math.isfinite(v) or not 0.0 < v < 1.0:
        raise ValueError(f"{name} outside (0, 1) cm3/cm3: {v}")
    return v


def _extract_soil_moisture(payload: dict) -> Optional[float]:
    """Pull volumetric soil moisture (cm3/cm3) out of a telemetry payload.

    Real payloads nest their readings: {"measurements": {"soilMoistureTop": 0.099}}.
    This only ever looked at the payload root, so it returned None on all 29849
    telemetry rows in production and the texture-aware workability branch was
    dead code. Measured 2026-09-16: `measurements.soilMoistureTop` present in
    25498 rows, `soilMoistureVwc` in 1147, values 0.067-0.16 (unitCode M3).

    Root-level keys are still honoured so callers that flatten keep working.
    """
    if not isinstance(payload, dict):
        return None
    raw = None
    measurements = payload.get("measurements")
    if isinstance(measurements, dict):
        for key in ("soilMoistureTop", "soilMoistureVwc", "soilMoisture"):
            raw = _extract_float(measurements.get(key))
            if raw is not None:
                break
    if raw is None:
        raw = _extract_float(payload.get("soil_moisture"))
    if raw is None:
        raw = _extract_float(payload.get("moisture"))
    return _as_volumetric_fraction(raw)


def _as_volumetric_fraction(value: Optional[float]) -> Optional[float]:
    """Normalise a soil water content to cm3/cm3, or None if it is not one.

    The two scales in use are distinguishable without guessing: water content is
    a fraction of soil volume, so it cannot exceed 1. Anything in (1, 100] is a
    percentage and is divided; anything above 100, negative, or non-finite is not
    a water content at all and is dropped rather than fed to the semaphore.
    """
    import math

    if value is None or not math.isfinite(value) or value < 0.0:
        return None
    if value <= 1.0:
        return value
    if value <= 100.0:
        return value / 100.0
    return None


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


def _water_content_or_none(value) -> Optional[float]:
    try:
        return _validated_water_content(value, "water_content")
    except ValueError:
        return None


def _tillage_limits(wet_limit, dry_limit) -> Optional[tuple[float, float]]:
    """(wet, dry) when both are water contents and dry < wet, else None."""
    if wet_limit is None or dry_limit is None:
        return None
    try:
        wet = _validated_water_content(wet_limit, "wet_tillage_limit")
        dry = _validated_water_content(dry_limit, "dry_tillage_limit")
    except ValueError:
        return None
    return (wet, dry) if dry < wet else None


def classify_workability(
    soil_moisture: Optional[float],
    wet_limit: Optional[float],
    dry_limit: Optional[float],
) -> tuple[str, Optional[str]]:
    """Workability (tempero) state and, when unknown, why.

    The limits are not computed here: the soil module publishes them per horizon
    (`wetTillageLimit`, `dryTillageLimit`; method and sources in its SDM notes).
    This only compares the soil moisture against them, all in m3/m3.
    """
    if soil_moisture is None:
        return "unknown", "no_soil_moisture"
    if wet_limit is None or dry_limit is None:
        return "unknown", "soil_thresholds_missing"
    limits = _tillage_limits(wet_limit, dry_limit)
    if limits is None:
        return "unknown", "invalid_thresholds"
    wet, dry = limits
    if soil_moisture > wet:
        return "too_wet", None
    if soil_moisture < dry:
        return "too_dry", None
    return "optimal", None

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

    Uses weather data from the weather-worker (WeatherObserved telemetry)
    rather than calling Open-Meteo directly — no external API dependency.

    Workability compares soil moisture with the tillage limits of the top soil
    horizon (`soil_texture["wet_tillage_limit"]` / `["dry_tillage_limit"]`,
    published by the soil module). Without them the semaphore is `unknown`.

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
            from weather_utils.spatial_downscaler import downscale_for_parcel

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
    # None when no day carries the value: an absent record is not 0 mm.
    precip_3d = None
    et0_3d = None
    for obs in weather_3d or []:
        if obs.get("precip_mm") is not None:
            precip_3d = (precip_3d or 0.0) + obs["precip_mm"]
        if obs.get("eto_mm") is not None:
            et0_3d = (et0_3d or 0.0) + obs["eto_mm"]

    weather_data = {
        "temperature": raw_temperature,
        "humidity": raw_humidity,
        "wind_speed": weather_observation.get("wind_speed_ms"),
        "wind_direction": weather_observation.get("wind_direction_deg"),
        "pressure": weather_observation.get("pressure_hpa"),
        "precipitation": weather_observation.get("precip_mm") or 0,
        "eto_today": weather_observation.get("eto_mm"),
        "precipitation_3d": round(precip_3d, 2) if precip_3d is not None else None,
        "eto_3d": round(et0_3d, 2) if et0_3d is not None else None,
        "wind_gusts": weather_observation.get("wind_gusts_ms"),
        # Time of the reading itself; never "now" when it is unknown.
        "observed_at": weather_observation.get("observed_at"),
    }

    # 3. Fuse sensor and weather data (Sensor > weather observation)
    fused = {
        "temperature": weather_data.get("temperature"),
        "humidity": weather_data.get("humidity"),
        "wind_speed": weather_data.get("wind_speed"),
        "wind_direction": weather_data.get("wind_direction"),
        "pressure": weather_data.get("pressure"),
        "precipitation": weather_data.get("precipitation", 0),
        "precipitation_3d": weather_data.get("precipitation_3d"),
        "eto_today": weather_data.get("eto_today"),
        "eto_3d": weather_data.get("eto_3d"),
        "wind_gusts": weather_data.get("wind_gusts"),
        "observed_at": weather_data.get("observed_at"),
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

    # Weather-map per-parcel raster (parcel_weather tier) overrides the regional
    # WEATHER-OBS values for the fields the raster provides (temperature, ET0,
    # soil moisture) — recorded in `weather_observation._weather_map_fields` by
    # the router.
    wm_fields = set(weather_observation.get("_weather_map_fields") or [])
    if "temp_avg" in wm_fields:
        fused["sources"]["temperature"] = "WEATHER-MAP"

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

    # 6b. Workability (tempero) semaphore: this parcel's soil moisture against the
    # limits the soil module publishes for its top horizon.
    soil_moisture = None
    soil_moisture_provenance = None
    if sensor_data and sensor_data.get("payload"):
        soil_moisture = _extract_soil_moisture(sensor_data["payload"])
        if soil_moisture is not None:
            soil_moisture_provenance = "iot_sensor"
    if soil_moisture is None:
        # Fallback: modelled moisture from the per-parcel virtual station
        # (Open-Meteo ERA5), normalised into weather_observation by the router
        # (percent scale via _soil_percent there). _as_volumetric_fraction
        # resolves percent and fraction alike, so both router paths are safe.
        # NOTE: _extract_float defaults to 0.0 — a missing key must stay None,
        # not read as bone-dry soil (default=None makes absence explicit).
        soil_moisture = _as_volumetric_fraction(
            _extract_float(
                weather_observation.get("soil_moisture_0_10cm"), None
            )
        )
        if soil_moisture is not None:
            soil_moisture_provenance = "parcel_weather"

    recent_precip = fused.get("precipitation_3d")

    soil = soil_texture or {}
    wet_limit = soil.get("wet_tillage_limit")
    dry_limit = soil.get("dry_tillage_limit")
    semaphores["workability"], workability_reason = classify_workability(
        soil_moisture, wet_limit, dry_limit
    )
    limits_applied = _tillage_limits(wet_limit, dry_limit) is not None
    # A sensor measures this parcel's soil; the virtual station's moisture comes
    # from a reanalysis with its own generic soil, so against this parcel's limits
    # it is only a regional estimate.
    workability_source = {
        "iot_sensor": "iot_sensor",
        "parcel_weather": "regional_estimate",
    }.get(soil_moisture_provenance)

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
    hydrologic_group = soil.get("hydrologic_group")
    recovery_hours = None
    if (
        hydrologic_group
        and semaphores["workability"] == "too_wet"
        and (recent_precip or 0) > 0
    ):
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
            "soil_moisture": soil_moisture,
            # The host panel reads `metrics.moisture` as a PERCENT (it appends
            # `%`), while `soil_moisture` is the analytical 0-1 fraction. Keep
            # both: fraction for models, percent for display.
            "moisture": (soil_moisture * 100.0) if soil_moisture is not None else None,
            "soil_moisture_provenance": soil_moisture_provenance,
            "workability_source": workability_source,
            "workability_reason": workability_reason,
            "wet_tillage_limit": wet_limit if limits_applied else None,
            "dry_tillage_limit": dry_limit if limits_applied else None,
        },
        "soil": {
            "texture_applied": limits_applied,
            "field_capacity": _water_content_or_none(soil.get("field_capacity")),
            "wilting_point": _water_content_or_none(soil.get("wilting_point")),
            "ksat": soil.get("ksat"),
            "hydrologic_group": hydrologic_group,
            "recovery_hours": recovery_hours,
            "texture_class": soil.get("texture_class"),
            "source": soil.get("source"),
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
