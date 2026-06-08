"""
GET /api/weather/observations/latest — latest observations per location.
GET /api/weather/observations — filtered historical observations.

When weather_observations table is empty (e.g., when only the ParcelWeatherEngine
is running and wrote data through Orion-LD → telemetry_events), these endpoints
fall back to telemetry_events for WeatherObserved virtual station data.
"""

import json
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

# Mapping from telemetry_events WeatherObserved measurement keys to
# weather_observations column names (NGSI-LD attribute → DB column).
_TELEMETRY_TO_WEATHER_COLUMN = {
    "temperature": "temp_avg",
    "relativeHumidity": "humidity_avg",
    "windSpeed": "wind_speed_ms",
    "windDirection": "wind_direction_deg",
    "precipitation": "precip_mm",
    "atmosphericPressure": "pressure_hpa",
    "et0": "eto_mm",
    "deltaT": "delta_t",
    "municipalityCode": "municipality_code",
    "sourceConfidence": "source",
}


def _fetch_from_telemetry_events(
    tenant_id: str,
    municipality_code: Optional[str] = None,
    source: Optional[str] = None,
    data_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
    latest_only: bool = False,
):
    """Fallback: query telemetry_events for WeatherObserved virtual station data.

    Used when weather_observations is empty — reads the telemetry events
    written by the Orion-LD subscription (ParcelWeatherEngine path).
    """
    try:
        with get_db_connection(tenant_id) as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)

            # Build the base WHERE clause
            where = ["entity_type = 'WeatherObserved'", "tenant_id = %s"]
            params = [tenant_id]

            # municipality_code is stored in payload.measurements
            if municipality_code:
                where.append(
                    "payload #>> '{measurements,municipalityCode}' = %s"
                )
                params.append(municipality_code)

            # source is stored in payload.measurements.sourceConfidence
            if source:
                where.append(
                    "payload #>> '{measurements,sourceConfidence}' = %s"
                )
                params.append(source)

            if start_date:
                where.append("observed_at >= %s")
                params.append(start_date)
            if end_date:
                where.append("observed_at <= %s")
                params.append(end_date)

            where_clause = " AND ".join(where)

            if latest_only:
                # Latest per entity (virtual station)
                query = f"""
                    SELECT DISTINCT ON (entity_id)
                        entity_id,
                        observed_at,
                        payload->'measurements' as measurements_raw,
                        payload->'raw'->'location' as location_raw
                    FROM telemetry_events
                    WHERE {where_clause}
                    ORDER BY entity_id, observed_at DESC
                """
            else:
                query = f"""
                    SELECT
                        entity_id,
                        observed_at,
                        payload->'measurements' as measurements_raw,
                        payload->'raw'->'location' as location_raw
                    FROM telemetry_events
                    WHERE {where_clause}
                    ORDER BY observed_at DESC
                    LIMIT %s
                """
                params.append(limit)

            cur.execute(query, params)
            rows = cur.fetchall()
            cur.close()

            # Map telemetry measurement keys → weather_observations column names
            observations = []
            for row in rows:
                measurements = row.get("measurements_raw") or {}
                if isinstance(measurements, str):
                    try:
                        measurements = json.loads(measurements)
                    except json.JSONDecodeError:
                        measurements = {}

                obs = {
                    "observed_at": row["observed_at"].isoformat()
                    if hasattr(row["observed_at"], "isoformat")
                    else str(row["observed_at"]),
                    "entity_id": row.get("entity_id", ""),
                }

                # Map known weather attributes
                for telem_key, weather_col in _TELEMETRY_TO_WEATHER_COLUMN.items():
                    if telem_key in measurements:
                        obs[weather_col] = measurements[telem_key]

                # Extract location coordinates for geo support
                location_raw = row.get("location_raw")
                if location_raw and isinstance(location_raw, dict):
                    coords = (
                        location_raw.get("value", {}).get("coordinates", [])
                        if isinstance(location_raw.get("value"), dict)
                        else location_raw.get("coordinates", [])
                    )
                    if coords and len(coords) >= 2:
                        obs["longitude"] = coords[0]
                        obs["latitude"] = coords[1]

                # Set default values for columns that don't exist in telemetry
                obs.setdefault("source", "OPEN-METEO")
                obs.setdefault("data_type", data_type or "HISTORY")
                obs.setdefault("municipality_code", measurements.get("municipalityCode", ""))

                observations.append(obs)

            return observations

    except Exception as e:
        logger.warning(f"Telemetry fallback query failed: {e}")
        return []


@router.get("/observations/latest")
def get_latest_weather_observations(
    tenant_id: str = Depends(require_auth_optional),
    municipality_code: Optional[str] = Query(None),
    source: str = Query("OPEN-METEO"),
    data_type: str = Query("HISTORY"),
):
    """Get latest weather observations for tenant locations.

    Primary source: weather_observations (legacy municipal worker).
    Fallback: telemetry_events WeatherObserved (parcel virtual stations).
    """
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
            return [dict(r) for r in rows]

    try:
        observations = _fetch_for_tenant(tenant_id)

        if not observations:
            # Fallback: query telemetry_events for WeatherObserved per-parcel data
            logger.info(
                f"No weather_observations for tenant {tenant_id}, "
                f"falling back to telemetry_events WeatherObserved"
            )
            observations = _fetch_from_telemetry_events(
                tenant_id=tenant_id,
                municipality_code=municipality_code,
                source=source,
                data_type=data_type,
                latest_only=True,
            )

        return {"observations": observations}
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
    """Get weather observations with optional filters.

    Primary source: weather_observations (legacy municipal worker).
    Fallback: telemetry_events WeatherObserved (parcel virtual stations).
    """
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
            return [dict(r) for r in rows]

    try:
        observations = _fetch_for_tenant(tenant_id)

        if not observations and tenant_id == "default":
            # Already on default, no fallback
            pass
        elif not observations:
            # Fallback: query telemetry_events for WeatherObserved per-parcel data
            logger.info(
                f"No weather_observations for tenant {tenant_id}, "
                f"falling back to telemetry_events WeatherObserved"
            )
            observations = _fetch_from_telemetry_events(
                tenant_id=tenant_id,
                municipality_code=municipality_code,
                source=source,
                data_type=data_type,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )

        return {
            "observations": observations,
            "count": len(observations),
        }
    except Exception as e:
        logger.error(f"Error getting weather observations: {e}")
        return JSONResponse({"error": "Database error"}, status_code=500)
