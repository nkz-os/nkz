"""Idempotent Orion subscription: AgriParcel -> entity-manager projection endpoint.

entity-manager does NOT import nkz-platform-sdk, so we use inject_fiware_headers
(the documented fallback) rather than SubscriptionRegistrar.
"""
import logging

import requests

from common.auth_middleware import inject_fiware_headers
from helpers import ORION_URL, CONTEXT_URL

logger = logging.getLogger(__name__)
_LINK = f'<{CONTEXT_URL}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'


def build_projection_subscription(endpoint_uri: str, secret: str) -> dict:
    # receiverInfo: Orion sends these headers on every notification → authenticates
    # the call to /internal/parcels/project (the endpoint is invoked BY Orion, not by us).
    return {
        "type": "Subscription",
        "entities": [{"type": "AgriParcel"}],
        "notification": {
            "endpoint": {
                "uri": endpoint_uri,
                "accept": "application/json",
                "receiverInfo": [{"key": "X-Internal-Service-Secret", "value": secret}],
            },
            "format": "normalized",
        },
        "description": "Project AgriParcel into cadastral_parcels read-model",
    }


def _list_subscriptions(tenant: str):
    headers = inject_fiware_headers({"Accept": "application/json", "Link": _LINK}, tenant)
    r = requests.get(f"{ORION_URL}/ngsi-ld/v1/subscriptions?limit=200", headers=headers, timeout=15)
    return (r.json() or []) if r.status_code == 200 else []


def _create_subscription(tenant: str, body: dict):
    headers = inject_fiware_headers({"Content-Type": "application/ld+json"}, tenant)
    body = dict(body)
    body["@context"] = CONTEXT_URL
    r = requests.post(f"{ORION_URL}/ngsi-ld/v1/subscriptions", json=body, headers=headers, timeout=15)
    if r.status_code not in (201, 409):
        logger.error("Subscription create failed (%s) tenant=%s", r.status_code, tenant)
    return r.status_code


def ensure_projection_subscription(tenant: str, endpoint_uri: str, secret: str):
    for s in _list_subscriptions(tenant):
        watches_parcel = any(e.get("type") == "AgriParcel" for e in s.get("entities", []))
        same_uri = s.get("notification", {}).get("endpoint", {}).get("uri") == endpoint_uri
        if watches_parcel and same_uri:
            return  # idempotent: already present
    _create_subscription(tenant, build_projection_subscription(endpoint_uri, secret))
