"""Unit tests for ModuleActivation."""

import json

import pytest
import respx
from nkz_platform_sdk.activation import ModuleActivation

ORION_URL = "http://orion-ld-service:1026"
CONTEXT_URL = "http://api-gateway-service:5000/ngsi-ld-context.json"
TENANT = "montiko"
PARCEL_URN = "urn:ngsi-ld:AgriParcel:montiko:Montiko"


@pytest.mark.asyncio
@respx.mock
async def test_ensure_entities_creates_when_not_exists():
    activation = ModuleActivation(
        tenant_id=TENANT,
        orion_url=ORION_URL,
        context_url=CONTEXT_URL,
    )

    # Mock: POST succeeds for both entities
    post_route = respx.post(f"{ORION_URL}/ngsi-ld/v1/entities").respond(201)

    result = await activation.ensure_entities(
        parcel_id=PARCEL_URN,
        entities=[
            {"type": "AgriCrop", "id_suffix": "default"},
            {"type": "CropHealthAssessment", "id_suffix": "latest"},
        ],
    )

    assert result["created"] == 2
    assert result["skipped"] == 0
    assert result["errors"] == []
    assert len(post_route.calls) == 2


@pytest.mark.asyncio
@respx.mock
async def test_ensure_entities_skips_existing_409():
    activation = ModuleActivation(
        tenant_id=TENANT,
        orion_url=ORION_URL,
        context_url=CONTEXT_URL,
    )

    # 409 = already exists -> PATCH instead
    respx.post(f"{ORION_URL}/ngsi-ld/v1/entities").respond(409)
    patch_route = respx.patch().respond(204)

    result = await activation.ensure_entities(
        parcel_id=PARCEL_URN,
        entities=[{"type": "AgriCrop", "id_suffix": "default"}],
    )

    assert result["skipped"] == 1
    assert result["created"] == 0
    assert result["errors"] == []
    assert len(patch_route.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_ensure_entities_uses_has_agri_parcel_relationship():
    activation = ModuleActivation(
        tenant_id=TENANT,
        orion_url=ORION_URL,
        context_url=CONTEXT_URL,
    )

    post_route = respx.post(f"{ORION_URL}/ngsi-ld/v1/entities").respond(201)

    await activation.ensure_entities(
        parcel_id=PARCEL_URN,
        entities=[{"type": "AgriCrop", "id_suffix": "default"}],
    )

    call_body = json.loads(post_route.calls[0].request.content)
    assert "hasAgriParcel" in call_body
    assert call_body["hasAgriParcel"]["object"] == PARCEL_URN


@pytest.mark.asyncio
@respx.mock
async def test_ensure_entities_includes_context():
    activation = ModuleActivation(
        tenant_id=TENANT,
        orion_url=ORION_URL,
        context_url=CONTEXT_URL,
    )

    post_route = respx.post(f"{ORION_URL}/ngsi-ld/v1/entities").respond(201)

    await activation.ensure_entities(
        parcel_id=PARCEL_URN,
        entities=[{"type": "AgriCrop", "id_suffix": "default"}],
    )

    call_body = json.loads(post_route.calls[0].request.content)
    assert "@context" in call_body
    assert call_body["@context"] == CONTEXT_URL


@pytest.mark.asyncio
@respx.mock
async def test_get_status_reports_correctly():
    activation = ModuleActivation(
        tenant_id=TENANT,
        orion_url=ORION_URL,
        context_url=CONTEXT_URL,
    )

    # Mock: AgriCrop exists, CropHealthAssessment doesn't
    def query_response(request):
        url = str(request.url)
        if "AgriCrop" in url and "hasAgriParcel" in url:
            return respx.MockResponse(200, json=[{"id": "urn:ngsi-ld:AgriCrop:montiko:Montiko-default", "type": "AgriCrop"}])
        return respx.MockResponse(200, json=[])

    respx.get().mock(side_effect=query_response)

    status = await activation.get_status(PARCEL_URN)

    assert status["AgriCrop"] == "ok"
    assert status["CropHealthAssessment"] == "unavailable"
    assert status["EOProduct"] == "unavailable"


@pytest.mark.asyncio
@respx.mock
async def test_ensure_entities_agri_soil_has_data_source():
    activation = ModuleActivation(
        tenant_id=TENANT,
        orion_url=ORION_URL,
        context_url=CONTEXT_URL,
    )

    post_route = respx.post(f"{ORION_URL}/ngsi-ld/v1/entities").respond(201)

    await activation.ensure_entities(
        parcel_id=PARCEL_URN,
        entities=[{"type": "AgriSoil", "id_suffix": "summary"}],
    )

    call_body = json.loads(post_route.calls[0].request.content)
    assert call_body["type"] == "AgriSoil"
    assert call_body["dataSource"]["value"] == "pending_analysis"
