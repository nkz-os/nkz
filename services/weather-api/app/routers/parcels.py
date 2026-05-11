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
from app.services.agro_status import calculate_agro_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["parcels"])


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
                    cm.ine_code as municipality_code,
                    cm.name as municipality_name,
                    cm.latitude as station_lat,
                    cm.longitude as station_lon,
                    ST_Distance(
                        cm.geom,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                    ) as distance_m
                FROM catalog_municipalities cm
                WHERE cm.latitude IS NOT NULL
                  AND cm.longitude IS NOT NULL
                  AND cm.geom IS NOT NULL
                ORDER BY cm.geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                LIMIT 1
                """,
                (parcel_lon, parcel_lat, parcel_lon, parcel_lat),
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

    Fuses sensor data (if available) with Open-Meteo data:
    - Priority: Sensor > Open-Meteo
    - Calculates parcel centroid from geometry
    - Returns current conditions and agroclimatic metrics
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
                    LIMIT 1
                    """,
                    (lon, lat, tenant_id, tenant_id, lon, lat),
                )
                sensor_row = cur.fetchone()
                if sensor_row and sensor_row["payload"]:
                    payload = (
                        sensor_row["payload"]
                        if isinstance(sensor_row["payload"], dict)
                        else json.loads(sensor_row["payload"])
                    )
                    sensor_data = {
                        "external_id": sensor_row["external_id"],
                        "name": sensor_row["name"],
                        "distance_m": float(sensor_row["distance_m"])
                        if sensor_row["distance_m"]
                        else None,
                        "observed_at": sensor_row["observed_at"].isoformat()
                        if sensor_row["observed_at"]
                        else None,
                        "payload": payload,
                    }
            finally:
                cur.close()
                conn.close()
        except Exception as e:
            logger.warning(f"Error fetching sensor data: {e}")

        # 4. Calculate agronomic status
        result = calculate_agro_status(
            lat=lat,
            lon=lon,
            parcel_entity=parcel_entity,
            sensor_data=sensor_data,
            openmeteo_api_url=settings.openmeteo_api_url,
        )
        return result

    except requests.exceptions.Timeout:
        return JSONResponse({"error": "Orion-LD request timed out"}, status_code=504)
    except Exception as e:
        logger.error(f"Error in get_parcel_agro_status: {e}", exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)
