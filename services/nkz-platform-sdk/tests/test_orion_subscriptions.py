"""Unit tests for OrionClient subscription methods."""

import json

import pytest
import respx
from httpx import Response

from nkz_platform_sdk.orion import OrionClient

ORION = "http://orion-ld-service:1026"


@pytest.mark.asyncio
@respx.mock
async def test_query_subscriptions_uses_json_plus_link():
    client = OrionClient("montiko", base_url=ORION)
    route = respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(200, json=[{"id": "urn:ngsi-ld:Subscription:1"}])
    )
    subs = await client.query_subscriptions()
    assert subs[0]["id"] == "urn:ngsi-ld:Subscription:1"
    req = route.calls[0].request
    assert req.headers["NGSILD-Tenant"] == "montiko"
    assert req.headers["Content-Type"] == "application/json"
    assert "Link" in req.headers  # json + Link is the legal combination
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_create_subscription_json_body_no_context():
    client = OrionClient("montiko", base_url=ORION)
    route = respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(201, headers={"Location": "/ngsi-ld/v1/subscriptions/urn:x"})
    )
    sub_id = await client.create_subscription(
        {"type": "Subscription", "entities": [{"type": "EOProduct"}]}
    )
    assert sub_id.endswith("urn:x")
    req = route.calls[0].request
    body = json.loads(req.content)
    assert "@context" not in body  # context travels in Link header
    assert req.headers["Content-Type"] == "application/json"
    assert "Link" in req.headers
    await client.close()
