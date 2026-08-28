"""Unit tests for OrionClient subscription methods."""

import json

import pytest
import respx
from httpx import Response

from nkz_platform_sdk.orion import ORION_PAGE_SIZE, OrionClient

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


@pytest.mark.asyncio
@respx.mock
async def test_query_all_subscriptions_follows_pagination():
    """A capped listing is what makes a reconciler duplicate its own subscriptions.

    Orion-LD returns 20 subscriptions when `limit` is omitted and rejects
    limit > 1000, so "ask for a big number" is not a fix — the listing has
    to be followed to the end.
    """
    client = OrionClient("montiko", base_url=ORION)
    page1 = [{"id": f"urn:ngsi-ld:Subscription:{i}"} for i in range(ORION_PAGE_SIZE)]
    page2 = [{"id": "urn:ngsi-ld:Subscription:last"}]
    route = respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        side_effect=[Response(200, json=page1), Response(200, json=page2)]
    )
    subs = await client.query_all_subscriptions()
    assert len(subs) == ORION_PAGE_SIZE + 1
    assert subs[-1]["id"].endswith("last")
    assert len(route.calls) == 2
    assert route.calls[0].request.url.params["offset"] == "0"
    assert route.calls[1].request.url.params["offset"] == str(ORION_PAGE_SIZE)
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_query_all_subscriptions_stops_on_a_short_page():
    """A short page means the end — no speculative extra round-trip."""
    client = OrionClient("montiko", base_url=ORION)
    route = respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(200, json=[{"id": "urn:ngsi-ld:Subscription:1"}])
    )
    subs = await client.query_all_subscriptions()
    assert len(subs) == 1
    assert len(route.calls) == 1
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_query_all_subscriptions_sends_json_plus_link():
    client = OrionClient("montiko", base_url=ORION)
    route = respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(200, json=[])
    )
    await client.query_all_subscriptions()
    req = route.calls[0].request
    assert req.headers["NGSILD-Tenant"] == "montiko"
    assert req.headers["Content-Type"] == "application/json"
    assert "Link" in req.headers
    await client.close()
