"""
GET /api/weather/alerts — active weather alerts for tenant locations.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg2.extras import RealDictCursor

from app.auth import require_auth
from app.deps import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["alerts"])


@router.get("/alerts")
def get_weather_alerts(
    tenant_id: str = Depends(require_auth),
    municipality_code: Optional[str] = Query(None),
    alert_type: Optional[str] = Query(None),
    active_only: str = Query("true"),
):
    """Get active weather alerts for tenant locations."""
    try:
        with get_db_connection(tenant_id) as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)

            query = """
                SELECT
                    id, municipality_code, alert_type, alert_category,
                    effective_from, effective_to, description,
                    aemet_alert_id, metadata
                FROM weather_alerts
                WHERE tenant_id = %s
            """
            params = [tenant_id]

            if municipality_code:
                query += " AND municipality_code = %s"
                params.append(municipality_code)
            if alert_type:
                query += " AND alert_type = %s"
                params.append(alert_type)
            if active_only.lower() == "true":
                query += " AND effective_to >= CURRENT_TIMESTAMP"

            query += " ORDER BY effective_from DESC, alert_type DESC"
            cur.execute(query, params)
            alerts = cur.fetchall()
            cur.close()

        return {"alerts": [dict(alert) for alert in alerts], "count": len(alerts)}

    except Exception as e:
        # Table may not exist if weather-worker hasn't run yet
        if 'relation "weather_alerts" does not exist' in str(e):
            logger.info("weather_alerts table does not exist yet, returning empty alerts")
            return {"alerts": [], "count": 0}
        logger.error(f"Error getting weather alerts: {e}")
        return JSONResponse({"error": "Database error"}, status_code=500)
