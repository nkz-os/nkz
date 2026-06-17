"""
GET /api/weather/alerts — active weather alerts from Orion-LD.

Migrated from direct PostgreSQL read to Orion-LD query (2026-06-05).
WeatherAlert entities live in tenant 'default' (alerts are geographic,
cross-tenant). For historical alerts, future work will query TimescaleDB
via the telemetry subscription pipeline.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.auth import require_auth
from app.config import settings
from common.ngsi_headers import inject_fiware_headers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["alerts"])


def _orion_headers(tenant_id: str) -> dict:
    """Build Orion-LD headers with tenant context and JSON-LD Link header."""
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)


@router.get("/alerts")
def get_weather_alerts(
    tenant_id: str = Depends(require_auth),
    municipality_code: Optional[str] = Query(None),
    alert_type: Optional[str] = Query(None),
):
    """
    Get active weather alerts from Orion-LD (WeatherAlert entities).

    Queries tenant 'default' — alerts are geographic (by AEMET zone),
    affecting all tenants in that area. Returns current-state only
    (validTo > now). For historical alerts, query TimescaleDB directly.

    Optional query params:
    - municipality_code: filter by INE municipality code
    - alert_type: filter by severity (minor, moderate, severe)
    """
    try:
        headers = _orion_headers("default")  # alerts live in default tenant

        # Filter: only alerts that haven't expired (validTo > now)
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        params: dict = {
            "type": "WeatherAlert",
            "q": f'validTo>"{now_iso}"',
            "options": "keyValues",
            "limit": 100,
        }

        resp = requests.get(
            f"{settings.orion_url}/ngsi-ld/v1/entities",
            params=params,
            headers=headers,
            timeout=10,
        )

        if resp.status_code != 200:
            logger.warning(
                f"Orion-LD alerts query returned {resp.status_code}: {resp.text[:200]}"
            )
            return {"alerts": [], "count": 0, "source": "orion-ld"}

        raw = resp.json()
        if isinstance(raw, dict):
            alerts = [raw]
        elif isinstance(raw, list):
            alerts = raw
        else:
            alerts = []

        # Optional: filter by municipality_code
        if municipality_code:
            alerts = [
                a
                for a in alerts
                if a.get("municipalityCode", {}).get("value", "") == municipality_code
                if isinstance(a.get("municipalityCode"), dict)
            ]

        # Optional: filter by severity (subCategory)
        if alert_type:
            alerts = [
                a
                for a in alerts
                if alert_type.lower() in [
                    s.lower()
                    for s in (
                        a.get("subCategory", {}).get("value", [])
                        if isinstance(a.get("subCategory"), dict)
                        else []
                    )
                ]
            ]

        # Sort by severity (critical first) and validFrom (newest first)
        severity_order = {"severe": 0, "moderate": 1, "minor": 2, "informational": 3}
        alerts.sort(
            key=lambda a: (
                severity_order.get(
                    (
                        a.get("severity", {}).get("value", "")
                        if isinstance(a.get("severity"), dict)
                        else ""
                    ),
                    99,
                ),
            )
        )

        return {"alerts": alerts, "count": len(alerts), "source": "orion-ld"}

    except Exception as e:
        logger.error(f"Error querying WeatherAlert from Orion-LD: {e}")
        return JSONResponse({"error": "Failed to query alerts", "detail": str(e)}, status_code=500)
