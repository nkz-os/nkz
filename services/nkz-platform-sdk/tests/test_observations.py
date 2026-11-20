"""Observations client: signing, parsing, raw paging, errors, sync parity."""

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx
from httpx import Response

from nkz_platform_sdk.agronomy import confidence_from_fidelity
from nkz_platform_sdk.crypto import verify_hmac_signature
from nkz_platform_sdk.observations import (
    BestResult,
    Observation,
    ObservableProperty,
    ObservationsClient,
    ObservationsError,
    SyncObservationsClient,
)

BASE = "http://reader:5000"
TENANT = "tenant-a"
SECRET = "test-secret"
FEATURE = "urn:ngsi-ld:AgriParcel:tenant-a:p1"
START = datetime(2026, 11, 1, tzinfo=timezone.utc)
END = datetime(2026, 11, 8, tzinfo=timezone.utc)

BEST_BODY = {
    "feature": FEATURE, "property": "airTemperature", "unitCode": "CEL", "unitSymbol": "°C",
    "medium": "ambientAir", "mode": "series", "resolution": "PT1H", "tz": "UTC",
    "from": "2026-11-01T00:00:00Z", "to": "2026-11-08T00:00:00Z",
    "groups": [{
        "subContext": {"heightM": 2.0},
        "points": [{
            "t": "2026-11-01T00:00:00Z", "value": 9.4, "tier": 2, "fidelity": "iot_sensor",
            "confidence": "high", "calibrated": True, "mediumFallback": False, "coverage": 0.97,
            "sources": [{"kind": "device", "id": "urn:ngsi-ld:Device:tenant-a:S1",
                         "deploymentId": "d-1", "distanceM": None, "n": 12}],
        }, {
            "t": "2026-11-01T01:00:00Z", "value": 11.0, "tier": 4, "fidelity": "parcel_weather",
            "confidence": "medium", "calibrated": False, "mediumFallback": False,
            "coverage": 1.0, "sources": [{"kind": "model", "id": "parcel_weather"}],
        }],
    }],
    "tiersUsed": [2, 4], "warnings": ["w1"],
}


def _item(i: int) -> dict:
    return {
        "t": f"2026-11-01T00:0{i}:00Z", "value": float(i), "observedProperty": "airTemperature",
        "featureOfInterest": FEATURE, "medium": "ambientAir", "depthFromCm": None,
        "depthToCm": None, "heightM": 2.0, "organ": None, "contextRef": None,
        "deploymentId": "d-1", "sourceKind": "device",
        "location": {"type": "Point", "coordinates": [-1.6, 42.8]}, "timeSource": "device",
        "qualityFlag": "valid", "calibrated": False, "deviceId": "urn:ngsi-ld:Device:t:S1",
        "tier": 1, "distanceM": None,
    }


def _assert_signed(request: httpx.Request) -> None:
    assert request.headers["X-Tenant-ID"] == TENANT
    assert verify_hmac_signature(SECRET, request.headers["X-Auth-Signature"], "", TENANT)


@pytest.mark.asyncio
@respx.mock
async def test_best_signs_and_parses():
    route = respx.post(f"{BASE}/api/observations/best").mock(
        return_value=Response(200, json=BEST_BODY))
    async with ObservationsClient(BASE, TENANT, SECRET) as c:
        res = await c.best(feature=FEATURE, property="airTemperature", start=START, end=END,
                           sub_context={"depthFrom": 20}, allow_medium_fallback=True,
                           tz="Europe/Madrid")
    req = route.calls[0].request
    _assert_signed(req)
    body = json.loads(req.content)
    assert body["from"] == "2026-11-01T00:00:00Z" and body["to"] == "2026-11-08T00:00:00Z"
    assert body["feature"] == FEATURE and "geometry" not in body
    assert body["subContext"] == {"depthFrom": 20} and body["allowMediumFallback"] is True
    assert body["mode"] == "series" and body["resolution"] == "PT1H" and body["tiers"] == "all"
    assert body["tz"] == "Europe/Madrid" and "medium" not in body

    assert isinstance(res, BestResult)
    assert res.unit_code == "CEL" and res.tiers_used == (2, 4) and res.warnings == ("w1",)
    assert res.start == START and res.end == END
    p0, p1 = res.groups[0].points
    assert res.groups[0].sub_context == {"heightM": 2.0}
    assert p0.t == START and p0.value == 9.4 and p0.medium_fallback is False
    assert p0.sources[0].deployment_id == "d-1" and p0.sources[0].n == 12
    assert p1.sources[0].kind == "model" and p1.sources[0].deployment_id is None
    with pytest.raises(Exception):  # frozen
        p0.value = 1.0


@pytest.mark.asyncio
async def test_naive_datetime_rejected():
    async with ObservationsClient(BASE, TENANT, SECRET) as c:
        with pytest.raises(ValueError):
            await c.best(feature=FEATURE, property="p", start=datetime(2026, 1, 1), end=END)


