"""delete_entity_attr — remove one attribute, resolved through the platform @context."""

from unittest.mock import MagicMock

import httpx
import pytest
import respx
from httpx import Response

from nkz_platform_sdk.orion import OrionClient, SyncOrionClient

ORION = "http://orion-ld-service:1026"
CTX = "http://api-gateway-service:5000/ngsi-ld-context.json"
URL = f"{ORION}/ngsi-ld/v1/entities/urn:ngsi-ld:AgriParcel:p1/attrs/hasAgriCrop"


@pytest.mark.asyncio
@respx.mock
async def test_async_delete_sends_the_context_link():
    route = respx.delete(URL).mock(return_value=Response(204))
    client = OrionClient("tenant-a", base_url=ORION, context_url=CTX)
    try:
        await client.delete_entity_attr("urn:ngsi-ld:AgriParcel:p1", "hasAgriCrop")
    finally:
        await client.close()
    headers = route.calls[0].request.headers
    assert CTX in headers["Link"] and headers["NGSILD-Tenant"] == "tenant-a"


@pytest.mark.asyncio
@respx.mock
async def test_async_delete_absent_attr_raises_404():
    respx.delete(URL).mock(return_value=Response(404))
    client = OrionClient("tenant-a", base_url=ORION, context_url=CTX)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.delete_entity_attr("urn:ngsi-ld:AgriParcel:p1", "hasAgriCrop")
    finally:
        await client.close()


def test_sync_delete_sends_the_context_link():
    client = SyncOrionClient("tenant-a", base_url=ORION, context_url=CTX)
    resp = MagicMock(status_code=204)
    client._session.delete = MagicMock(return_value=resp)
    client.delete_entity_attr("urn:ngsi-ld:AgriParcel:p1", "hasAgriCrop")
    args, kwargs = client._session.delete.call_args
    assert args[0] == URL
    assert CTX in kwargs["headers"]["Link"] and kwargs["headers"]["NGSILD-Tenant"] == "tenant-a"
    resp.raise_for_status.assert_called_once()
