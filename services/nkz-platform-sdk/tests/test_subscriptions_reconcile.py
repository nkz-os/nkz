"""remove_all / reconcile_installations — remove only what is ours, never on error."""

import asyncio

import pytest
import respx
from httpx import Response

from nkz_platform_sdk.subscriptions import SubscriptionRegistrar

ORION = "http://orion-ld-service:1026"
EM = "http://em:5000"
SUBS_URL = f"{ORION}/ngsi-ld/v1/subscriptions"
NOTIFY = "http://bio-api:8420/api/graph/internal/phenology-update"
PHENOLOGY_ID = "urn:ngsi-ld:Subscription:bioorchestrator:CropHealthAssessment"


def phenology() -> SubscriptionRegistrar:
    return SubscriptionRegistrar(
        orion_url=ORION, notification_url=NOTIFY, module_name="bioorchestrator",
        subscriptions=[{"type": "CropHealthAssessment", "throttling": None,
                        "watched_attributes": ["phenologyStage"]}],
    )


def catalog() -> SubscriptionRegistrar:
    return SubscriptionRegistrar(
        orion_url=ORION, notification_url=NOTIFY, module_name="bioorchestrator",
        subscriptions=[{"type": "AgriCrop", "throttling": None}],
    )


def _listing(rows):
    """Subscriptions as Orion lists them: (id, description, entities)."""
    return [{"id": i, "description": d, "entities": e} for i, d, e in rows]


def _deleted_ids(route):
    return [c.request.url.path.rsplit("/", 1)[-1] for c in route.calls]


@pytest.mark.asyncio
@respx.mock
async def test_remove_all_deletes_deterministic_id_and_counts_404():
    reg = phenology()
    respx.get(SUBS_URL).mock(return_value=Response(200, json=[]))
    delete = respx.delete(f"{SUBS_URL}/{PHENOLOGY_ID}").mock(
        side_effect=[Response(204), Response(404)]
    )
    first = await reg.remove_all(["montiko"])
    second = await reg.remove_all(["montiko"])
    assert first == {"removed": 1, "absent": 0, "errors": []}
    assert second == {"removed": 0, "absent": 1, "errors": []}
    assert delete.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_remove_all_leaves_other_registrar_and_other_module_alone():
    reg, cat = phenology(), catalog()
    own = reg._description(reg._subs[0])
    respx.get(SUBS_URL).mock(return_value=Response(200, json=_listing([
        ("urn:legacy:1", own, [{"type": "CropHealthAssessment"}]),
        ("urn:ngsi-ld:Subscription:bioorchestrator:AgriCrop",
         cat._description(cat._subs[0]), [{"type": "AgriCrop"}]),
        ("urn:ngsi-ld:Subscription:telemetry-worker:crophealthassessment-updates",
         "Telemetry Worker - CropHealthAssessment updates", [{"type": "CropHealthAssessment"}]),
        ("urn:hand-edited", own, [{"type": "AgriParcel"}]),
    ])))
    delete = respx.delete(url__startswith=f"{SUBS_URL}/").mock(return_value=Response(204))
    result = await reg.remove_all(["montiko"])
    assert sorted(_deleted_ids(delete)) == sorted([PHENOLOGY_ID, "urn:legacy:1"])
    assert result["errors"] == []


@pytest.mark.asyncio
@respx.mock
async def test_legacy_match_survives_unwrapped_and_expanded_type():
    reg = phenology()
    own = reg._description(reg._subs[0])
    respx.get(SUBS_URL).mock(return_value=Response(200, json=_listing([
        ("urn:legacy:obj", own, {"type": "CropHealthAssessment"}),
        ("urn:legacy:uri", own,
         [{"type": "https://uri.etsi.org/ngsi-ld/default-context/CropHealthAssessment"}]),
    ])))
    delete = respx.delete(url__startswith=f"{SUBS_URL}/").mock(return_value=Response(204))
    await reg.remove_all(["montiko"])
    assert {"urn:legacy:obj", "urn:legacy:uri"} <= set(_deleted_ids(delete))


@pytest.mark.asyncio
@respx.mock
async def test_listing_failure_still_removes_deterministic_id():
    reg = phenology()
    respx.get(SUBS_URL).mock(return_value=Response(500))
    delete = respx.delete(url__startswith=f"{SUBS_URL}/").mock(return_value=Response(204))
    result = await reg.remove_all(["montiko"])
    assert _deleted_ids(delete) == [PHENOLOGY_ID]
    assert result["removed"] == 1 and len(result["errors"]) == 1


def _em(states, status=200):
    body = {"module_id": "bioorchestrator", "tenants": states}
    return respx.get(f"{EM}/api/internal/modules/bioorchestrator/tenants").mock(
        return_value=Response(status, json=body)
    )


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_unavailable_touches_nothing():
    _em({}, status=503)
    post = respx.post(SUBS_URL).mock(return_value=Response(201))
    delete = respx.delete(url__startswith=f"{SUBS_URL}/").mock(return_value=Response(204))
    listing = respx.get(SUBS_URL).mock(return_value=Response(200, json=[]))
    result = await phenology().reconcile_installations(
        "bioorchestrator", base_url=EM, internal_secret="s3")
    assert result["skipped"] == "unavailable"
    assert not post.called and not delete.called and not listing.called


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_ensures_enabled_and_removes_the_rest():
    _em({"montiko": "enabled", "intiasa": "disabled", "gutxi": "not_installed"})
    post = respx.post(SUBS_URL).mock(return_value=Response(201, headers={"Location": "/x"}))
    respx.get(SUBS_URL).mock(return_value=Response(200, json=[]))
    delete = respx.delete(url__startswith=f"{SUBS_URL}/").mock(return_value=Response(204))
    result = await phenology().reconcile_installations(
        "bioorchestrator", base_url=EM, internal_secret="s3")
    assert [c.request.headers["NGSILD-Tenant"] for c in post.calls] == ["montiko"]
    assert sorted(c.request.headers["NGSILD-Tenant"] for c in delete.calls) == ["gutxi", "intiasa"]
    assert result["ensured"]["created"] == 1 and result["removed"]["removed"] == 2


@pytest.mark.asyncio
async def test_reconciles_are_serialized(monkeypatch):
    from nkz_platform_sdk.installations import TenantInstallations
    import nkz_platform_sdk.subscriptions as subs_mod

    reg = phenology()
    active, peak = 0, 0

    async def fake_installed(module_id, **kw):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return TenantInstallations(module_id, (), (), ())

    monkeypatch.setattr(subs_mod, "installed_tenants", fake_installed)
    await asyncio.gather(*(reg.reconcile_installations("bioorchestrator") for _ in range(3)))
    assert peak == 1
