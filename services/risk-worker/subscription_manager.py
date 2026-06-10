"""
Orion-LD subscription manager for RiskAssessment entities.

Ensures NGSI-LD subscriptions exist per tenant so that RiskAssessment
entity changes are forwarded to the notification handler.
"""

import logging
import os
import re

import psycopg2
import requests
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
SERVICE_HOST = os.getenv(
    "NOTIFICATION_SERVICE_HOST",
    os.getenv("SERVICE_HOST", "entity-manager-service"),
)
SERVICE_PORT = os.getenv("NOTIFICATION_SERVICE_PORT", "5000")
NOTIFICATION_URL = f"http://{SERVICE_HOST}:{SERVICE_PORT}/notify"
POSTGRES_URL = os.getenv("POSTGRES_URL", "")
DEFAULT_TENANT = "platform"

SUBSCRIPTIONS = [
    {
        "description": "Risk Worker - RiskAssessment evaluations",
        "type": "Subscription",
        "entities": [{"type": "RiskAssessment"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "throttling": 5,
        "isActive": True,
    },
]


def _make_headers(tenant_id: str) -> dict:
    """Build Orion-LD headers with normalized tenant ID."""
    n = tenant_id.lower().strip().replace("-", "_").replace(" ", "_")
    n = re.sub(r"[^a-z0-9_]", "", n)
    n = n.strip("_") or tenant_id
    headers = {
        "NGSILD-Tenant": n,
        "Fiware-Service": n,
        "Fiware-ServicePath": "/",
        "Accept": "application/ld+json",
    }
    ctx = os.getenv("CONTEXT_URL", "")
    if ctx:
        headers["Link"] = (
            f'<{ctx}>; rel="http://www.w3.org/ns/json-ld#context";'
            ' type="application/ld+json"'
        )
    return headers


def _get_active_tenants() -> list:
    """Query PostgreSQL for all active tenant IDs."""
    if not POSTGRES_URL:
        return [DEFAULT_TENANT]
    try:
        conn = psycopg2.connect(POSTGRES_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT tenant_id FROM tenants WHERE tenant_id IS NOT NULL"
            )
            rows = cur.fetchall()
            cur.close()
            return [r[0] for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.error("Error querying active tenants: %s", e)
        return [DEFAULT_TENANT]


def _ensure_tenant_subscriptions(tenant_id: str):
    """Create missing NGSI-LD subscriptions for a single tenant."""
    headers = _make_headers(tenant_id)
    headers["Content-Type"] = "application/json"
    try:
        response = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/subscriptions",
            headers=headers,
        )
        response.raise_for_status()
        existing_subs = response.json() if isinstance(response.json(), list) else []
        existing_descriptions = [sub.get("description") for sub in existing_subs]

        for sub_def in SUBSCRIPTIONS:
            if sub_def["description"] in existing_descriptions:
                logger.debug(
                    "Subscription '%s' exists for %s",
                    sub_def["description"],
                    tenant_id,
                )
            else:
                logger.info(
                    "Creating subscription '%s' for %s",
                    sub_def["description"],
                    tenant_id,
                )
                res = requests.post(
                    f"{ORION_URL}/ngsi-ld/v1/subscriptions",
                    json=sub_def,
                    headers=headers,
                )
                if res.status_code in [200, 201]:
                    logger.info(
                        "Created: %s for %s",
                        sub_def["description"],
                        tenant_id,
                    )
                else:
                    logger.error(
                        "Failed: %s for %s: %s %s",
                        sub_def["description"],
                        tenant_id,
                        res.status_code,
                        res.text[:200],
                    )
    except Exception as e:
        logger.error("Error managing subscriptions for %s: %s", tenant_id, e)


@retry(stop=stop_after_attempt(5), wait=wait_fixed(5))
def ensure_subscriptions_for_all_tenants():
    """Create NGSI-LD subscriptions for all active tenants."""
    tenants = _get_active_tenants()
    if not tenants:
        tenants = [DEFAULT_TENANT]
    logger.info("Ensuring RiskAssessment subscriptions for %d tenants", len(tenants))
    for tenant_id in tenants:
        _ensure_tenant_subscriptions(tenant_id)


# Backwards compat alias
check_or_create_subscription = ensure_subscriptions_for_all_tenants
