import os
import logging
import requests

from prometheus_client import Counter
from tenacity import retry, stop_after_attempt, wait_fixed
from common.ngsi_headers import inject_fiware_headers

logger = logging.getLogger(__name__)

# Incremented whenever creating an Orion-LD subscription fails (non-2xx response
# or an exception). Ops can alert on a non-zero rate — a persistent failure
# means a tenant stops receiving telemetry notifications silently.
SUBSCRIPTION_CREATION_FAILED = Counter(
    "telemetry_subscription_creation_failed_total",
    "Orion-LD subscription creations that failed",
    ["tenant_id", "reason"],
)

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
SERVICE_HOST = os.getenv("SERVICE_HOST", "telemetry-worker-service")
SERVICE_PORT = os.getenv("SERVICE_PORT", "80")
NOTIFICATION_URL = f"http://{SERVICE_HOST}:{SERVICE_PORT}/notify"
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
        # Hardware provisioned through DaTaK is created as "AgriDevice", not "Device" —
        # this subscription exists for that provisioning path. "Device" itself IS a term
        # of the platform @context (has been since an earlier plan) and is what
        # DeviceMeasurement.refDevice points at; the subscription below covers it.
        "description": "Telemetry Worker - AgriDevice updates",
        "type": "Subscription",
        "entities": [{"type": "AgriDevice"}],
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
        # DeviceMeasurement is what entity-manager writes from Device/ManufacturingMachine
        # attribute updates (one entity per device+controlledProperty, overwritten in
        # place — see entity-manager/blueprints/measurements.py). This is the canonical
        # route a sensor reading takes to reach TimescaleDB; notification_handler.py has a
        # dedicated branch to read its inverted shape (device id in refDevice, measurement
        # name in controlledProperty, value in numValue/textValue, instant in dateObserved).
        "description": "Telemetry Worker - DeviceMeasurement readings",
        "type": "Subscription",
        "entities": [{"type": "DeviceMeasurement"}],
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
    {
        "description": "Telemetry Worker - CropHealthAssessment updates",
        "type": "Subscription",
        "entities": [{"type": "CropHealthAssessment"}],
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
    {
        "description": "Telemetry Worker - WeatherObserved virtual stations",
        "type": "Subscription",
        "entities": [{"type": "WeatherObserved"}],
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
        "description": "Telemetry Worker - WeatherAlert updates",
        "type": "Subscription",
        "entities": [{"type": "WeatherAlert"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "throttling": 10,
        "isActive": True,
    },
    {
        "description": "Telemetry Worker - AgriSoil water budget attributes",
        "type": "Subscription",
        "entities": [{"type": "AgriSoil"}],
        "watchedAttributes": ["currentMoisture", "deficitMm", "fieldCapacity", "awc"],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
            "attributes": [
                "currentMoisture",
                "deficitMm",
                "fieldCapacity",
                "awc",
                "wiltingPoint",
                "forecast7d",
                "lastComputed"
            ],
        },
        "throttling": 5,
        "isActive": True,
    },
    {
        "description": "Telemetry Worker - Weather zonal stats (AgriParcelRecord)",
        "type": "Subscription",
        "entities": [{"type": "AgriParcelRecord"}],
        "watchedAttributes": ["eto", "solarRadiation", "soilMoistureVwc"],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
            "attributes": [
                "eto",
                "solarRadiation",
                "soilMoistureVwc",
                "waterBalance",
                "frostRisk",
                "airTemperatureAvg",
                "airTemperatureMin",
                "soilTemperature",
                "relativeHumidity",
            ],
        },
        "throttling": 5,
        "isActive": True,
    },
]


def _make_headers(tenant_id: str) -> dict:
    """Build Orion-LD headers — tenant sent AS-IS (canonical is hyphenated)."""
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)


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


# Orion-LD serves subscription listings one page at a time and falls back to a
# page of 20 when no limit is given. A dedup built on a single unpaginated page
# stops recognising its own subscriptions once a tenant holds more than a page
# of them, and recreates them on every run.
ORION_PAGE_SIZE = 1000


def _fetch_all_subscriptions(headers: dict) -> list:
    """Return every subscription of a tenant, following Orion's pagination."""
    subs: list = []
    offset = 0
    while True:
        response = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/subscriptions",
            headers=headers,
            params={"limit": ORION_PAGE_SIZE, "offset": offset},
            timeout=30,
        )
        response.raise_for_status()
        page = response.json() or []
        subs.extend(page)
        if len(page) < ORION_PAGE_SIZE:
            return subs
        offset += ORION_PAGE_SIZE


def _cleanup_broken_subscriptions(tenant_id: str):
    """Delete subscriptions with wrong port (legacy bug)."""
    headers = _make_headers(tenant_id)
    try:
        for sub in _fetch_all_subscriptions(headers):
            uri = sub.get("notification", {}).get("endpoint", {}).get("uri", "")
            if ":8080" in uri and "telemetry-worker" in uri:
                sub_id = sub.get("id")
                requests.delete(
                    f"{ORION_URL}/ngsi-ld/v1/subscriptions/{sub_id}",
                    headers=headers,
                    timeout=30,
                )
                logger.info(f"Deleted broken subscription {sub_id} (port 8080)")
    except Exception as e:
        logger.warning(f"Error cleaning broken subscriptions for {tenant_id}: {e}")


def _ensure_tenant_subscriptions(tenant_id: str):
    """Create missing NGSI-LD subscriptions for a single tenant."""
    headers = _make_headers(tenant_id)
    headers["Content-Type"] = "application/json"  # needed for POST below
    try:
        existing_subs = _fetch_all_subscriptions(headers)
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
                    timeout=30,
                )
                if res.status_code in [200, 201]:
                    logger.info(f"Created: {sub['description']} for {tenant_id}")
                else:
                    SUBSCRIPTION_CREATION_FAILED.labels(
                        tenant_id=tenant_id, reason=f"http_{res.status_code}"
                    ).inc()
                    logger.error(
                        f"Failed: {sub['description']} for {tenant_id}: "
                        f"{res.status_code} {res.text}"
                    )
    except Exception as e:
        SUBSCRIPTION_CREATION_FAILED.labels(
            tenant_id=tenant_id, reason="exception"
        ).inc()
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
