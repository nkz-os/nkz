# tests/test_subscriptions.py
"""Unit tests for SubscriptionRegistrar — delegates to OrionClient."""

import asyncio
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


def _one_sub_registrar() -> SubscriptionRegistrar:
    return SubscriptionRegistrar(
        orion_url=ORION,
        notification_url=NOTIFY,
        subscriptions=[{"type": "EOProduct", "throttling": 30}],
        module_name="crop-health",
    )


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_creates_missing_subscriptions_with_deterministic_id():
    get_route = respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(200, json=[])
    )
    post = respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(201, headers={"Location": "/ngsi-ld/v1/subscriptions/urn:x"})
    )
    result = await make_registrar().ensure_all(["montiko"])
    assert result == {"created": 2, "skipped": 0, "errors": []}
    assert len(post.calls) == 2

    ids = {json.loads(c.request.content)["id"] for c in post.calls}
    assert ids == {
        "urn:ngsi-ld:Subscription:crop-health:EOProduct",
        "urn:ngsi-ld:Subscription:crop-health:WeatherObserved",
    }
    # a 201 triggers the legacy-duplicate sweep
    assert get_route.calls


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_counts_409_as_skipped_without_error():
    respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(409, json={"type": "AlreadyExists", "title": "duplicate"})
    )
    result = await _one_sub_registrar().ensure_all(["montiko"])
    assert result == {"created": 0, "skipped": 1, "errors": []}


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_run_twice_creates_once_then_skips():
    """Same registrar, two runs: first process wins (201), second collides (409)."""
    respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(return_value=Response(200, json=[]))
    respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        side_effect=[
            Response(201, headers={"Location": "/x"}),
            Response(409, json={"title": "duplicate"}),
        ]
    )
    registrar = _one_sub_registrar()
    first = await registrar.ensure_all(["montiko"])
    second = await registrar.ensure_all(["montiko"])
    assert first == {"created": 1, "skipped": 0, "errors": []}
    assert second == {"created": 0, "skipped": 1, "errors": []}


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_purges_legacy_duplicate_after_create():
    legacy = [{
        "id": "urn:ngsi-ld:Subscription:legacy-random-uuid",
        "type": "Subscription",
        "description": "nkz-module: EOProduct -> crop-health",
    }]
    respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(200, json=legacy)
    )
    respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(201, headers={"Location": "/x"})
    )
    delete_route = respx.delete(
        f"{ORION}/ngsi-ld/v1/subscriptions/urn:ngsi-ld:Subscription:legacy-random-uuid"
    ).mock(return_value=Response(204))

    result = await _one_sub_registrar().ensure_all(["montiko"])

    assert result == {"created": 1, "skipped": 0, "errors": []}
    assert len(delete_route.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_409_does_not_list_or_delete():
    get_route = respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(200, json=[])
    )
    delete_route = respx.delete(
        f"{ORION}/ngsi-ld/v1/subscriptions/urn:ngsi-ld:Subscription:legacy"
    ).mock(return_value=Response(204))
    respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(409, json={"title": "duplicate"})
    )

    result = await _one_sub_registrar().ensure_all(["montiko"])

    assert result == {"created": 0, "skipped": 1, "errors": []}
    assert not get_route.calls, "409 must not trigger the legacy-duplicate listing"
    assert not delete_route.calls, "409 must not trigger any delete"


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_collects_errors_without_raising():
    sub_id = "urn:ngsi-ld:Subscription:crop-health:EOProduct"
    respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(500, text="boom")
    )
    # a non-409 failure is reconciled against the broker before being
    # trusted as a real error -- confirm it genuinely doesn't exist
    respx.get(f"{ORION}/ngsi-ld/v1/subscriptions/{sub_id}").mock(
        return_value=Response(404, json={"title": "not found"})
    )
    result = await _one_sub_registrar().ensure_all(["montiko"])
    assert result["created"] == 0
    assert result["skipped"] == 0
    assert len(result["errors"]) == 1


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_reconciles_500_as_skip_when_subscription_actually_exists():
    """Verified against the live broker: under real concurrency Orion
    intermittently answers 500 (not 409) when it loses the create race --
    the "exactly one 201" invariant still held every round, only the
    losers' status code was sometimes wrong. A 500 must not be trusted at
    face value: confirm existence with the broker before counting it as a
    real failure.
    """
    sub_id = "urn:ngsi-ld:Subscription:crop-health:EOProduct"
    respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(500, text="boom")
    )
    get_one = respx.get(f"{ORION}/ngsi-ld/v1/subscriptions/{sub_id}").mock(
        return_value=Response(
            200,
            json={"id": sub_id, "description": "nkz-module: EOProduct -> crop-health"},
        )
    )

    result = await _one_sub_registrar().ensure_all(["montiko"])

    assert result == {"created": 0, "skipped": 1, "errors": []}
    assert len(get_one.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_500_then_missing_is_a_real_error_naming_original_status():
    sub_id = "urn:ngsi-ld:Subscription:crop-health:EOProduct"
    respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(500, text="boom")
    )
    respx.get(f"{ORION}/ngsi-ld/v1/subscriptions/{sub_id}").mock(
        return_value=Response(404, json={"title": "not found"})
    )

    result = await _one_sub_registrar().ensure_all(["montiko"])

    assert result["created"] == 0
    assert result["skipped"] == 0
    assert len(result["errors"]) == 1
    assert "500" in result["errors"][0]


