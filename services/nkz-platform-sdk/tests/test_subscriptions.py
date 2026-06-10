"""Unit tests for SubscriptionRegistrar."""

import pytest
import respx
from nkz_platform_sdk.subscriptions import SubscriptionRegistrar

ORION_URL = "http://orion-ld-service:1026"
NOTIFY_URL = "http://crop-health-backend:8000/api/crop-health/webhooks/orion"
CONTEXT_URL = "http://api-gateway-service:5000/ngsi-ld-context.json"

SUBSCRIPTIONS_DEF = [
    {"type": "EOProduct", "throttling": 30},
    {"type": "WeatherObserved", "throttling": 60},
]


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_creates_missing_subscriptions():
    registrar = SubscriptionRegistrar(
        orion_url=ORION_URL,
        notification_url=NOTIFY_URL,
        context_url=CONTEXT_URL,
        subscriptions=SUBSCRIPTIONS_DEF,
        module_name="crop-health",
    )

    # Mock: no existing subscriptions
    respx.get(f"{ORION_URL}/ngsi-ld/v1/subscriptions").respond(
        200, json=[]
    )
    # Mock: POST returns 201 for each
    post_route = respx.post(f"{ORION_URL}/ngsi-ld/v1/subscriptions").respond(201)

    result = await registrar.ensure_all(["montiko"])

    assert result["created"] == 2
    assert result["skipped"] == 0
    assert result["errors"] == []
    assert len(post_route.calls) == 2


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_skips_existing_by_description():
    registrar = SubscriptionRegistrar(
        orion_url=ORION_URL,
        notification_url=NOTIFY_URL,
        context_url=CONTEXT_URL,
        subscriptions=SUBSCRIPTIONS_DEF,
        module_name="crop-health",
    )

    # Mock: EOProduct subscription already exists
    existing = [
        {
            "id": "urn:ngsi-ld:Subscription:existing-1",
            "description": "nkz-module: EOProduct -> crop-health",
            "type": "Subscription",
        }
    ]
    respx.get(f"{ORION_URL}/ngsi-ld/v1/subscriptions").respond(200, json=existing)
    post_route = respx.post(f"{ORION_URL}/ngsi-ld/v1/subscriptions").respond(201)

    result = await registrar.ensure_all(["montiko"])

    assert result["created"] == 1  # only WeatherObserved
    assert result["skipped"] == 1  # EOProduct already exists
    assert result["errors"] == []
    assert len(post_route.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_uses_correct_headers():
    registrar = SubscriptionRegistrar(
        orion_url=ORION_URL,
        notification_url=NOTIFY_URL,
        context_url=CONTEXT_URL,
        subscriptions=[{"type": "EOProduct", "throttling": 30}],
        module_name="crop-health",
    )

    respx.get(f"{ORION_URL}/ngsi-ld/v1/subscriptions").respond(200, json=[])
    post_route = respx.post(f"{ORION_URL}/ngsi-ld/v1/subscriptions").respond(201)

    await registrar.ensure_all(["montiko"])

    call = post_route.calls[0]
    assert call.request.headers["NGSILD-Tenant"] == "montiko"
    assert call.request.headers["Fiware-Service"] == "montiko"
    assert "Link" in call.request.headers
    body = call.request.content  # respx stores content as bytes
    import json
    parsed = json.loads(body)
    assert parsed["description"] == "nkz-module: EOProduct -> crop-health"
    assert parsed["entities"] == [{"type": "EOProduct"}]
    assert parsed["throttling"] == 30


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_multi_tenant():
    registrar = SubscriptionRegistrar(
        orion_url=ORION_URL,
        notification_url=NOTIFY_URL,
        context_url=CONTEXT_URL,
        subscriptions=[{"type": "EOProduct", "throttling": 30}],
        module_name="crop-health",
    )

    respx.get(f"{ORION_URL}/ngsi-ld/v1/subscriptions").respond(200, json=[])
    post_route = respx.post(f"{ORION_URL}/ngsi-ld/v1/subscriptions").respond(201)

    result = await registrar.ensure_all(["montiko", "platform"])

    assert result["created"] == 2  # one per tenant
    assert result["skipped"] == 0
    assert len(post_route.calls) == 2
