# tests/test_activation.py
"""Unit tests for ModuleActivation — delegates to OrionClient."""

import json

import pytest
import respx
from httpx import Response

from nkz_platform_sdk.activation import ModuleActivation

ORION = "http://orion-ld-service:1026"
TENANT = "montiko"
PARCEL = "urn:ngsi-ld:AgriParcel:montiko:Montiko"


def make_activation() -> ModuleActivation:
    return ModuleActivation(tenant_id=TENANT, orion_url=ORION)


@pytest.mark.asyncio
@respx.mock
async def test_ensure_entities_creates_when_not_exists():
    post = respx.post(f"{ORION}/ngsi-ld/v1/entities").mock(
        return_value=Response(201)
    )
    activation = make_activation()
    result = await activation.ensure_entities(
        parcel_id=PARCEL,
        entities=[
            {"type": "AgriCrop", "id_suffix": "default"},
            {"type": "CropHealthAssessment", "id_suffix": "latest"},
        ],
    )
    await activation.close()
    assert result["created"] == 2
    assert result["skipped"] == 0
    assert result["errors"] == []
    assert len(post.calls) == 2
    # ld+json body with @context, NO Link header (legal combination)
    req = post.calls[0].request
    assert req.headers["Content-Type"] == "application/ld+json"
    assert "Link" not in req.headers
    body = json.loads(req.content)
    assert "@context" in body


@pytest.mark.asyncio
@respx.mock
async def test_ensure_entities_409_falls_back_to_patch():
    respx.post(f"{ORION}/ngsi-ld/v1/entities").mock(return_value=Response(409))
    patch = respx.patch(url__regex=rf"{ORION}/ngsi-ld/v1/entities/.*/attrs").mock(
        return_value=Response(204)
    )
    activation = make_activation()
    result = await activation.ensure_entities(
        parcel_id=PARCEL, entities=[{"type": "AgriCrop", "id_suffix": "default"}]
    )
    await activation.close()
    assert result == {
        "created": 0, "skipped": 1, "errors": [],
        "entity_ids": ["urn:ngsi-ld:AgriCrop:montiko:Montiko-default"],
    }
    assert len(patch.calls) == 1
    # PATCH /attrs must be the LEGAL combination: json + Link, no @context,
    # and must not refresh dateCreated on idempotent re-runs.
    preq = patch.calls[0].request
    assert preq.headers["Content-Type"] == "application/json"
    assert "Link" in preq.headers
    pbody = json.loads(preq.content)
    assert "@context" not in pbody
    assert "dateCreated" not in pbody


@pytest.mark.asyncio
@respx.mock
async def test_placeholder_has_relationship_and_no_fabricated_dates():
    post = respx.post(f"{ORION}/ngsi-ld/v1/entities").mock(return_value=Response(201))
    activation = make_activation()
    await activation.ensure_entities(
        parcel_id=PARCEL, entities=[{"type": "AgriCrop", "id_suffix": "default"}]
    )
    await activation.close()
    body = json.loads(post.calls[0].request.content)
    assert body["hasAgriParcel"]["object"] == PARCEL
    assert body["status"]["value"] == "pending"
    assert body["provenance"]["value"] == "placeholder"
    assert "plantingDate" not in body   # never invent agronomic data
    assert "harvestDate" not in body


@pytest.mark.asyncio
@respx.mock
async def test_get_status_checks_both_relationship_names():
    def responder(request):
        q = request.url.params.get("q", "")
        assert 'hasAgriParcel=="' in q and 'refAgriParcel=="' in q  # OR query
        if request.url.params.get("type") == "AgriCrop":
            return Response(200, json=[{"id": "urn:x", "type": "AgriCrop"}])
        return Response(200, json=[])

    respx.get(f"{ORION}/ngsi-ld/v1/entities").mock(side_effect=responder)
    activation = make_activation()
    status = await activation.get_status(
        PARCEL, entity_types=["AgriCrop", "CropHealthAssessment"]
    )
    await activation.close()
    assert status == {"AgriCrop": "ok", "CropHealthAssessment": "unavailable"}
