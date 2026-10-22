"""
GET /api/weather/observations/latest — latest observations per location.
GET /api/weather/observations — filtered historical observations.

Source: telemetry_events WeatherObserved rows (per-parcel virtual stations
written through the Orion-LD subscription). The legacy weather_observations
table is no longer read.
"""

from common.tenant_constants import SHARED_TENANT
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

# Mapping from telemetry_events WeatherObserved measurement keys to the
# column names of the response (NGSI-LD attribute → response field). The
# platform @context is not injective, so a field can arrive under several
# aliases (e.g. `temperature` / `airTemperature`); the first alias present wins.
_TELEMETRY_TO_WEATHER_COLUMN = {
    "temperature": "temp_avg",
    "airTemperature": "temp_avg",
    "tempMin": "temp_min",
    "tempMax": "temp_max",
    "relativeHumidity": "humidity_avg",
    "humidity": "humidity_avg",
    "windSpeed": "wind_speed_ms",
    "windGusts": "wind_gusts_ms",
    "windDirection": "wind_direction_deg",
    "precipitation": "precip_mm",
    "atmosphericPressure": "pressure_hpa",
    "solarRadiation": "solar_rad_w_m2",
    "et0": "eto_mm",
    "soilMoistureTop": "soil_moisture_0_10cm",
    "soilMoistureSub": "soil_moisture_10_40cm",
    "gddAccumulated": "gdd_accumulated",
    "deltaT": "delta_t",
    "municipalityCode": "municipality_code",
    "sourceConfidence": "source",
}

_WEATHER_ENTITY_TYPES = (
    "WeatherObserved",
    "https://saref.etsi.org/saref4agri/WeatherObserved",
)


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
    """Query telemetry_events for WeatherObserved virtual station data.

    Reads the telemetry events written by the Orion-LD subscription
    (ParcelWeatherEngine path). Absent measurements are omitted, never zeroed.
    Raises on database errors so callers can answer 500 instead of an empty list.
    """
    with get_db_connection(tenant_id) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Build the base WHERE clause
        # Closed-day series entities ("...-daily") are not stations.
        where = [
            "entity_type IN (%s, %s)",
            "tenant_id = %s",
            "entity_id NOT LIKE '%%-daily'",
        ]
        params = [*_WEATHER_ENTITY_TYPES, tenant_id]

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
                if telem_key in measurements and weather_col not in obs:
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


@router.get("/observations/latest")
def get_latest_weather_observations(
    tenant_id: str = Depends(require_auth_optional),
    municipality_code: Optional[str] = Query(None),
    source: str = Query("OPEN-METEO"),
    data_type: str = Query("HISTORY"),
):
    """Get latest weather observations for tenant locations.

    Source: telemetry_events WeatherObserved (parcel virtual stations).
    """
    if not tenant_id:
        tenant_id = SHARED_TENANT

    try:
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

    Source: telemetry_events WeatherObserved (parcel virtual stations).
    """
    if not tenant_id:
        tenant_id = SHARED_TENANT

    # FORECAST default window
    if data_type == "FORECAST" and not start_date and not end_date:
        now = datetime.utcnow()
        start_date = (now - timedelta(hours=1)).isoformat()
        end_date = (now + timedelta(days=8)).isoformat()
        limit = min(limit, 250) if limit <= 100 else limit
    if data_type == "FORECAST" and limit == 100:
        limit = 250

    try:
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
