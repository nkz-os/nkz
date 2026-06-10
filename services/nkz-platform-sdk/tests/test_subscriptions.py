# tests/test_subscriptions.py
"""Unit tests for SubscriptionRegistrar — delegates to OrionClient."""

import json

import pytest
import respx
from httpx import Response

from nkz_platform_sdk.subscriptions import SubscriptionRegistrar

ORION = "http://orion-ld-service:1026"
NOTIFY = "http://crop-health-api-service:8000/api/crop-health/webhooks/orion"

SUBS = [
    {"type": "EOProduct", "throttling": 30},
    {"type": "WeatherObserved", "throttling": 60},
]


def make_registrar() -> SubscriptionRegistrar:
    return SubscriptionRegistrar(
        orion_url=ORION,
        notification_url=NOTIFY,
        subscriptions=SUBS,
        module_name="crop-health",
    )


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_creates_missing_subscriptions():
    respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(200, json=[])
    )
    post = respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(201, headers={"Location": "/ngsi-ld/v1/subscriptions/urn:x"})
    )
    result = await make_registrar().ensure_all(["montiko"])
    assert result == {"created": 2, "skipped": 0, "errors": []}
    assert len(post.calls) == 2


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_skips_existing_by_description():
    existing = [{
        "id": "urn:ngsi-ld:Subscription:existing-1",
        "type": "Subscription",
        "description": "nkz-module: EOProduct -> crop-health",
    }]
    respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(200, json=existing)
    )
    post = respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(201, headers={"Location": "/x"})
    )
    result = await make_registrar().ensure_all(["montiko"])
    assert result["created"] == 1   # only WeatherObserved
    assert result["skipped"] == 1
    assert len(post.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_subscription_body_and_headers_are_ngsild_compliant():
    respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(200, json=[])
    )
    post = respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(201, headers={"Location": "/x"})
    )
    await SubscriptionRegistrar(
        orion_url=ORION, notification_url=NOTIFY,
        subscriptions=[{"type": "EOProduct", "throttling": 30}],
        module_name="crop-health",
    ).ensure_all(["montiko"])

    req = post.calls[0].request
    # Legal NGSI-LD combination: application/json + Link, no @context in body
    assert req.headers["Content-Type"] == "application/json"
    assert "Link" in req.headers
    assert req.headers["NGSILD-Tenant"] == "montiko"
    body = json.loads(req.content)
    assert "@context" not in body
    assert body["description"] == "nkz-module: EOProduct -> crop-health"
    assert body["entities"] == [{"type": "EOProduct"}]
    assert body["throttling"] == 30
    assert body["notification"]["endpoint"]["uri"] == NOTIFY


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_collects_errors_without_raising():
    respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(500, text="boom")
    )
    result = await make_registrar().ensure_all(["montiko"])
    assert result["created"] == 0
    assert len(result["errors"]) == 1
