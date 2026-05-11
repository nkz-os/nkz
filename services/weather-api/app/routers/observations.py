"""
GET /api/weather/observations/latest — latest observations per location.
GET /api/weather/observations — filtered historical observations.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg2.extras import RealDictCursor

from app.auth import require_auth_optional
from app.deps import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["observations"])


@router.get("/observations/latest")
def get_latest_weather_observations(
    tenant_id: str = Depends(require_auth_optional),
    municipality_code: Optional[str] = Query(None),
    source: str = Query("OPEN-METEO"),
    data_type: str = Query("HISTORY"),
):
    """Get latest weather observations for tenant locations."""
    if not tenant_id:
        tenant_id = "default"

    def _fetch_for_tenant(tid: str):
        with get_db_connection(tid) as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            query = """
                SELECT DISTINCT ON (municipality_code, source, data_type)
                    municipality_code,
                    source,
                    data_type,
                    observed_at,
                    temp_avg, temp_min, temp_max,
                    humidity_avg, precip_mm,
                    solar_rad_w_m2, solar_rad_ghi_w_m2, solar_rad_dni_w_m2,
                    eto_mm, soil_moisture_0_10cm, soil_moisture_10_40cm,
                    wind_speed_ms, wind_direction_deg, pressure_hpa,
                    gdd_accumulated, delta_t,
                    metrics, metadata
                FROM weather_observations
                WHERE tenant_id = %s
            """
            params = [tid]
            if municipality_code:
                query += " AND municipality_code = %s"
                params.append(municipality_code)
            if source:
                query += " AND source = %s"
                params.append(source)
            if data_type:
                query += " AND data_type = %s"
                params.append(data_type)
            query += " ORDER BY municipality_code, source, data_type, observed_at DESC"
            cur.execute(query, params)
            rows = cur.fetchall()
            cur.close()
            return rows

    try:
        observations = _fetch_for_tenant(tenant_id)
        if not observations and tenant_id != "default":
            logger.info(
                f"No observations for tenant {tenant_id}, falling back to default"
            )
            observations = _fetch_for_tenant("default")
        return {"observations": [dict(obs) for obs in observations]}
    except Exception as e:
        logger.error(f"Error getting latest weather observations: {e}")
        return JSONResponse({"error": "Database error"}, status_code=500)


@router.get("/observations")
def get_weather_observations(
    tenant_id: str = Depends(require_auth_optional),
    municipality_code: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    data_type: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(100),
):
    """Get weather observations with optional filters."""
    if not tenant_id:
        tenant_id = "default"

    # FORECAST default window
    if data_type == "FORECAST" and not start_date and not end_date:
        now = datetime.utcnow()
        start_date = (now - timedelta(hours=1)).isoformat()
        end_date = (now + timedelta(days=8)).isoformat()
        limit = min(limit, 250) if limit <= 100 else limit
    if data_type == "FORECAST" and limit == 100:
        limit = 250

    def _fetch_for_tenant(tid: str):
        with get_db_connection(tid) as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            query = """
                SELECT
                    municipality_code, source, data_type, observed_at,
                    temp_avg, temp_min, temp_max,
                    humidity_avg, precip_mm,
                    solar_rad_w_m2, solar_rad_ghi_w_m2, solar_rad_dni_w_m2,
                    eto_mm, soil_moisture_0_10cm, soil_moisture_10_40cm,
                    wind_speed_ms, wind_direction_deg, pressure_hpa,
                    gdd_accumulated, delta_t,
                    metrics, metadata
                FROM weather_observations
                WHERE tenant_id = %s
            """
            params = [tid]

            if municipality_code:
                query += " AND municipality_code = %s"
                params.append(municipality_code)
            if source:
                query += " AND source = %s"
                params.append(source)
            if data_type:
                query += " AND data_type = %s"
                params.append(data_type)
            if start_date:
                query += " AND observed_at >= %s"
                params.append(start_date)
            if end_date:
                query += " AND observed_at <= %s"
                params.append(end_date)

            query += " ORDER BY observed_at DESC LIMIT %s"
            params.append(limit)

            cur.execute(query, params)
            rows = cur.fetchall()
            cur.close()
            return rows

    try:
        observations = _fetch_for_tenant(tenant_id)
        if not observations and tenant_id != "default":
            observations = _fetch_for_tenant("default")

        return {
            "observations": [dict(obs) for obs in observations],
            "count": len(observations),
        }
    except Exception as e:
        logger.error(f"Error getting weather observations: {e}")
        return JSONResponse({"error": "Database error"}, status_code=500)
