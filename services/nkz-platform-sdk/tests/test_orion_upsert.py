"""Unit tests for OrionClient.upsert_entities_batch (entityOperations/upsert)."""

import json

import httpx
import pytest
import respx
from httpx import Response

from nkz_platform_sdk.orion import OrionClient

ORION = "http://orion-ld-service:1026"
UPSERT = f"{ORION}/ngsi-ld/v1/entityOperations/upsert"


def _crop(uri: str) -> dict:
    return {"id": uri, "type": "AgriCrop",
            "name": {"type": "Property", "value": uri.split(":")[-1]}}


@pytest.mark.asyncio
@respx.mock
async def test_upsert_uses_update_option_and_ldjson_with_context():
    client = OrionClient("default", base_url=ORION)
    route = respx.post(UPSERT).mock(return_value=Response(204))
    res = await client.upsert_entities_batch([_crop("urn:ngsi-ld:AgriCrop:wheat")])
    req = route.calls[0].request
    assert "options=update" in str(req.url)
    assert req.headers["Content-Type"] == "application/ld+json"
    assert req.headers["NGSILD-Tenant"] == "default"
    # @context injected per entity
    assert json.loads(req.content)[0]["@context"]
    assert res == {"upserted": 1, "errors": [], "entity_ids": ["urn:ngsi-ld:AgriCrop:wheat"]}
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_upsert_empty_is_noop():
    client = OrionClient("default", base_url=ORION)
    route = respx.post(UPSERT).mock(return_value=Response(204))
    res = await client.upsert_entities_batch([])
    assert res == {"upserted": 0, "errors": [], "entity_ids": []}
    assert not route.called
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_upsert_207_partial_reports_errors():
    client = OrionClient("default", base_url=ORION)
    respx.post(UPSERT).mock(return_value=Response(
        207, json={"success": ["urn:ngsi-ld:AgriCrop:a"],
                   "errors": [{"entityId": "urn:ngsi-ld:AgriCrop:b", "error": {"title": "bad"}}]}))
    res = await client.upsert_entities_batch([_crop("urn:ngsi-ld:AgriCrop:a"),
                                              _crop("urn:ngsi-ld:AgriCrop:b")])
    assert res["upserted"] == 1
    assert len(res["errors"]) == 1
    assert res["entity_ids"] == ["urn:ngsi-ld:AgriCrop:a"]
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_upsert_raises_on_4xx():
    client = OrionClient("default", base_url=ORION)
    respx.post(UPSERT).mock(return_value=Response(400, text="bad request"))
    with pytest.raises(httpx.HTTPStatusError):
        await client.upsert_entities_batch([_crop("urn:ngsi-ld:AgriCrop:a")])
    await client.close()
