import os
import logging
import requests
import json
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
SERVICE_HOST = os.getenv("SERVICE_HOST", "telemetry-worker-service")
SERVICE_PORT = os.getenv("SERVICE_PORT", "8080")
NOTIFICATION_URL = f"http://{SERVICE_HOST}:{SERVICE_PORT}/notify"
CONTEXT_URL = os.getenv("CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json")
# Default tenant for IoT devices (NGSILD-Tenant header)
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
                "accept": "application/json"
            },
            "format": "normalized"
        },
        "throttling": 30,
        "isActive": True
    },
    {
        "description": "Telemetry Worker - Device updates",
        "type": "Subscription",
        "entities": [{"type": "Device"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json"
            },
            "format": "normalized"
        },
        "throttling": 30,
        "isActive": True
    },
    {
        "description": "Telemetry Worker - AgriParcel updates",
        "type": "Subscription",
        "entities": [{"type": "AgriParcel"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json"
            },
            "format": "normalized"
        },
        "throttling": 30,
        "isActive": True
    },
]


def _get_headers(tenant: str) -> dict:
    """Standard NGSI-LD headers with tenant and @context Link."""
    return {
        "Content-Type": "application/json",
        "NGSILD-Tenant": tenant,
        "Link": f'<{CONTEXT_URL}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"',
    }


@retry(stop=stop_after_attempt(5), wait=wait_fixed(5))
def check_or_create_subscription():
    """Check if subscriptions exist in Orion-LD and create them if not."""
    logger.info(f"Checking subscriptions against Orion at {ORION_URL} for tenant={DEFAULT_TENANT}...")

    try:
        headers = _get_headers(DEFAULT_TENANT)

        # Get existing subs for this tenant
        response = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/subscriptions",
            headers=headers
        )
        response.raise_for_status()
        existing_subs = response.json()

        existing_descriptions = [
            sub.get("description") for sub in existing_subs
        ] if existing_subs else []

        for sub in SUBSCRIPTIONS:
            if sub["description"] in existing_descriptions:
                logger.info(f"Subscription '{sub['description']}' already exists.")
            else:
                logger.info(f"Creating subscription: '{sub['description']}'...")
                res = requests.post(
                    f"{ORION_URL}/ngsi-ld/v1/subscriptions",
                    json=sub,
                    headers=headers,
                )
                if res.status_code in [200, 201]:
                    logger.info(f"Created subscription: {sub['description']}")
                else:
                    logger.error(
                        f"Failed to create subscription {sub['description']}: "
                        f"{res.status_code} {res.text}"
                    )

    except Exception as e:
        logger.error(f"Error communicating with Orion: {e}")
        raise e