@pytest.mark.asyncio
@respx.mock
async def test_ensure_all_409_skips_the_existence_check():
    """A 409 is unambiguous -- it must not cost an extra round trip."""
    sub_id = "urn:ngsi-ld:Subscription:crop-health:EOProduct"
    respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(409, json={"title": "duplicate"})
    )
    get_one = respx.get(f"{ORION}/ngsi-ld/v1/subscriptions/{sub_id}").mock(
        return_value=Response(200, json={"id": sub_id})
    )

    result = await _one_sub_registrar().ensure_all(["montiko"])

    assert result == {"created": 0, "skipped": 1, "errors": []}
    assert not get_one.calls


@pytest.mark.asyncio
@respx.mock
async def test_subscription_body_and_headers_are_ngsild_compliant():
    respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(200, json=[])
    )
    post = respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(201, headers={"Location": "/x"})
    )
    await _one_sub_registrar().ensure_all(["montiko"])

    req = post.calls[0].request
    # Legal NGSI-LD combination: application/json + Link, no @context in body
    assert req.headers["Content-Type"] == "application/json"
    assert "Link" in req.headers
    assert req.headers["NGSILD-Tenant"] == "montiko"
    body = json.loads(req.content)
    assert "@context" not in body
    assert body["id"] == "urn:ngsi-ld:Subscription:crop-health:EOProduct"
    assert body["description"] == "nkz-module: EOProduct -> crop-health"
    assert body["entities"] == [{"type": "EOProduct"}]
    assert body["throttling"] == 30
    assert body["notification"]["endpoint"]["uri"] == NOTIFY


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


def test_subscription_id_is_deterministic_and_stable():
    reg = _registrar()
    sub = SubscriptionDef(type="AgriCrop")
    assert reg._subscription_id(sub) == "urn:ngsi-ld:Subscription:bioorchestrator:AgriCrop"
    # same input -> same id, every time
    assert reg._subscription_id(sub) == reg._subscription_id(sub)


def test_subscription_id_sanitises_invalid_urn_characters():
    reg = SubscriptionRegistrar(
        orion_url="http://orion:1026",
        notification_url="http://svc/notify",
        subscriptions=[],
        module_name="weird module/name!",
    )
    sub_id = reg._subscription_id(SubscriptionDef(type="Some Type#With/Slashes"))
    assert sub_id == "urn:ngsi-ld:Subscription:weird-module-name-:Some-Type-With-Slashes"


@pytest.mark.asyncio
@respx.mock
async def test_legacy_purge_finds_duplicate_beyond_the_first_page():
    """The legacy-duplicate sweep must use the paginating listing, not a
    single-page read, or a legacy duplicate sitting past page 1 survives
    convergence forever.
    """
    from nkz_platform_sdk.orion import ORION_PAGE_SIZE

    filler = [
        {"id": f"urn:ngsi-ld:Subscription:filler-{i}", "description": f"unrelated-{i}"}
        for i in range(ORION_PAGE_SIZE)
    ]
    legacy = {
        "id": "urn:ngsi-ld:Subscription:legacy-random-uuid",
        "description": "nkz-module: EOProduct -> crop-health",
    }
    respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        side_effect=[
            Response(200, json=filler),    # page 1, full page
            Response(200, json=[legacy]),  # page 2, short page, stop
        ]
    )
    respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(201, headers={"Location": "/x"})
    )
    delete_route = respx.delete(
        f"{ORION}/ngsi-ld/v1/subscriptions/urn:ngsi-ld:Subscription:legacy-random-uuid"
    ).mock(return_value=Response(204))

    result = await _one_sub_registrar().ensure_all(["montiko"])

    assert result == {"created": 1, "skipped": 0, "errors": []}
    assert len(delete_route.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_legacy_purge_ignores_404_on_delete():
    """A 404 on the legacy delete means someone else already removed it —
    not an error."""
    legacy = [{
        "id": "urn:ngsi-ld:Subscription:legacy-random-uuid",
        "description": "nkz-module: EOProduct -> crop-health",
    }]
    respx.get(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(200, json=legacy)
    )
    respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(201, headers={"Location": "/x"})
    )
    respx.delete(
        f"{ORION}/ngsi-ld/v1/subscriptions/urn:ngsi-ld:Subscription:legacy-random-uuid"
    ).mock(return_value=Response(404, json={"title": "not found"}))

    result = await _one_sub_registrar().ensure_all(["montiko"])

    assert result == {"created": 1, "skipped": 0, "errors": []}


@pytest.mark.asyncio
@respx.mock
async def test_periodic_heal_never_raises_on_heal_cycle_failure(monkeypatch):
    """A failing ensure_all cycle (Orion returning errors) must not kill the loop."""
    respx.post(f"{ORION}/ngsi-ld/v1/subscriptions").mock(
        return_value=Response(500, text="boom")
    )
    sleep_calls = 0

    async def fake_sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    registrar = _one_sub_registrar()
    with pytest.raises(asyncio.CancelledError):
        await registrar.periodic_heal(["montiko"], interval_minutes=60)
    assert sleep_calls == 2