@pytest.mark.asyncio
async def test_feature_xor_geometry():
    async with ObservationsClient(BASE, TENANT, SECRET) as c:
        with pytest.raises(ValueError):
            await c.best(property="p", start=START, end=END)
        with pytest.raises(ValueError):
            await c.best(feature=FEATURE, geometry={"type": "Polygon"}, property="p",
                         start=START, end=END)


@pytest.mark.asyncio
@respx.mock
async def test_latest_sends_latest_mode_with_window():
    route = respx.post(f"{BASE}/api/observations/best").mock(
        return_value=Response(200, json={**BEST_BODY, "mode": "latest"}))
    async with ObservationsClient(BASE, TENANT, SECRET) as c:
        res = await c.latest(feature=FEATURE, property="airTemperature", medium="ambientAir")
    body = json.loads(route.calls[0].request.content)
    assert body["mode"] == "latest" and body["medium"] == "ambientAir"
    assert body["from"] < body["to"] and body["to"].endswith("Z")
    assert res.mode == "latest"


@pytest.mark.asyncio
@respx.mock
async def test_raw_follows_cursor():
    # page 2 is empty with a non-null cursor (contract 8.9): the client must keep going
    pages = [
        {"items": [_item(1), _item(2)], "nextCursor": "c1"},
        {"items": [], "nextCursor": "c2"},
        {"items": [_item(3)], "nextCursor": None},
    ]
    route = respx.post(f"{BASE}/api/observations").mock(
        side_effect=[Response(200, json=p) for p in pages])
    async with ObservationsClient(BASE, TENANT, SECRET) as c:
        got = [o async for o in c.raw(feature=FEATURE, property="airTemperature",
                                      start=START, end=END, quality_flags=["valid"], limit=2)]
    assert [o.value for o in got] == [1.0, 2.0, 3.0]
    assert isinstance(got[0], Observation) and got[0].tier == 1
    assert got[0].location == {"type": "Point", "coordinates": [-1.6, 42.8]}
    assert got[0].device_id == "urn:ngsi-ld:Device:t:S1" and got[0].height_m == 2.0
    bodies = [json.loads(r.request.content) for r in route.calls]
    assert "cursor" not in bodies[0]
    assert [b["cursor"] for b in bodies[1:]] == ["c1", "c2"]
    assert all(b["qualityFlags"] == ["valid"] and b["limit"] == 2 for b in bodies)
    for call in route.calls:
        _assert_signed(call.request)


@pytest.mark.asyncio
@respx.mock
async def test_properties():
    respx.get(f"{BASE}/api/observations/properties").mock(return_value=Response(200, json={
        "items": [{"id": "u1", "tenantId": None, "name": "airTemperature", "domain": "environment",
                   "unit": "°C", "unitCode": "CEL", "defaultMedium": "ambientAir",
                   "allowedMedia": ["ambientAir"], "temporalSemantics": "instant",
                   "validMin": -40.0, "validMax": 60.0, "maxRatePerHour": None,
                   "fusion": "median", "nearbyRadiusM": 5000.0, "maxAgeS": 3600,
                   "minCoverage": 0.5, "subContextTolerance": {}, "hasModel": True,
                   "deprecatedAt": None, "createdAt": "2026-11-19T10:00:00+00:00"}]}))
    async with ObservationsClient(BASE, TENANT, SECRET) as c:
        props = await c.properties()
    assert isinstance(props[0], ObservableProperty)
    assert props[0].default_medium == "ambientAir" and props[0].has_model is True
    assert props[0].allowed_media == ("ambientAir",) and props[0].max_age_s == 3600
    assert props[0].created_at == datetime(2026, 11, 19, 10, tzinfo=timezone.utc)


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("status,payload", [(400, {"error": "bad"}), (413, {"error": "big"}),
                                            (422, {"error": "unknown property"}),
                                            (503, {"error": "timeout"})])
async def test_error_maps_status_and_message(status, payload):
    respx.post(f"{BASE}/api/observations/best").mock(return_value=Response(status, json=payload))
    async with ObservationsClient(BASE, TENANT, SECRET) as c:
        with pytest.raises(ObservationsError) as ei:
            await c.best(feature=FEATURE, property="p", start=START, end=END)
    assert ei.value.status == status and ei.value.message == payload["error"]


@pytest.mark.asyncio
@respx.mock
async def test_error_non_json_body():
    respx.post(f"{BASE}/api/observations/best").mock(return_value=Response(502, text="bad gw"))
    async with ObservationsClient(BASE, TENANT, SECRET) as c:
        with pytest.raises(ObservationsError) as ei:
            await c.best(feature=FEATURE, property="p", start=START, end=END)
    assert ei.value.status == 502 and "bad gw" in ei.value.message


