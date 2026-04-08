import os
import logging
import requests

from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
SERVICE_HOST = os.getenv("SERVICE_HOST", "telemetry-worker-service")
SERVICE_PORT = os.getenv("SERVICE_PORT", "80")
NOTIFICATION_URL = f"http://{SERVICE_HOST}:{SERVICE_PORT}/notify"
CONTEXT_URL = os.getenv(
    "CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json"
)
POSTGRES_URL = os.getenv("POSTGRES_URL", "")
DEFAULT_TENANT = os.getenv("DEFAULT_TENANT", "platform")

# NGSI-LD subscriptions — no watchedAttributes = trigger on ANY attribute change
SUBSCRIPTIONS = [
    {
        "description": "Telemetry Worker - AgriSensor updates",
        "type": "Subscription",
        "entities": [{"type": "AgriSensor"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "throttling": 30,
        "isActive": True,
    },
    {
        "description": "Telemetry Worker - Device updates",
        "type": "Subscription",
        "entities": [{"type": "Device"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "throttling": 30,
        "isActive": True,
    },
    {
        "description": "Telemetry Worker - AgriParcel updates",
        "type": "Subscription",
        "entities": [{"type": "AgriParcel"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "throttling": 30,
        "isActive": True,
    },
    {
        "description": "Telemetry Worker - VegetationIndex analysis results",
        "type": "Subscription",
        "entities": [{"type": "VegetationIndex"}],
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


def _get_headers(tenant: str) -> dict:
    """Standard NGSI-LD headers with tenant and @context Link."""
    return {
        "Content-Type": "application/json",
        "NGSILD-Tenant": tenant,
        "Link": f'<{CONTEXT_URL}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"',
    }


def _get_active_tenants() -> list:
    """Query PostgreSQL for all active tenant IDs."""
    if not POSTGRES_URL:
        logger.warning("POSTGRES_URL not set, cannot query tenants")
        return []
    try:
        import psycopg2

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
        logger.error(f"Error querying active tenants: {e}")
        return []


def _cleanup_broken_subscriptions(tenant_id: str):
    """Delete subscriptions with wrong port (legacy bug)."""
    headers = _get_headers(tenant_id)
    try:
        r = requests.get(f"{ORION_URL}/ngsi-ld/v1/subscriptions", headers=headers)
        if r.status_code != 200:
            return
        for sub in r.json():
            uri = sub.get("notification", {}).get("endpoint", {}).get("uri", "")
            if ":8080" in uri and "telemetry-worker" in uri:
                sub_id = sub.get("id")
                requests.delete(
                    f"{ORION_URL}/ngsi-ld/v1/subscriptions/{sub_id}",
                    headers=headers,
                )
                logger.info(f"Deleted broken subscription {sub_id} (port 8080)")
    except Exception as e:
        logger.warning(f"Error cleaning broken subscriptions for {tenant_id}: {e}")


def _ensure_tenant_subscriptions(tenant_id: str):
    """Create missing NGSI-LD subscriptions for a single tenant."""
    headers = _get_headers(tenant_id)
    try:
        response = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/subscriptions",
            headers=headers,
        )
        response.raise_for_status()
        existing_subs = response.json()
        existing_descriptions = (
            [sub.get("description") for sub in existing_subs] if existing_subs else []
        )

        for sub in SUBSCRIPTIONS:
            if sub["description"] in existing_descriptions:
                logger.debug(
                    f"Subscription '{sub['description']}' exists for tenant {tenant_id}"
                )
            else:
                logger.info(
                    f"Creating subscription '{sub['description']}' for tenant {tenant_id}"
                )
                res = requests.post(
                    f"{ORION_URL}/ngsi-ld/v1/subscriptions",
                    json=sub,
                    headers=headers,
                )
                if res.status_code in [200, 201]:
                    logger.info(f"Created: {sub['description']} for {tenant_id}")
                else:
                    logger.error(
                        f"Failed: {sub['description']} for {tenant_id}: "
                        f"{res.status_code} {res.text}"
                    )
    except Exception as e:
        logger.error(f"Error managing subscriptions for {tenant_id}: {e}")


@retry(stop=stop_after_attempt(5), wait=wait_fixed(5))
def ensure_subscriptions_for_all_tenants():
    """Create NGSI-LD subscriptions for all active tenants."""
    tenants = _get_active_tenants()
    if not tenants:
        tenants = [DEFAULT_TENANT]
        logger.info(f"No tenants from DB, using default: {DEFAULT_TENANT}")

    logger.info(f"Ensuring subscriptions for {len(tenants)} tenants: {tenants}")

    for tenant_id in tenants:
        _cleanup_broken_subscriptions(tenant_id)
        _ensure_tenant_subscriptions(tenant_id)


# Backwards compat alias for app.py import
check_or_create_subscription = ensure_subscriptions_for_all_tenants
