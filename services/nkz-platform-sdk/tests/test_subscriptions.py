# tests/test_subscriptions.py
"""Unit tests for SubscriptionRegistrar — delegates to OrionClient."""

import json

import pytest
import respx
from httpx import Response

from nkz_platform_sdk.subscriptions import SubscriptionDef, SubscriptionRegistrar

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


def _registrar():
    return SubscriptionRegistrar(
        orion_url="http://orion:1026",
        notification_url="http://svc/notify",
        subscriptions=[],
        module_name="bioorchestrator",
    )


def test_body_omits_watched_and_condition_when_unset():
    body = _registrar()._body(SubscriptionDef(type="AgriCrop"))
    assert "watchedAttributes" not in body
    assert "condition" not in body


def test_body_includes_watched_and_condition_when_set():
    sub = SubscriptionDef(
        type="CropHealthAssessment",
        watched_attributes=["phenologyStage"],
        condition={"attrs": ["phenologyStage"]},
    )
    body = _registrar()._body(sub)
    assert body["watchedAttributes"] == ["phenologyStage"]
    assert body["condition"] == {"attrs": ["phenologyStage"]}
    assert body["entities"] == [{"type": "CropHealthAssessment"}]


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_sees_subscriptions_beyond_the_first_page():
    """Runaway-duplication regression.

    When the registrar's own subscriptions sit past the first page of the
    listing, a single-page read reports them missing and re-creates them on
    every heal cycle. Each cycle pushes the real ones further out of the
    window, so the loop never recovers on its own.
    """
    from nkz_platform_sdk.orion import ORION_PAGE_SIZE

    filler = [
        {"id": f"urn:ngsi-ld:Subscription:{i}", "description": f"unrelated-{i}"}
        for i in range(ORION_PAGE_SIZE)
    ]
    mine = [
        {"id": "urn:ngsi-ld:Subscription:a", "description": "nkz-module: EOProduct -> crop-health"},
        {"id": "urn:ngsi-ld:Subscription:b", "description": "nkz-module: WeatherObserved -> crop-health"},
    ]
    respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        side_effect=[Response(200, json=filler), Response(200, json=mine)]
    )
    post = respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(201, headers={"Location": "/ngsi-ld/v1/subscriptions/urn:x"})
    )
    result = await make_registrar().ensure_all(["montiko"])
    assert result == {"created": 0, "skipped": 2, "errors": []}
    assert len(post.calls) == 0, "re-created subscriptions that already existed"
