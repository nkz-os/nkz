"""
GET /api/weather/municipalities/search — search municipalities in catalog.
"""

import logging
from typing import Optional

import requests
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg2.extras import RealDictCursor

from app.auth import require_auth_optional
from app.config import settings
from app.deps import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["municipalities"])


@router.get("/municipalities/search")
def search_municipalities(
    tenant_id: str = Depends(require_auth_optional),
    q: str = Query("", min_length=2),
    limit: int = Query(20),
):
    """Search municipalities in catalog (supports AEMET/INE codes and names)."""
    if not tenant_id:
        tenant_id = "default"

    if not q or len(q) < 2:
        return {"municipalities": []}

    try:
        with get_db_connection(tenant_id) as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)

            search_term = f"%{q}%"
            cur.execute(
                """
                SELECT
                    ine_code, name, province, autonomous_community,
                    aemet_id, latitude, longitude
                FROM catalog_municipalities
                WHERE
                    LOWER(name) LIKE LOWER(%s)
                    OR ine_code LIKE %s
                    OR LOWER(province) LIKE LOWER(%s)
                ORDER BY
                    CASE
                        WHEN LOWER(name) = LOWER(%s) THEN 1
                        WHEN LOWER(name) LIKE LOWER(%s) THEN 2
                        WHEN ine_code = %s THEN 3
                        ELSE 4
                    END,
                    name ASC
                LIMIT %s
                """,
                (search_term, search_term, search_term, q, f"{q}%", q, limit),
            )
            municipalities = cur.fetchall()

            # AEMET API fallback
            if not municipalities and settings.aemet_api_key:
                try:
                    logger.info(f"Local catalog empty for '{q}', trying AEMET API")
                    aemet_url = "https://opendata.aemet.es/opendata/api/maestro/municipios"
                    aemet_response = requests.get(
                        aemet_url,
                        headers={"api_key": settings.aemet_api_key},
                        timeout=10,
                    )
                    aemet_response.raise_for_status()
                    data_url = aemet_response.json().get("datos")
                    if data_url:
                        data_response = requests.get(data_url, timeout=30)
                        data_response.raise_for_status()
                        aemet_data = data_response.json()

                        found = []
                        for muni in aemet_data:
                            muni_name = muni.get("nombre", "").lower()
                            muni_id = muni.get("id", "")
                            if q.lower() in muni_name or q in muni_id:
                                # Insert into catalog
                                cur.execute(
                                    """
                                    INSERT INTO catalog_municipalities
                                    (ine_code, name, province, aemet_id, latitude, longitude, geom)
                                    VALUES (%s, %s, %s, %s, %s, %s,
                                        CASE
                                            WHEN %s IS NOT NULL AND %s IS NOT NULL
                                            THEN ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                                            ELSE NULL
                                        END)
                                    ON CONFLICT (ine_code) DO UPDATE SET
                                        name = EXCLUDED.name,
                                        province = EXCLUDED.province,
                                        aemet_id = EXCLUDED.aemet_id,
                                        latitude = EXCLUDED.latitude,
                                        longitude = EXCLUDED.longitude
                                    RETURNING ine_code, name, province, autonomous_community, aemet_id, latitude, longitude
                                    """,
                                    (
                                        muni.get("id"), muni.get("nombre"),
                                        muni.get("provincia"), muni.get("idAEMET"),
                                        muni.get("latitud_dec"), muni.get("longitud_dec"),
                                        muni.get("longitud_dec"), muni.get("latitud_dec"),
                                        muni.get("longitud_dec"), muni.get("latitud_dec"),
                                    ),
                                )
                                inserted = cur.fetchone()
                                if inserted:
                                    found.append(dict(inserted))
                                if len(found) >= limit:
                                    break
                        conn.commit()
                        if found:
                            municipalities = found
                            logger.info(f"Found {len(found)} municipalities from AEMET for '{q}'")
                except Exception as e:
                    logger.warning(f"Error fetching from AEMET: {e}")
                    conn.rollback()

            cur.close()

        # Geocode municipalities without coordinates on-demand (Nominatim)
        geocoded_count = 0
        for mun in municipalities:
            if not mun.get("latitude") or not mun.get("longitude"):
                try:
                    coords = _geocode_municipality(
                        name=mun.get("name", ""),
                        province=mun.get("province"),
                        ine_code=mun.get("ine_code"),
                    )
                    if coords:
                        lat, lon = coords
                        with get_db_connection(tenant_id) as conn2:
                            cur2 = conn2.cursor(cursor_factory=RealDictCursor)
                            cur2.execute(
                                """
                                UPDATE catalog_municipalities
                                SET latitude = %s, longitude = %s,
                                    geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                                WHERE ine_code = %s
                                RETURNING latitude, longitude
                                """,
                                (lat, lon, lon, lat, mun.get("ine_code")),
                            )
                            updated = cur2.fetchone()
                            if updated:
                                mun["latitude"] = updated["latitude"]
                                mun["longitude"] = updated["longitude"]
                                geocoded_count += 1
                            cur2.close()
                            conn2.commit()
                except Exception as e:
                    logger.warning(f"Error geocoding municipality {mun.get('ine_code')}: {e}")

        if geocoded_count > 0:
            logger.info(f"Geocoded {geocoded_count} municipalities on-demand")

        return {
            "municipalities": [dict(m) for m in municipalities],
            "count": len(municipalities),
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error searching municipalities: {e}", exc_info=True)
        if "does not exist" in error_msg.lower() or "relation" in error_msg.lower():
            return JSONResponse(
                {
                    "error": "Database schema incomplete",
                    "detail": "The catalog_municipalities table is missing.",
                },
                status_code=500,
            )
        return JSONResponse(
            {"error": "Database error", "detail": error_msg}, status_code=500
        )


def _geocode_municipality(
    name: str,
    province: Optional[str] = None,
    ine_code: Optional[str] = None,
    country: str = "Spain",
) -> Optional[tuple]:
    """Geocode a municipality using Nominatim (OSM) on-demand. Returns (latitude, longitude)."""
    try:
        query_parts = [name]
        if province:
            query_parts.append(province)
        query_parts.append(country)
        query = ", ".join(query_parts)

        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "es"},
            headers={"User-Agent": "Nekazari-Platform/1.0 (Weather Service)"},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()
        if data:
            result = data[0]
            lat = float(result.get("lat", 0))
            lon = float(result.get("lon", 0))
            if lat != 0 and lon != 0:
                logger.info(f"Geocoded '{name}' ({ine_code}): {lat}, {lon}")
                return (lat, lon)
        logger.warning(f"Could not geocode municipality '{name}' ({ine_code})")
        return None
    except requests.exceptions.Timeout:
        logger.warning(f"Geocoding timeout for '{name}' ({ine_code})")
        return None
    except Exception as e:
        logger.warning(f"Geocoding error for '{name}' ({ine_code}): {e}")
        return None
