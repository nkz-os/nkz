"""Unit tests for OrionClient.append_entity_attrs (POST /attrs semantics)."""

import json

import pytest
import respx
from httpx import Response

from nkz_platform_sdk.orion import OrionClient

ORION = "http://orion-ld-service:1026"
EID = "urn:ngsi-ld:AgriEnergyTracker:t1"


@pytest.mark.asyncio
@respx.mock
async def test_append_uses_post_json_plus_link_tenant_as_is():
    client = OrionClient("asociacion-allotarra", base_url=ORION)
    route = respx.post(f"{ORION}/ngsi-ld/v1/entities/{EID}/attrs").mock(
        return_value=Response(204)
    )
    await client.append_entity_attrs(
        EID, {"targetTilt": {"type": "Property", "value": 30.0}}
    )
    req = route.calls[0].request
    # Tenant must travel AS-IS (hyphen-canonical), never normalized
    assert req.headers["NGSILD-Tenant"] == "asociacion-allotarra"
    assert req.headers["Fiware-Service"] == "asociacion-allotarra"
    # Fragment rule: application/json + Link, no @context in body
    assert req.headers["Content-Type"] == "application/json"
    assert "Link" in req.headers
    assert "@context" not in json.loads(req.content)
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_append_no_overwrite_option():
    client = OrionClient("montiko", base_url=ORION)
    route = respx.post(f"{ORION}/ngsi-ld/v1/entities/{EID}/attrs").mock(
        return_value=Response(204)
    )
    await client.append_entity_attrs(
        EID, {"tilt": {"type": "Property", "value": 1.0}}, overwrite=False
    )
    assert "options=noOverwrite" in str(route.calls[0].request.url)
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_append_raises_on_error():
    client = OrionClient("montiko", base_url=ORION)
    respx.post(f"{ORION}/ngsi-ld/v1/entities/{EID}/attrs").mock(
        return_value=Response(400)
    )
    import httpx

    with pytest.raises(httpx.HTTPStatusError):
        await client.append_entity_attrs(
            EID, {"tilt": {"type": "Property", "value": 1.0}}
        )
    await client.close()
