"""
GET /api/weather/locations — list tenant weather locations.
POST /api/weather/locations — create weather location.
GET /api/weather/municipality/near — nearest municipality to coordinates.
"""

import json
import logging
from typing import Optional

import requests
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from psycopg2.extras import RealDictCursor

from app.auth import require_auth, require_auth_optional
from app.deps import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["locations"])


@router.get("/locations")
def get_weather_locations(
    tenant_id: str = Depends(require_auth_optional),
):
    """Get weather locations configured for the tenant."""
    if not tenant_id:
        tenant_id = "default"

    try:
        with get_db_connection(tenant_id) as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                SELECT
                    twl.id,
                    twl.municipality_code,
                    cm.name as municipality_name,
                    cm.latitude,
                    cm.longitude,
                    twl.station_id,
                    twl.label,
                    twl.is_primary,
                    twl.metadata
                FROM tenant_weather_locations twl
                JOIN catalog_municipalities cm ON cm.ine_code = twl.municipality_code
                WHERE twl.tenant_id = %s
                ORDER BY twl.is_primary DESC, twl.created_at DESC
                """,
                (tenant_id,),
            )
            locations = cur.fetchall()
            cur.close()

        return {"locations": [dict(loc) for loc in locations]}
    except Exception as e:
        logger.error(f"Error getting weather locations: {e}")
        return JSONResponse({"error": "Database error"}, status_code=500)


@router.get("/municipality/near")
def get_nearest_municipality(
    tenant_id: str = Depends(require_auth_optional),
    latitude: Optional[float] = Query(None),
    longitude: Optional[float] = Query(None),
    max_distance_km: float = Query(50.0),
):
    """Get nearest municipality to given coordinates."""
    if not tenant_id:
        tenant_id = "default"

    if not latitude or not longitude:
        return JSONResponse(
            {"error": "latitude and longitude are required"}, status_code=400
        )

    try:
        with get_db_connection(tenant_id) as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                SELECT
                    cm.ine_code, cm.name, cm.province, cm.autonomous_community,
                    cm.latitude, cm.longitude,
                    ST_Distance(
                        wo.location::geography,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                    ) / 1000.0 as distance_km
                FROM weather_observations wo
                JOIN catalog_municipalities cm ON cm.ine_code = wo.municipality_code
                WHERE wo.location IS NOT NULL
                AND ST_Distance(
                    wo.location::geography,
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                ) / 1000.0 <= %s
                ORDER BY distance_km ASC
                LIMIT 1
                """,
                (longitude, latitude, longitude, latitude, max_distance_km),
            )
            municipality = cur.fetchone()
            cur.close()

        if municipality:
            return {"municipality": dict(municipality)}
        return JSONResponse(
            {"error": "No municipality found within specified distance"},
            status_code=404,
        )
    except Exception as e:
        logger.error(f"Error finding nearest municipality: {e}")
        return JSONResponse({"error": "Database error"}, status_code=500)


@router.post("/locations")
def create_weather_location(
    request: Request,
    tenant_id: str = Depends(require_auth),
):
    """Create a new weather location for the tenant."""
    try:
        data = request.json()
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON body"}, status_code=400
        )

    municipality_code = data.get("municipality_code")
    if not municipality_code:
        return JSONResponse(
            {"error": "municipality_code is required"}, status_code=400
        )

    is_primary = data.get("is_primary", False)
    label = data.get("label")
    station_id = data.get("station_id")
    metadata = data.get("metadata", {})

    try:
        with get_db_connection(tenant_id) as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)

            # Verify municipality exists in catalog
            cur.execute(
                "SELECT ine_code, name FROM catalog_municipalities WHERE ine_code = %s",
                (municipality_code,),
            )
            municipality = cur.fetchone()

            if not municipality:
                # Try on-demand creation with known codes
                common_municipalities = {
                    "31001": {"name": "Pamplona", "province": "Navarra", "latitude": 42.8169, "longitude": -1.6432},
                    "28079": {"name": "Madrid", "province": "Madrid", "latitude": 40.4168, "longitude": -3.7038},
                    "08019": {"name": "Barcelona", "province": "Barcelona", "latitude": 41.3851, "longitude": 2.1734},
                    "41091": {"name": "Sevilla", "province": "Sevilla", "latitude": 37.3891, "longitude": -5.9845},
                    "46015": {"name": "Valencia", "province": "Valencia", "latitude": 39.4699, "longitude": -0.3763},
                }
                mun_data = common_municipalities.get(municipality_code)
                if mun_data:
                    cur.execute(
                        """
                        INSERT INTO catalog_municipalities
                        (ine_code, name, province, latitude, longitude, geom)
                        VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                        ON CONFLICT (ine_code) DO NOTHING
                        """,
                        (
                            municipality_code, mun_data["name"], mun_data["province"],
                            mun_data["longitude"], mun_data["latitude"],
                            mun_data["longitude"], mun_data["latitude"],
                        ),
                    )
                    logger.info(f"Created municipality {municipality_code} ({mun_data['name']}) in catalog")
                else:
                    cur.execute(
                        """
                        INSERT INTO catalog_municipalities
                        (ine_code, name, latitude, longitude, geom)
                        VALUES (%s, %s, NULL, NULL, NULL)
                        ON CONFLICT (ine_code) DO NOTHING
                        """,
                        (municipality_code, f"Municipality {municipality_code}"),
                    )
                    logger.warning(f"Created municipality {municipality_code} with minimal info")

                # Re-fetch municipality
                cur.execute(
                    "SELECT ine_code, name FROM catalog_municipalities WHERE ine_code = %s",
                    (municipality_code,),
                )
                municipality = cur.fetchone()

            # If setting as primary, unset other primary locations
            if is_primary:
                cur.execute(
                    "UPDATE tenant_weather_locations SET is_primary = false WHERE tenant_id = %s",
                    (tenant_id,),
                )

            # Insert new location
            cur.execute(
                """
                INSERT INTO tenant_weather_locations
                (tenant_id, municipality_code, station_id, label, is_primary, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, municipality_code)
                DO UPDATE SET
                    station_id = EXCLUDED.station_id,
                    label = EXCLUDED.label,
                    is_primary = EXCLUDED.is_primary,
                    metadata = EXCLUDED.metadata,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id, municipality_code, station_id, label, is_primary, metadata, created_at, updated_at
                """,
                (tenant_id, municipality_code, station_id, label, is_primary, json.dumps(metadata)),
            )
            result = cur.fetchone()
            conn.commit()

            if not result:
                return JSONResponse({"error": "Failed to create location"}, status_code=500)

            # Get full location with municipality name
            cur.execute(
                """
                SELECT
                    twl.id, twl.municipality_code,
                    cm.name as municipality_name,
                    cm.latitude, cm.longitude,
                    twl.station_id, twl.label, twl.is_primary, twl.metadata
                FROM tenant_weather_locations twl
                JOIN catalog_municipalities cm ON cm.ine_code = twl.municipality_code
                WHERE twl.id = %s
                """,
                (result["id"],),
            )
            location = cur.fetchone()
            cur.close()

            if not location:
                return JSONResponse({"error": "Location created but not found"}, status_code=500)

            return JSONResponse({"location": dict(location)}, status_code=201)

    except Exception as e:
        logger.error(f"Error creating weather location: {e}")
        return JSONResponse({"error": "Database error"}, status_code=500)
