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

from common.ngsi_headers import inject_fiware_headers
from common.subscription_health import reactivate_if_paused

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


INTERNAL_SERVICE_SECRET = os.getenv("INTERNAL_SERVICE_SECRET", "")
_SUB_ID_PREFIX = "risk-worker"


def _slugify(text: str) -> str:
    """'RiskAssessment evaluations' -> 'riskassessment-evaluations'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _subscription_id(sub_def: dict) -> str:
    suffix = sub_def.get("description", "").split(" - ", 1)[-1]
    return f"urn:ngsi-ld:Subscription:{_SUB_ID_PREFIX}:{_slugify(suffix)}"


def _subscription_body(sub_def: dict) -> dict:
    body = {**sub_def, "id": _subscription_id(sub_def)}
    if INTERNAL_SERVICE_SECRET:
        endpoint = {
            **sub_def["notification"]["endpoint"],
            "receiverInfo": [
                {"key": "X-Internal-Service-Secret", "value": INTERNAL_SERVICE_SECRET}
            ],
        }
        body["notification"] = {**sub_def["notification"], "endpoint": endpoint}
    return body


def _matches_are_stale(matches: list) -> bool:
    if not matches:
        return False
    if len(matches) > 1:
        return True
    if not INTERNAL_SERVICE_SECRET:
        return False
    endpoint = matches[0].get("notification", {}).get("endpoint", {})
    return not endpoint.get("receiverInfo")


def _delete_subscription(headers: dict, sub_id: str) -> None:
    if not sub_id:
        return
    try:
        res = requests.delete(
            f"{ORION_URL}/ngsi-ld/v1/subscriptions/{sub_id}", headers=headers, timeout=30
        )
        if res.status_code not in (200, 204, 404):
            logger.warning("Delete subscription %s: HTTP %s", sub_id, res.status_code)
    except requests.RequestException as e:
        logger.warning("Delete subscription %s failed: %s", sub_id, e)


def _create_subscription(headers: dict, sub_def: dict, tenant_id: str) -> None:
    res = requests.post(
        f"{ORION_URL}/ngsi-ld/v1/subscriptions",
        json=_subscription_body(sub_def),
        headers=headers,
        timeout=30,
    )
    if res.status_code in (200, 201, 409):  # 409 = concurrent create won
        logger.info("Subscription '%s' ensured for %s", sub_def["description"], tenant_id)
    else:
        logger.error(
            "Failed: %s for %s: %s %s",
            sub_def["description"], tenant_id, res.status_code, res.text[:200],
        )


def _make_headers(tenant_id: str) -> dict:
    """Build Orion-LD headers — delegates to canonical ngsi_headers."""
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)


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


ORION_PAGE_SIZE = 1000


def _fetch_all_subscriptions(headers: dict) -> list:
    """Return every subscription of a tenant, following Orion's pagination.

    Orion-LD returns 20 subscriptions when `limit` is omitted, so a single-page
    read hides the service's own subscriptions once a tenant has more than that
    — and the reconciler then re-creates them on every cycle, pushing the real
    ones further out of the window. Asking for one oversized page is not a fix
    either: Orion rejects limit > 1000.
    """
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


def _ensure_tenant_subscriptions(tenant_id: str):
    """Create missing NGSI-LD subscriptions for a single tenant."""
    headers = _make_headers(tenant_id)
    headers["Content-Type"] = "application/json"
    try:
        existing_subs = _fetch_all_subscriptions(headers) or []
        existing_by_description: dict = {}
        for existing in existing_subs:
            existing_by_description.setdefault(existing.get("description"), []).append(
                existing
            )

        for sub_def in SUBSCRIPTIONS:
            matches = existing_by_description.get(sub_def["description"], [])
            if _matches_are_stale(matches):
                logger.info(
                    "Replacing subscription '%s' for %s (%d existing)",
                    sub_def["description"], tenant_id, len(matches),
                )
                for existing in matches:
                    _delete_subscription(headers, existing.get("id"))
                _create_subscription(headers, sub_def, tenant_id)
            elif matches:
                # Existing is not the same as firing: Orion pauses a subscription
                # after 3 consecutive notification failures and never resumes it.
                for existing in matches:
                    reactivate_if_paused(ORION_URL, headers, existing, logger)
            else:
                _create_subscription(headers, sub_def, tenant_id)
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
