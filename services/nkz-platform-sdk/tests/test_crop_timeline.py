"""crop_timeline — the broker read around the crop-cycle rule; None on failure."""

from datetime import date, datetime, timezone

import httpx
import pytest
import respx
from httpx import Response

from nkz_platform_sdk.crop_timeline import (
    fetch_crop_timeline,
    local_today,
    normalize_parcel_urn,
)

ORION = "http://orion-ld-service:1026"
P = "urn:ngsi-ld:AgriParcel:p1"
ENT = f"{ORION}/ngsi-ld/v1/entities"


def _crop(**a):
    return {"id": "urn:ngsi-ld:AgriCrop:t:a", "type": "AgriCrop", "hasAgriParcel": P, **a}


def _mock(parcel, crops, ops, page_size_ops=None):
    respx.get(f"{ENT}/{P}").mock(return_value=Response(200, json=parcel))

    def entities(request):
        typ = request.url.params.get("type")
        if typ == "AgriCrop":
            return Response(200, json=crops)
        return Response(200, json=ops)

    return respx.get(ENT).mock(side_effect=entities)


@pytest.mark.asyncio
@respx.mock
async def test_payload_is_the_timeline_dict():
    _mock({"id": P, "type": "AgriParcel"},
          [_crop(status="active", plantingDate="2026-03-01", plantingDateSource="manual")], [])
    t = await fetch_crop_timeline("tenant-a", "p1", date(2026, 10, 9), base_url=ORION)
    assert t["parcel_id"] == P and t["current"]["start"]["provenance"] == "declared"
    assert set(t) >= {"current", "previous", "next", "pending", "cycles", "accumulation", "campaign"}


@pytest.mark.asyncio
@respx.mock
async def test_operations_query_is_filtered_and_paged():
    route = _mock({"id": P, "type": "AgriParcel"}, [], [])
    await fetch_crop_timeline("tenant-a", P, date(2026, 10, 9), base_url=ORION)
    ops_calls = [c for c in route.calls if c.request.url.params.get("type") == "AgriParcelOperation"]
    q = ops_calls[0].request.url.params["q"]
    assert 'hasAgriParcel=="urn:ngsi-ld:AgriParcel:p1"' in q and '"sowing"' in q and '"harvesting"' in q
    assert ops_calls[0].request.url.params["limit"] == "500"


@pytest.mark.asyncio
@respx.mock
async def test_missing_parcel_is_none():
    respx.get(f"{ENT}/{P}").mock(return_value=Response(404))
    assert await fetch_crop_timeline("tenant-a", P, base_url=ORION) is None


@pytest.mark.asyncio
@respx.mock
async def test_broker_down_is_none():
    respx.get(f"{ENT}/{P}").mock(side_effect=httpx.ConnectError("down"))
    assert await fetch_crop_timeline("tenant-a", P, base_url=ORION) is None


@pytest.mark.asyncio
@respx.mock
async def test_without_at_uses_the_parcel_day():
    # 23:30 UTC on Oct 9 is already Oct 10 in Madrid.
    parcel = {"id": P, "type": "AgriParcel", "timeZone": "Europe/Madrid"}
    assert local_today(parcel, datetime(2026, 10, 9, 23, 30, tzinfo=timezone.utc)) == date(2026, 10, 10)


def test_local_today_falls_back_to_utc():
    now = datetime(2026, 10, 9, 23, 30, tzinfo=timezone.utc)
    assert local_today({"timeZone": "Not/AZone"}, now) == date(2026, 10, 9)
    assert local_today({}, now) == date(2026, 10, 9)


def test_normalize_parcel_urn():
    assert normalize_parcel_urn("p1") == P and normalize_parcel_urn(P) == P


@pytest.mark.asyncio
@respx.mock
async def test_operations_are_read_across_pages():
    ops = [{"id": f"urn:ngsi-ld:AgriParcelOperation:{i}", "type": "AgriParcelOperation",
            "hasAgriParcel": P, "operationType": "tillage", "status": "completed",
            "endedAt": "2026-01-01T00:00:00Z"} for i in range(1100)]
    respx.get(f"{ENT}/{P}").mock(return_value=Response(200, json={"id": P, "type": "AgriParcel"}))
    offsets = []

    def entities(request):
        if request.url.params.get("type") == "AgriCrop":
            return Response(200, json=[])
        off, lim = int(request.url.params["offset"]), int(request.url.params["limit"])
        offsets.append(off)
        return Response(200, json=ops[off:off + lim])

    respx.get(ENT).mock(side_effect=entities)
    await fetch_crop_timeline("tenant-a", P, date(2026, 10, 9), base_url=ORION)
    assert offsets == [0, 500, 1000]


@pytest.mark.asyncio
async def test_without_at_the_day_comes_from_the_parcel(monkeypatch):
    import nkz_platform_sdk.crop_timeline as ct

    seen = {}

    async def fake_load(client, urn):
        return {"id": urn, "timeZone": {"type": "Property", "value": "Europe/Madrid"}}, [], []

    def fake_today(parcel, now=None):
        seen["parcel"] = parcel
        return date(2026, 10, 10)

    monkeypatch.setattr(ct, "load_crop_data", fake_load)
    monkeypatch.setattr(ct, "local_today", fake_today)
    t = await fetch_crop_timeline("tenant-a", P, base_url=ORION)
    assert t["at"] == "2026-10-10" and seen["parcel"]["id"] == P


def test_local_today_reads_the_normalized_zone():
    parcel = {"timeZone": {"type": "Property", "value": "Europe/Madrid"}}
    assert local_today(parcel, datetime(2026, 10, 9, 23, 30, tzinfo=timezone.utc)) == date(2026, 10, 10)


@pytest.mark.asyncio
@respx.mock
async def test_rejected_request_is_none_and_logged_as_error(caplog):
    import logging

    respx.get(f"{ENT}/{P}").mock(return_value=Response(400, json={"title": "BadRequest"}))
    with caplog.at_level(logging.WARNING):
        assert await fetch_crop_timeline("tenant-a", P, base_url=ORION) is None
    assert any(r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
@respx.mock
async def test_non_json_answer_is_none():
    respx.get(f"{ENT}/{P}").mock(return_value=Response(200, text="<html>proxy</html>"))
    assert await fetch_crop_timeline("tenant-a", P, base_url=ORION) is None
