"""OrionClient query_entities/get_entity honor the `options` param (e.g. keyValues)."""

import pytest
import respx
from httpx import Response

from nkz_platform_sdk.orion import OrionClient

ORION = "http://orion-ld-service:1026"
ENTITIES = f"{ORION}/ngsi-ld/v1/entities"


@pytest.mark.asyncio
@respx.mock
async def test_query_entities_appends_options():
    client = OrionClient("default", base_url=ORION)
    route = respx.get(ENTITIES).mock(return_value=Response(200, json=[]))
    await client.query_entities(type="AgriCrop", options="keyValues")
    assert "options=keyValues" in str(route.calls[0].request.url)
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_query_entities_omits_options_when_none():
    client = OrionClient("default", base_url=ORION)
    route = respx.get(ENTITIES).mock(return_value=Response(200, json=[]))
    await client.query_entities(type="AgriCrop")
    assert "options=" not in str(route.calls[0].request.url)
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_get_entity_appends_options():
    client = OrionClient("default", base_url=ORION)
    uri = "urn:ngsi-ld:AgriParcel:P1"
    route = respx.get(f"{ENTITIES}/{uri}").mock(return_value=Response(200, json={"id": uri}))
    await client.get_entity(uri, options="keyValues")
    assert "options=keyValues" in str(route.calls[0].request.url)
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_get_entity_omits_options_when_none():
    client = OrionClient("default", base_url=ORION)
    uri = "urn:ngsi-ld:AgriParcel:P1"
    route = respx.get(f"{ENTITIES}/{uri}").mock(return_value=Response(200, json={"id": uri}))
    await client.get_entity(uri)
    assert "options=" not in str(route.calls[0].request.url)
    await client.close()