@respx.mock
def test_sync_client_same_shapes():
    best = respx.post(f"{BASE}/api/observations/best").mock(
        return_value=Response(200, json=BEST_BODY))
    raw = respx.post(f"{BASE}/api/observations").mock(side_effect=[
        Response(200, json={"items": [_item(1)], "nextCursor": "c"}),
        Response(200, json={"items": [_item(2)], "nextCursor": None})])
    respx.get(f"{BASE}/api/observations/properties").mock(
        return_value=Response(200, json={"items": []}))
    with SyncObservationsClient(BASE, TENANT, SECRET) as c:
        res = c.best(feature=FEATURE, property="airTemperature", start=START, end=END)
        items = list(c.raw(feature=FEATURE, property="airTemperature", start=START, end=END))
        assert c.properties() == []
    _assert_signed(best.calls[0].request)
    assert res == BestResult.from_json(BEST_BODY)
    assert [o.value for o in items] == [1.0, 2.0]
    assert json.loads(raw.calls[1].request.content)["cursor"] == "c"


@respx.mock
def test_sync_error():
    respx.post(f"{BASE}/api/observations/best").mock(
        return_value=Response(404, json={"error": "feature not found"}))
    with SyncObservationsClient(BASE, TENANT, SECRET) as c:
        with pytest.raises(ObservationsError) as ei:
            c.best(feature=FEATURE, property="p", start=START, end=END)
    assert (ei.value.status, ei.value.message) == (404, "feature not found")


def test_default_base_url(monkeypatch):
    monkeypatch.delenv("TIMESERIES_READER_URL", raising=False)
    assert SyncObservationsClient(None, TENANT, SECRET).base_url == \
        "http://timeseries-reader-service:5000"
    monkeypatch.setenv("TIMESERIES_READER_URL", "http://x:1")
    assert SyncObservationsClient(None, TENANT, SECRET).base_url == "http://x:1"


def test_manual_observation_confidence_high():
    assert confidence_from_fidelity("manual_observation") == "high"


# ---- fix round 1: paging guards and malformed bodies

from nkz_platform_sdk.observations import ObservationsParseError  # noqa: E402


@pytest.mark.asyncio
@respx.mock
async def test_raw_repeated_cursor_raises_async():
    route = respx.post(f"{BASE}/api/observations").mock(
        return_value=Response(200, json={"items": [], "nextCursor": "same"}))
    async with ObservationsClient(BASE, TENANT, SECRET) as c:
        with pytest.raises(ObservationsError) as ei:
            async for _ in c.raw(feature=FEATURE, property="p", start=START, end=END):
                pass
    assert ei.value.status == 502 and "repeated" in ei.value.message
    assert route.call_count == 2  # first page, then the repeat is detected


@respx.mock
def test_raw_repeated_cursor_raises_sync():
    route = respx.post(f"{BASE}/api/observations").mock(side_effect=[
        Response(200, json={"items": [], "nextCursor": "a"}),
        Response(200, json={"items": [], "nextCursor": "b"}),
        Response(200, json={"items": [], "nextCursor": "a"})])  # A -> B -> A cycle
    with SyncObservationsClient(BASE, TENANT, SECRET) as c:
        with pytest.raises(ObservationsError) as ei:
            list(c.raw(feature=FEATURE, property="p", start=START, end=END))
    assert ei.value.status == 502 and route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_malformed_best_is_typed_error():
    bad = {**BEST_BODY, "groups": [{"points": [{"t": "2026-11-01T00:00:00Z", "value": 1.0}]}]}
    respx.post(f"{BASE}/api/observations/best").mock(return_value=Response(200, json=bad))
    async with ObservationsClient(BASE, TENANT, SECRET) as c:
        with pytest.raises(ObservationsParseError) as ei:
            await c.best(feature=FEATURE, property="p", start=START, end=END)
    assert isinstance(ei.value, ObservationsError) and ei.value.status == 502


@respx.mock
def test_malformed_raw_item_and_non_json_sync():
    respx.post(f"{BASE}/api/observations").mock(return_value=Response(
        200, json={"items": [{"t": "2026-11-01T00:00:00Z"}], "nextCursor": None}))
    respx.get(f"{BASE}/api/observations/properties").mock(
        return_value=Response(200, text="<html>"))
    with SyncObservationsClient(BASE, TENANT, SECRET) as c:
        with pytest.raises(ObservationsParseError):
            list(c.raw(feature=FEATURE, property="p", start=START, end=END))
        with pytest.raises(ObservationsParseError):
            c.properties()


@respx.mock
def test_source_without_id_tolerated():
    body = json.loads(json.dumps(BEST_BODY))
    del body["groups"][0]["points"][1]["sources"][0]["id"]
    respx.post(f"{BASE}/api/observations/best").mock(return_value=Response(200, json=body))
    with SyncObservationsClient(BASE, TENANT, SECRET) as c:
        res = c.best(feature=FEATURE, property="p", start=START, end=END)
    assert res.groups[0].points[1].sources[0].id is None


@pytest.mark.asyncio
@respx.mock
async def test_raw_null_items_page_tolerated():
    respx.post(f"{BASE}/api/observations").mock(
        return_value=Response(200, json={"items": None, "nextCursor": None}))
    async with ObservationsClient(BASE, TENANT, SECRET) as c:
        got = [o async for o in c.raw(feature=FEATURE, property="p", start=START, end=END)]
    assert got == []
