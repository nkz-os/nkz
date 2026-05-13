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
from psycopg2.extras import RealDictCursor, Json

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


def _persist_agro_status_to_db(
    tenant_id: str, parcel_id: str, result: dict, sensor_data: Optional[dict]
):
    """Write agro-status calculation to agro_status_log for historical queries."""
    try:
        conn = get_db_connection(tenant_id)
        try:
            cur = conn.cursor()
            semaphores = result.get("semaphores", {})
            metrics = result.get("metrics", {})
            soil = result.get("soil") or {}

            cur.execute(
                """
                INSERT INTO agro_status_log (
                    tenant_id, parcel_id, calculated_at,
                    spraying, workability, irrigation,
                    source_confidence, soil_texture,
                    field_capacity, wilting_point,
                    delta_t, water_balance,
                    downscaling_applied, sensor_count, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    parcel_id,
                    result.get("timestamp"),
                    semaphores.get("spraying"),
                    semaphores.get("workability"),
                    semaphores.get("irrigation"),
                    result.get("source_confidence"),
                    soil.get("texture_class"),
                    soil.get("field_capacity"),
                    soil.get("wilting_point"),
                    metrics.get("delta_t"),
                    metrics.get("water_balance"),
                    result.get("downscaling") == "applied",
                    sensor_data.get("validation", {}).get("total_sensors", 1)
                    if sensor_data
                    else 0,
                    Json(
                        {
                            "sensor_validation": sensor_data.get("validation")
                            if sensor_data
                            else None,
                            "spraying_reason": metrics.get("spraying_reason"),
                        }
                    ),
                ),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.debug(f"Could not persist agroStatus to DB: {e}")


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

        # Step 4: Find nearest municipality with weather data
        conn = get_db_connection(tenant_id)
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                SELECT
                    wo.municipality_code,
                    cm.name as municipality_name,
                    ST_Y(wo.location) as station_lat,
                    ST_X(wo.location) as station_lon,
                    ST_Distance(
                        wo.location,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                    ) as distance_m
                FROM weather_observations wo
                LEFT JOIN catalog_municipalities cm ON cm.ine_code = wo.municipality_code
                WHERE wo.tenant_id = %s
                  AND wo.location IS NOT NULL
                ORDER BY wo.location <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                LIMIT 1
                """,
                (parcel_lon, parcel_lat, tenant_id, parcel_lon, parcel_lat),
            )
            municipality = cur.fetchone()
            if not municipality:
                return JSONResponse(
                    {"error": "No municipality with weather data found near parcel"},
                    status_code=404,
                )

            muni_code = municipality["municipality_code"]

            # Step 5: Get weather observations for this municipality
            cur.execute(
                """
                SELECT
                    observed_at, temp_avg, temp_min, temp_max,
                    humidity_avg, precip_mm,
                    solar_rad_w_m2, solar_rad_ghi_w_m2, solar_rad_dni_w_m2,
                    eto_mm, soil_moisture_0_10cm, soil_moisture_10_40cm,
                    wind_speed_ms, wind_direction_deg, pressure_hpa,
                    gdd_accumulated, delta_t,
                    source, data_type, metadata
                FROM weather_observations
                WHERE tenant_id = %s
                  AND municipality_code = %s
                  AND source = %s
                  AND data_type = %s
                ORDER BY observed_at DESC
                LIMIT %s
                """,
                (tenant_id, muni_code, source, data_type, limit),
            )
            observations = [dict(row) for row in cur.fetchall()]
        finally:
            cur.close()
            conn.close()

        if not observations:
            return JSONResponse(
                {
                    "parcel_id": parcel_id,
                    "municipality_code": muni_code,
                    "municipality_name": municipality.get("municipality_name"),
                    "downscaling": "unavailable",
                    "observations": [],
                }
            )

        # Step 6: Extract station elevation from observation metadata
        station_altitude = 0.0
        meta = observations[0].get("metadata") or {}
        if isinstance(meta, dict):
            station_altitude = float(meta.get("station_elevation_m", 0) or 0)

        # Step 7: Apply spatial downscaling
        downscaling_applied = False
        try:
            from common.weather_utils.spatial_downscaler import (
                downscale_for_parcel,
            )

            corrected_observations = []
            for obs in observations:
                obs_dt = obs.get("observed_at")
                doy = (
                    obs_dt.timetuple().tm_yday if hasattr(obs_dt, "timetuple") else None
                )

                corrected = downscale_for_parcel(
                    weather_data=obs,
                    parcel_lat=parcel_lat,
                    parcel_lon=parcel_lon,
                    parcel_altitude_m=parcel_altitude,
                    station_altitude_m=station_altitude,
                    parcel_aspect_deg=parcel_aspect,
                    parcel_slope_deg=parcel_slope,
                    doy=doy,
                )
                for key in ("observed_at", "source", "data_type", "municipality_code"):
                    if key in obs:
                        corrected[key] = obs[key]
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
            "municipality_code": muni_code,
            "municipality_name": municipality.get("municipality_name"),
            "parcel_altitude_m": parcel_altitude,
            "station_altitude_m": station_altitude,
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
                    "q": f"refAgriParcel=={parcel_id}",
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

        if not weather_observation:
            return JSONResponse(
                {
                    "error": "No weather data available for this location",
                    "details": "Weather data has not been ingested yet for this area",
                },
                status_code=503,
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

        # Persist agroStatus to Orion-LD and PostgreSQL (non-blocking, best-effort)
        _persist_agro_status_to_orion(tenant_id, parcel_id, result)
        _persist_agro_status_to_db(tenant_id, parcel_id, result, sensor_data)

        return result

    except requests.exceptions.Timeout:
        return JSONResponse({"error": "Orion-LD request timed out"}, status_code=504)
    except Exception as e:
        logger.error(f"Error in get_parcel_agro_status: {e}", exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/parcel/{parcel_id}/agro-status/history")
def get_parcel_agro_status_history(
    parcel_id: str,
    tenant_id: str = Depends(require_auth),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    limit: int = Query(30, le=365),
):
    """
    Get historical agro-status semaphores for a parcel.

    Returns time-series of spraying/workability/irrigation semaphores
    for trend analysis and decision audit.
    """
    try:
        conn = get_db_connection(tenant_id)
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)

            query = """
                SELECT calculated_at, spraying, workability, irrigation,
                       source_confidence, soil_texture,
                       field_capacity, wilting_point,
                       delta_t, water_balance,
                       downscaling_applied, sensor_count, metadata
                FROM agro_status_log
                WHERE tenant_id = %s AND parcel_id = %s
            """
            params = [tenant_id, parcel_id]

            if from_date:
                query += " AND calculated_at >= %s"
                params.append(from_date)
            if to_date:
                query += " AND calculated_at <= %s"
                params.append(to_date)

            query += " ORDER BY calculated_at DESC LIMIT %s"
            params.append(limit)

            cur.execute(query, params)
            rows = [dict(r) for r in cur.fetchall()]

            return {
                "parcel_id": parcel_id,
                "history": rows,
                "count": len(rows),
            }
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"Error fetching agro-status history: {e}", exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)
