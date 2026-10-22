"""
Alert Manager — creates and manages Alert entities in Orion-LD.

Alert entities are the canonical platform-level alert mechanism.
Any subsystem (DataHub, Odoo, email, etc.) subscribes to them via Orion-LD.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)


class AlertManager:
    """Creates and manages Alert entities in Orion-LD."""

    def __init__(self, orion_url: str, context_url: str):
        self._orion_url = orion_url.rstrip("/")
        self._context_url = context_url

    async def create_alert(
        self,
        tenant_id: str,
        sensor_id: str,
        sensor_name: str,
        alert_type: str,
        variables: List[str],
        description: str,
    ) -> Optional[str]:
        """
        Create an Alert entity in Orion-LD.

        alert_type: 'stagnation' | 'timeout' | 'out_of_bounds' | 'nan'
        Returns the alert entity ID, or None on failure.
        """
        alert_id = f"urn:ngsi-ld:Alert:{tenant_id}:{uuid4().hex[:12]}"
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        entity = {
            "id": alert_id,
            "type": "Alert",
            "@context": self._context_url,
            "category": {"type": "Property", "value": "sensor_failure"},
            "alertType": {"type": "Property", "value": alert_type},
            "description": {"type": "Property", "value": description},
            "observedAt": {"type": "Property", "value": now},
            "severity": {"type": "Property", "value": "high"},
            "refSourceSensor": {"type": "Relationship", "object": sensor_id},
            "affectedVariables": {"type": "Property", "value": variables},
            "status": {"type": "Property", "value": "active"},
        }

        headers = {
            "NGSILD-Tenant": tenant_id,
            "Fiware-Service": tenant_id,
            "Fiware-ServicePath": "/",
            "Content-Type": "application/ld+json",
        }

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self._orion_url}/ngsi-ld/v1/entities",
                    json=entity,
                    headers=headers,
                    timeout=10,
                )
                if resp.status_code == 201:
                    logger.info(
                        f"Created Alert {alert_id} for sensor {sensor_id} ({alert_type})"
                    )
                    return alert_id
                else:
                    logger.error(
                        f"Failed to create Alert: {resp.status_code} {resp.text[:200]}"
                    )
                    return None
            except Exception as e:
                logger.error(f"Error creating Alert entity: {e}")
                return None

    async def close_alert(self, tenant_id: str, alert_id: str) -> bool:
        """Mark an alert as resolved."""
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {
            "status": {"type": "Property", "value": "resolved"},
            "resolvedAt": {"type": "Property", "value": now},
        }
        headers = {
            "NGSILD-Tenant": tenant_id,
            "Fiware-Service": tenant_id,
            "Fiware-ServicePath": "/",
            "Content-Type": "application/json",
            "Link": f'<{self._context_url}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"',
        }
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.patch(
                    f"{self._orion_url}/ngsi-ld/v1/entities/{alert_id}/attrs",
                    json=body,
                    headers=headers,
                    timeout=10,
                )
                return resp.status_code in (200, 204)
            except Exception as e:
                logger.error(f"Error closing alert {alert_id}: {e}")
                return False

    async def get_active_alerts_for_sensor(
        self, tenant_id: str, sensor_id: str
    ) -> List[Dict]:
        """Get active Alert entities for a specific sensor."""
        headers = {
            "NGSILD-Tenant": tenant_id,
            "Fiware-Service": tenant_id,
            "Fiware-ServicePath": "/",
            "Accept": "application/json",
            "Link": f'<{self._context_url}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"',
        }
        url = (
            f"{self._orion_url}/ngsi-ld/v1/entities"
            f"?type=Alert&options=keyValues"
            f"&q=refSourceSensor==%22{sensor_id}%22;status==active"
        )
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        return data
            except Exception as e:
                logger.error(f"Error fetching active alerts: {e}")
        return []
