"""
GET /api/weather/parcel/{parcel_id} — canonical parcel weather with spatial downscaling.
GET /api/weather/parcel/{parcel_id}/agro-status — agronomic semaphores.
"""

import json
import logging
from typing import Optional

import requests
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg2.extras import RealDictCursor

from app.auth import require_auth, require_auth_optional
from app.config import settings
from app.deps import get_db_connection
from app.services.agro_status import (
    calculate_agro_status,
    _usda_texture_class,
    _extract_float,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["parcels"])


def _cross_validate_sensors(sensors: list) -> dict:
    """Cross-validate multiple sensors, flag outliers >30% from median.

    Returns the primary (nearest reliable) sensor data with validation metadata.
    When one sensor diverges significantly, uses median of remaining sensors.
    """
    if len(sensors) < 2:
        return sensors[0] if sensors else None

    # Numeric metrics to cross-validate
    keys = [
        "temperature",
        "temp",
        "humidity",
        "soil_moisture",
        "moisture",
        "wind_speed",
        "pressure",
    ]

    def _median(values):
        sorted_vals = sorted(v for v in values if v is not None)
        if not sorted_vals:
            return None
        n = len(sorted_vals)
        mid = n // 2
        return (
            sorted_vals[mid] if n % 2 else (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
        )

    # Flag unreliable sensors
    reliable = []
    for s in sensors:
        s["_unreliable"] = False
        reliable.append(s)

    if len(reliable) >= 2:
        for key in keys:
            values = [
                s["payload"].get(key)
                for s in reliable
                if s["payload"].get(key) is not None
            ]
            if len(values) < 2:
                continue
            med = _median(values)
            if med and med > 0:
                for s in reliable:
                    val = s["payload"].get(key)
                    if val is not None and med > 0:
                        deviation = abs(val - med) / med
                        if deviation > 0.3:
                            s["_unreliable"] = True

    # Build fused payload from reliable sensors (median of each metric)
    reliable_only = [s for s in reliable if not s["_unreliable"]]
    if not reliable_only:
        reliable_only = reliable  # fallback: all sensors unreliable, use all

    fused_payload = {}
    for key in keys:
        vals = [
            s["payload"].get(key)
            for s in reliable_only
            if s["payload"].get(key) is not None
        ]
        fused_payload[key] = _median(vals)

    # Use nearest reliable sensor as primary
    primary = reliable_only[0]
    primary["payload"] = fused_payload
    primary["validation"] = {
        "status": "cross_validated",
        "total_sensors": len(sensors),
        "reliable_sensors": len(reliable_only),
        "unreliable_ids": [s["external_id"] for s in sensors if s["_unreliable"]],
    }
    return primary


def _persist_agro_status_to_orion(tenant_id: str, parcel_id: str, result: dict):
    """Write agroStatus semaphores to the AgriParcel entity in Orion-LD.

    Non-blocking best-effort: failures are logged but never propagated.
    """
    try:
        headers = _orion_headers(tenant_id)
        headers["Content-Type"] = "application/json"
        semaphores = result.get("semaphores", {})
        soil = result.get("soil") or {}
        metrics = result.get("metrics", {})

        agro_status_value = {
            "spraying": semaphores.get("spraying", "unknown"),
            "workability": semaphores.get("workability", "unknown"),
            "irrigation": semaphores.get("irrigation", "unknown"),
            "calculatedAt": result.get("timestamp"),
            "sourceConfidence": result.get("source_confidence"),
            "downscalingApplied": result.get("downscaling") == "applied",
        }
        if soil.get("texture_applied"):
            agro_status_value["soilTexture"] = soil.get("texture_class")
            agro_status_value["fieldCapacity"] = soil.get("field_capacity")
            agro_status_value["wiltingPoint"] = soil.get("wilting_point")
        if metrics.get("delta_t") is not None:
            agro_status_value["deltaT"] = metrics["delta_t"]
        if metrics.get("water_balance") is not None:
            agro_status_value["waterBalance"] = metrics["water_balance"]
        if metrics.get("spraying_reason"):
            agro_status_value["sprayingReason"] = metrics["spraying_reason"]

        body = {
            "agroStatus": {
                "type": "Property",
                "value": agro_status_value,
            }
        }

        resp = requests.patch(
            f"{settings.orion_url}/ngsi-ld/v1/entities/{parcel_id}/attrs",
            headers=headers,
            json=body,
            timeout=3,
        )
        if resp.status_code not in (200, 201, 204):
            logger.debug(f"Orion agroStatus persist returned {resp.status_code}")
    except Exception as e:
        logger.debug(f"Could not persist agroStatus to Orion: {e}")


def _orion_headers(tenant_id: str) -> dict:
    h = {"Accept": "application/ld+json"}
    if tenant_id:
        h["Fiware-Service"] = tenant_id
        h["Fiware-ServicePath"] = "/"
        h["NGSILD-Tenant"] = tenant_id
    if settings.context_url:
        h["Link"] = (
            f'<{settings.context_url}>; rel="http://www.w3.org/ns/json-ld#context";'
            f' type="application/ld+json"'
        )
    return h


def _resolve_parcel_location(parcel_entity: dict) -> Optional[tuple]:
    """Extract (longitude, latitude) from a parcel entity's location attribute."""
    location_attr = parcel_entity.get("location", {})
    if isinstance(location_attr, dict):
        loc_value = location_attr.get("value", location_attr)
    else:
        loc_value = location_attr

    if isinstance(loc_value, dict):
        geom_type = loc_value.get("type", "")
        coords = loc_value.get("coordinates", [])
        if geom_type == "Point" and len(coords) >= 2:
            return (float(coords[0]), float(coords[1]))
        if geom_type in ("Polygon", "MultiPolygon") and coords:
            ring = coords[0] if geom_type == "Polygon" else coords[0][0]
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            return (sum(xs) / len(xs), sum(ys) / len(ys))
    return None


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


@router.get("/parcel/{parcel_id}")
def get_parcel_weather(
    parcel_id: str,
    tenant_id: str = Depends(require_auth_optional),
    source: str = Query("OPEN-METEO"),
    data_type: str = Query("HISTORY"),
    limit: int = Query(1, le=72),
):
    """
    Canonical weather endpoint for a specific parcel.

    Resolves the parcel's location from Orion-LD, finds the nearest
    municipality with weather data, applies spatial downscaling (altitude,
    aspect, slope), and returns corrected observations.
    """
    if not tenant_id:
        tenant_id = "default"

    try:
        # Step 1: Resolve parcel from Orion-LD
        headers = _orion_headers(tenant_id)
        parcel_resp = requests.get(
            f"{settings.orion_url}/ngsi-ld/v1/entities/{parcel_id}",
            headers=headers,
            timeout=10,
        )
        if parcel_resp.status_code != 200:
            return JSONResponse(
                {"error": f"Parcel not found: {parcel_resp.status_code}"},
                status_code=404,
            )

        parcel = parcel_resp.json()

        # Step 2: Extract location
        loc = _resolve_parcel_location(parcel)
        if loc is None:
            return JSONResponse(
                {"error": "Parcel has no resolvable location"}, status_code=400
            )
        parcel_lon, parcel_lat = loc

        # Step 3: Extract terrain attributes
        parcel_altitude = 0.0
        elev = parcel.get("elevation", {})
        if isinstance(elev, dict):
            parcel_altitude = float(elev.get("value", 0) or 0)

        parcel_aspect = 0.0
        ta = parcel.get("terrainAspect", {})
        if isinstance(ta, dict):
            parcel_aspect = float(ta.get("value", 0) or 0)

        parcel_slope = 0.0
        ts = parcel.get("terrainSlope", {})
        if isinstance(ts, dict):
            parcel_slope = float(ts.get("value", 0) or 0)

        # Step 4: Try to find linked WeatherObserved entity in Orion-LD
        wo_resp = requests.get(
            f"{settings.orion_url}/ngsi-ld/v1/entities",
            params={
                "type": "WeatherObserved",
                "q": f'locatedAt=="{parcel_id}"',
                "limit": 1,
            },
            headers=headers,
            timeout=10,
        )

        wo_entity = None
        if wo_resp.status_code == 200:
            wo_data = wo_resp.json()
            if isinstance(wo_data, list) and len(wo_data) > 0:
                wo_entity = wo_data[0]
            elif isinstance(wo_data, dict) and wo_data.get("id"):
                wo_entity = wo_data

        if wo_entity:
            # Normalize to same schema as on-the-fly response
            wo_attrs = wo_entity if isinstance(wo_entity, dict) else {}
            temp = wo_attrs.get("temperature", {})
            humidity = wo_attrs.get("relativeHumidity", {})
            wind = wo_attrs.get("windSpeed", {})
            precip = wo_attrs.get("precipitation", {})
            pressure = wo_attrs.get("atmosphericPressure", {})
            et0 = wo_attrs.get("et0", {})
            delta_t = wo_attrs.get("deltaT", {})
            date_obs = wo_attrs.get("dateObserved", {})

            normalized_obs = {
                "observed_at": (
                    date_obs.get("value", {}).get("@value", "")
                    if isinstance(date_obs, dict)
                    else ""
                ),
                "temp_avg": temp.get("value") if isinstance(temp, dict) else None,
                "temp_max": None,
                "temp_min": None,
                "humidity_avg": humidity.get("value") if isinstance(humidity, dict) else None,
                "precip_mm": precip.get("value") if isinstance(precip, dict) else None,
                "wind_speed_ms": wind.get("value") if isinstance(wind, dict) else None,
                "pressure_hpa": pressure.get("value") if isinstance(pressure, dict) else None,
                "eto_mm": et0.get("value") if isinstance(et0, dict) else None,
                "delta_t": delta_t.get("value") if isinstance(delta_t, dict) else None,
                "source": wo_attrs.get("sourceConfidence", {}).get("value", "OPEN-METEO")
                if isinstance(wo_attrs.get("sourceConfidence"), dict)
                else "OPEN-METEO",
                "data_type": "HISTORY",
            }

            return {
                "parcel_id": parcel_id,
                "source": "orion-cache",
                "observations": [normalized_obs],
            }

        # Step 5: Cache miss — fetch from Open-Meteo directly + downscaling
        from datetime import datetime, timedelta

        today = datetime.utcnow().strftime("%Y-%m-%d")
        end = (datetime.utcnow() + timedelta(days=min(limit, 14))).strftime("%Y-%m-%d")

        om_resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": parcel_lat,
                "longitude": parcel_lon,
                "start_date": today,
                "end_date": end,
                "daily": [
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "temperature_2m_mean",
                    "relative_humidity_2m_mean",
                    "precipitation_sum",
                    "precipitation_probability_max",
                    "wind_speed_10m_max",
                    "wind_direction_10m_dominant",
                    "et0_fao_evapotranspiration",
                    "shortwave_radiation_sum",
                    "soil_moisture_0_to_7cm_mean",
                    "soil_moisture_7_to_28cm_mean",
                    "surface_pressure_mean",
                ],
                "timezone": "Europe/Madrid",
            },
            timeout=10,
        )

        if om_resp.status_code != 200:
            return JSONResponse(
                {"error": f"Open-Meteo returned {om_resp.status_code}"},
                status_code=502,
            )

        raw = om_resp.json()
        station_elevation = raw.get("elevation", 0.0)
        daily = raw.get("daily", {})
        dates = daily.get("time", [])

        # Parse Open-Meteo response to observation dicts
        observations = []
        for i, date_str in enumerate(dates):
            obs = {
                "observed_at": date_str,
                "temp_max": _safe_idx(daily, "temperature_2m_max", i),
                "temp_min": _safe_idx(daily, "temperature_2m_min", i),
                "temp_avg": _safe_idx(daily, "temperature_2m_mean", i),
                "humidity_avg": _safe_idx(daily, "relative_humidity_2m_mean", i),
                "precip_mm": _safe_idx(daily, "precipitation_sum", i),
                "precip_probability": _safe_idx(daily, "precipitation_probability_max", i),
                "wind_speed_ms": _safe_idx(daily, "wind_speed_10m_max", i),
                "wind_direction_deg": _safe_idx(daily, "wind_direction_10m_dominant", i),
                "pressure_hpa": _safe_idx(daily, "surface_pressure_mean", i),
                "eto_mm": _safe_idx(daily, "et0_fao_evapotranspiration", i),
                "solar_rad_w_m2": _div_if(
                    _safe_idx(daily, "shortwave_radiation_sum", i), 0.0864
                ),
                "soil_moisture_0_10cm": _safe_idx(
                    daily, "soil_moisture_0_to_7cm_mean", i
                ),
                "soil_moisture_10_40cm": _safe_idx(
                    daily, "soil_moisture_7_to_28cm_mean", i
                ),
            }
            observations.append(obs)

        if not observations:
            return JSONResponse(
                {
                    "parcel_id": parcel_id,
                    "source": "on-the-fly",
                    "downscaling": "unavailable",
                    "observations": [],
                }
            )

        # Step 6: Apply spatial downscaling
        downscaling_applied = False
        try:
            from common.weather_utils.spatial_downscaler import (
                downscale_for_parcel,
            )

            corrected_observations = []
            for obs in observations:
                obs_dt_str = obs.get("observed_at")
                doy = None
                if obs_dt_str and isinstance(obs_dt_str, str):
                    try:
                        doy = datetime.fromisoformat(obs_dt_str).timetuple().tm_yday
                    except (ValueError, TypeError):
                        pass

                corrected = downscale_for_parcel(
                    weather_data=obs,
                    parcel_lat=parcel_lat,
                    parcel_lon=parcel_lon,
                    parcel_altitude_m=parcel_altitude,
                    station_altitude_m=station_elevation,
                    parcel_aspect_deg=parcel_aspect,
                    parcel_slope_deg=parcel_slope,
                    doy=doy,
                )
                corrected["observed_at"] = obs.get("observed_at")
                corrected["source"] = "OPEN-METEO"
                corrected["data_type"] = data_type
                corrected_observations.append(corrected)

            observations = corrected_observations
            downscaling_applied = parcel_altitude > 0 or parcel_slope >= 1.0

        except ImportError:
            logger.debug(
                "Spatial downscaler not available — returning raw observations"
            )
        except Exception as exc:
            logger.warning(f"Downscaling error (returning raw data): {exc}")

        return {
            "parcel_id": parcel_id,
            "source": "on-the-fly",
            "parcel_altitude_m": parcel_altitude,
            "station_altitude_m": station_elevation,
            "parcel_aspect_deg": parcel_aspect,
            "parcel_slope_deg": parcel_slope,
            "downscaling": "applied" if downscaling_applied else "unavailable",
            "observations": observations,
        }

    except requests.exceptions.Timeout:
        return JSONResponse({"error": "Orion-LD request timed out"}, status_code=504)
    except Exception as e:
        logger.error(f"Error in get_parcel_weather: {e}", exc_info=True)
        return JSONResponse(
            {"error": "Failed to fetch parcel weather"}, status_code=500
        )


@router.get("/parcel/{parcel_id}/agro-status")
def get_parcel_agro_status(
    parcel_id: str,
    tenant_id: str = Depends(require_auth),
):
    """
    Get agronomic weather status for a parcel.

    Uses weather-worker data (weather_observations) — no direct Open-Meteo call.
    Fuses sensor data when available within 5km radius.
    Applies spatial downscaling for parcel-specific microclimate.
    """
    try:
        # 1. Get parcel from Orion-LD
        headers = _orion_headers(tenant_id)
        response = requests.get(
            f"{settings.orion_url}/ngsi-ld/v1/entities/{parcel_id}",
            headers=headers,
            timeout=10,
        )
        if response.status_code == 404:
            return JSONResponse({"error": "Parcel not found"}, status_code=404)
        if response.status_code != 200:
            logger.error(f"Error fetching parcel from Orion: {response.status_code}")
            return JSONResponse({"error": "Failed to fetch parcel"}, status_code=500)

        parcel_entity = response.json()

        # 2. Calculate centroid from parcel geometry
        loc = _resolve_parcel_location(parcel_entity)
        if not loc:
            return JSONResponse(
                {
                    "error": "Parcel has no valid location/geometry",
                    "details": "Parcel location could not be determined",
                },
                status_code=400,
            )
        lon, lat = loc

        # 3. Try to get sensor data near the parcel (within 5km radius)
        #    Fetch all nearby sensors for cross-validation
        sensor_data = None
        try:
            conn = get_db_connection(tenant_id)
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute(
                    """
                    SELECT
                        s.external_id,
                        s.name,
                        ST_X(s.installation_location::geometry) as lon,
                        ST_Y(s.installation_location::geometry) as lat,
                        ST_Distance(
                            s.installation_location::geography,
                            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                        ) as distance_m,
                        te.observed_at,
                        te.payload
                    FROM sensors s
                    LEFT JOIN LATERAL (
                        SELECT observed_at, payload
                        FROM telemetry_events
                        WHERE tenant_id = %s
                        AND device_id = s.external_id
                        ORDER BY observed_at DESC
                        LIMIT 1
                    ) te ON true
                    WHERE s.tenant_id = %s
                    AND ST_Distance(
                        s.installation_location::geography,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                    ) <= 5000
                    ORDER BY distance_m ASC
                    """,
                    (lon, lat, tenant_id, tenant_id, lon, lat),
                )
                all_sensors = []
                for row in cur.fetchall():
                    if row["payload"]:
                        payload = (
                            row["payload"]
                            if isinstance(row["payload"], dict)
                            else json.loads(row["payload"])
                        )
                        all_sensors.append(
                            {
                                "external_id": row["external_id"],
                                "name": row["name"],
                                "distance_m": float(row["distance_m"])
                                if row["distance_m"]
                                else None,
                                "observed_at": row["observed_at"].isoformat()
                                if row["observed_at"]
                                else None,
                                "payload": payload,
                            }
                        )

                # Cross-validate: if 2+ sensors, compare and flag outliers (>30% from median)
                if len(all_sensors) >= 2:
                    sensor_data = _cross_validate_sensors(all_sensors)
                elif all_sensors:
                    sensor_data = all_sensors[0]
                    sensor_data["validation"] = {"status": "single"}
                else:
                    sensor_data = None
            finally:
                cur.close()
                conn.close()
        except Exception as e:
            logger.warning(f"Error fetching sensor data: {e}")

        # 4. Extract terrain attributes for spatial downscaling
        parcel_altitude = 0.0
        elev = parcel_entity.get("elevation", {})
        if isinstance(elev, dict):
            parcel_altitude = float(elev.get("value", 0) or 0)

        parcel_aspect = 0.0
        ta = parcel_entity.get("terrainAspect", {})
        if isinstance(ta, dict):
            parcel_aspect = float(ta.get("value", 0) or 0)

        parcel_slope = 0.0
        ts = parcel_entity.get("terrainSlope", {})
        if isinstance(ts, dict):
            parcel_slope = float(ts.get("value", 0) or 0)

        # 4.5. Try to get soil texture from AgriSoil entity linked to this parcel
        soil_texture = None
        try:
            soil_headers = _orion_headers(tenant_id)
            soil_response = requests.get(
                f"{settings.orion_url}/ngsi-ld/v1/entities",
                params={
                    "type": "AgriSoil",
                    "q": f"hasAgriParcel=={parcel_id}",
                    "limit": 1,
                },
                headers=soil_headers,
                timeout=5,
            )
            if soil_response.status_code == 200:
                soil_entities = soil_response.json()
                if isinstance(soil_entities, list) and soil_entities:
                    soil = soil_entities[0]
                    horizons = soil.get("horizons", {}).get("value", [])
                    if isinstance(horizons, list) and horizons:
                        # Use top horizon (0-30cm)
                        h = horizons[0]
                        soil_texture = {
                            "sand": _extract_float(h.get("sand")),
                            "clay": _extract_float(h.get("clay")),
                            "organic_carbon": _extract_float(
                                h.get("organicCarbon"), 0.5
                            ),
                        }
                        # Determine USDA texture class
                        silt = 100.0 - soil_texture["sand"] - soil_texture["clay"]
                        soil_texture["silt"] = max(0.0, silt)
                        soil_texture["texture_class"] = _usda_texture_class(
                            soil_texture["sand"], soil_texture["clay"]
                        )
                        logger.debug(
                            f"Soil texture found for parcel {parcel_id}: {soil_texture['texture_class']}"
                        )
        except Exception as e:
            logger.debug(f"Could not fetch AgriSoil for parcel {parcel_id}: {e}")

        # 5. Query weather_observations: nearest municipality, latest obs, and 3-day history
        weather_observation = {}
        weather_3d = []
        station_altitude = 0.0

        try:
            conn = get_db_connection(tenant_id)
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)

                # 5a. Find nearest municipality with weather data
                cur.execute(
                    """
                    SELECT municipality_code,
                           metadata->>'station_elevation_m' as station_elevation_m
                    FROM weather_observations
                    WHERE tenant_id = %s AND location IS NOT NULL
                    ORDER BY location <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                    LIMIT 1
                    """,
                    (tenant_id, lon, lat),
                )
                nearest = cur.fetchone()

                if nearest:
                    muni_code = nearest["municipality_code"]
                    if nearest.get("station_elevation_m"):
                        station_altitude = float(nearest["station_elevation_m"])

                    # 5b. Latest observation for current conditions
                    cur.execute(
                        """
                        SELECT observed_at, temp_avg, temp_min, temp_max,
                               humidity_avg, precip_mm, precip_probability,
                               wind_speed_ms, wind_gusts_ms, wind_direction_deg,
                               pressure_hpa,
                               solar_rad_w_m2, solar_rad_ghi_w_m2, solar_rad_dni_w_m2,
                               eto_mm, soil_moisture_0_10cm, soil_moisture_10_40cm,
                               gdd_accumulated, delta_t,
                               source, data_type, metadata
                        FROM weather_observations
                        WHERE tenant_id = %s
                          AND municipality_code = %s
                          AND source = 'OPEN-METEO'
                        ORDER BY observed_at DESC
                        LIMIT 1
                        """,
                        (tenant_id, muni_code),
                    )
                    row = cur.fetchone()
                    if row:
                        weather_observation = dict(row)

                    # 5c. Last 3 days for water balance aggregation
                    cur.execute(
                        """
                        SELECT precip_mm, eto_mm, observed_at
                        FROM weather_observations
                        WHERE tenant_id = %s
                          AND municipality_code = %s
                          AND source = 'OPEN-METEO'
                          AND data_type = 'HISTORY'
                          AND observed_at >= NOW() - INTERVAL '3 days'
                        ORDER BY observed_at DESC
                        """,
                        (tenant_id, muni_code),
                    )
                    weather_3d = [dict(r) for r in cur.fetchall()]
            finally:
                cur.close()
                conn.close()
        except Exception as e:
            logger.warning(f"Could not fetch weather observations: {e}")

        # 5d. Fallback: if no PG data, query WeatherObserved directly from Orion-LD
        if not weather_observation:
            try:
                wo_resp = requests.get(
                    f"{settings.orion_url}/ngsi-ld/v1/entities",
                    params={
                        "type": "WeatherObserved",
                        "q": f'locatedAt=="{parcel_id}"',
                        "limit": 1,
                    },
                    headers=headers,
                    timeout=10,
                )
                if wo_resp.status_code == 200:
                    wo_data = wo_resp.json()
                    wo_entities = (
                        wo_data
                        if isinstance(wo_data, list)
                        else [wo_data]
                        if wo_data.get("id")
                        else []
                    )
                    if wo_entities:
                        wo = wo_entities[0]
                        # Normalize NGSI-LD attribute names → internal format
                        def _attr(entity, key, default=None):
                            a = entity.get(key, {})
                            return a.get("value") if isinstance(a, dict) else a or default

                        date_obs = wo.get("dateObserved", {})
                        obs_at = (
                            date_obs.get("value", {}).get("@value", "")
                            if isinstance(date_obs, dict)
                            else ""
                        )
                        weather_observation = {
                            "observed_at": obs_at,
                            "temp_avg": _attr(wo, "temperature"),
                            "temp_min": None,
                            "temp_max": None,
                            "humidity_avg": _attr(wo, "relativeHumidity"),
                            "precip_mm": _attr(wo, "precipitation"),
                            "precip_probability": None,
                            "wind_speed_ms": _attr(wo, "windSpeed"),
                            "wind_gusts_ms": None,
                            "wind_direction_deg": _attr(wo, "windDirection"),
                            "pressure_hpa": _attr(wo, "atmosphericPressure"),
                            "solar_rad_w_m2": None,
                            "solar_rad_ghi_w_m2": None,
                            "solar_rad_dni_w_m2": None,
                            "eto_mm": _attr(wo, "et0"),
                            "soil_moisture_0_10cm": None,
                            "soil_moisture_10_40cm": None,
                            "gdd_accumulated": None,
                            "delta_t": _attr(wo, "deltaT"),
                            "source": _attr(wo, "sourceConfidence", "OPEN-METEO"),
                            "data_type": "HISTORY",
                            "municipality_code": _attr(wo, "municipalityCode"),
                            "station_elevation_m": _attr(wo, "stationElevation"),
                        }
                        station_altitude = float(
                            _attr(wo, "stationElevation") or 0
                        )
                        logger.info(
                            f"Orion WeatherObserved fallback for parcel {parcel_id}"
                        )
            except Exception as e:
                logger.warning(f"Orion WeatherObserved fallback failed: {e}")

        if not weather_observation:
            # Graceful degradation: return parcel metadata + sensor data
            # without weather semaphores, rather than 503.
            parcel_name = "Unnamed"
            name_attr = parcel_entity.get("name", {})
            if isinstance(name_attr, dict):
                parcel_name = name_attr.get("value", "Unnamed")
            return JSONResponse(
                {
                    "parcel_id": parcel_id,
                    "parcel_name": parcel_name,
                    "centroid": {"latitude": lat, "longitude": lon},
                    "weather": None,
                    "semaphores": {
                        "spraying": "no_data",
                        "workability": "no_data",
                        "irrigation": "no_data",
                    },
                    "metrics": None,
                    "soil": None,
                    "crop": None,
                    "source_confidence": None,
                    "downscaling": "unavailable",
                    "no_data_reason": "Weather data has not been ingested yet for this area. The weather-worker processes parcels periodically — data should appear within the next hour.",
                    "timestamp": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                },
            )

        # 6. Calculate agronomic status
        result = calculate_agro_status(
            lat=lat,
            lon=lon,
            parcel_entity=parcel_entity,
            weather_observation=weather_observation,
            weather_3d=weather_3d,
            sensor_data=sensor_data,
            soil_texture=soil_texture,
            parcel_altitude_m=parcel_altitude,
            station_altitude_m=station_altitude,
            parcel_aspect_deg=parcel_aspect,
            parcel_slope_deg=parcel_slope,
        )

        # 7. Persist agroStatus to Orion-LD (non-blocking, best-effort)
        _persist_agro_status_to_orion(tenant_id, parcel_id, result)

        return result

    except requests.exceptions.Timeout:
        return JSONResponse({"error": "Orion-LD request timed out"}, status_code=504)
    except Exception as e:
        logger.error(f"Error in get_parcel_agro_status: {e}", exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/parcel/{parcel_id}/forecast")
def get_parcel_forecast(
    parcel_id: str,
    tenant_id: str = Depends(require_auth),
    days: int = Query(7, le=14, description="Forecast days (max 14)"),
):
    """
    Direct Open-Meteo forecast for a parcel's exact centroid.

    Resolves the AgriParcel from Orion-LD, extracts the centroid from its
    location geometry (Point or Polygon), and fetches a forecast from
    Open-Meteo for that precise point. No DB dependency, no municipality
    catalog — pure coordinates.

    Use this for the dashboard forecast card with parcel dropdown.
    """
    try:
        # 1. Resolve parcel from Orion-LD
        headers = _orion_headers(tenant_id)
        parcel_resp = requests.get(
            f"{settings.orion_url}/ngsi-ld/v1/entities/{parcel_id}",
            headers=headers,
            timeout=10,
        )
        if parcel_resp.status_code != 200:
            return JSONResponse(
                {"error": f"Parcel not found: {parcel_resp.status_code}"},
                status_code=404,
            )

        parcel = parcel_resp.json()

        # 2. Extract centroid from location
        loc = _resolve_parcel_location(parcel)
        if loc is None:
            return JSONResponse(
                {"error": "Parcel has no resolvable location"},
                status_code=400,
            )
        parcel_lon, parcel_lat = loc

        # 3. Get parcel display name
        name_attr = parcel.get("name", {})
        parcel_name = (
            name_attr.get("value", "")
            if isinstance(name_attr, dict)
            else str(name_attr or "")
        )

        # 4. Fetch forecast from Open-Meteo
        from datetime import datetime, timedelta

        today = datetime.utcnow().strftime("%Y-%m-%d")
        end = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")

        params = {
            "latitude": parcel_lat,
            "longitude": parcel_lon,
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

        resp = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=10)
        if resp.status_code != 200:
            return JSONResponse(
                {"error": f"Open-Meteo returned {resp.status_code}"},
                status_code=502,
            )

        raw = resp.json()
        daily = raw.get("daily", {})
        dates = daily.get("time", [])

        # 5. Build forecast response
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
            "parcel_id": parcel_id,
            "parcel_name": parcel_name or parcel_id,
            "coordinates": {"latitude": parcel_lat, "longitude": parcel_lon},
            "elevation_m": raw.get("elevation"),
            "forecast_days": days,
            "forecast": forecast,
            "source": "OPEN-METEO",
        }

    except requests.exceptions.Timeout:
        return JSONResponse(
            {"error": "Open-Meteo request timed out"}, status_code=504
        )
    except Exception as e:
        logger.error(f"Error in get_parcel_forecast: {e}", exc_info=True)
        return JSONResponse(
            {"error": "Failed to fetch parcel forecast"}, status_code=500
        )
