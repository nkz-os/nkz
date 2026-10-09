"""crop_cycles_service: Orion reads -> SDK resolver; reconciler writes only on change."""
import os
import sys
from datetime import date

import pytest
import requests

_em = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (_em, os.path.join(_em, "..")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import crop_cycles_service as svc  # noqa: E402

P = "urn:ngsi-ld:AgriParcel:p1"
AT = date(2026, 10, 9)


class FakeOrion:
    def __init__(self, parcel=None, crops=(), ops=(), fail=False):
        self.parcel = parcel if parcel is not None else {"id": P, "type": "AgriParcel"}
        self.crops, self.ops, self.fail = list(crops), list(ops), fail
        self.writes = []

    def get_entity(self, eid, options=None):
        if self.fail:
            raise requests.ConnectionError("down")
        if eid != P or self.parcel == {}:
            r = requests.Response(); r.status_code = 404
            raise requests.HTTPError(response=r)
        return self.parcel

    def query_entities(self, type=None, q=None, limit=100, offset=0, attrs=None, options=None):
        if self.fail:
            raise requests.ConnectionError("down")
        return self.crops if type == "AgriCrop" else self.ops

    def append_entity_attrs(self, eid, attrs, **kw):
        self.writes.append((eid, attrs))
        # reflect the write so a second pass sees it
        target = self.parcel if eid == P else next(c for c in self.crops if c["id"] == eid)
        for k, v in attrs.items():
            target[k] = v.get("object") or v.get("value")


CROP = {"id": "urn:ngsi-ld:AgriCrop:t:a", "type": "AgriCrop", "refAgriParcel": P, "status": "active",
        "plantingDate": "2026-03-01", "harvestDate": "2026-11-30"}
SOW = {"id": "urn:ngsi-ld:AgriParcelOperation:s1", "type": "AgriParcelOperation", "hasAgriParcel": P,
       "operationType": "sowing", "status": "completed", "endedAt": "2026-03-12T09:00:00Z"}


def test_timeline_shape():
    t = svc.timeline("t", P, AT, client=FakeOrion(crops=[dict(CROP)]))
    assert t["current"]["crop_id"] == CROP["id"] and t["accumulation"]["basis"] == "cycle_start"


def test_timeline_unknown_parcel():
    with pytest.raises(svc.ParcelNotFound):
        svc.timeline("t", P, AT, client=FakeOrion(parcel={}))


def test_timeline_orion_down_raises():
    with pytest.raises(svc.OrionUnavailable):
        svc.timeline("t", P, AT, client=FakeOrion(fail=True))


def test_reconcile_writes_actuals_and_parcel_facts():
    fake = FakeOrion(crops=[dict(CROP)], ops=[dict(SOW)])
    written = svc.reconcile_parcel("t", P, AT, client=fake)
    assert (CROP["id"], "actualPlantingDate") in written
    assert (P, "hasAgriCrop") in written and (P, "lastPlantedAt") in written
    crop_attrs = dict(fake.writes)[CROP["id"]]
    assert crop_attrs["actualPlantingDate"]["value"] == {"@type": "Date", "@value": "2026-03-12"}


def test_reconcile_is_idempotent():
    fake = FakeOrion(crops=[dict(CROP)], ops=[dict(SOW)])
    svc.reconcile_parcel("t", P, AT, client=fake)
    assert svc.reconcile_parcel("t", P, AT, client=fake) == []


def test_reconcile_planned_start_does_not_set_last_planted():
    fake = FakeOrion(crops=[dict(CROP)])
    written = svc.reconcile_parcel("t", P, AT, client=fake)
    assert (P, "lastPlantedAt") not in written and (P, "hasAgriCrop") in written


def test_reconcile_orion_down_writes_nothing():
    fake = FakeOrion(crops=[dict(CROP)], fail=True)
    with pytest.raises(svc.OrionUnavailable):
        svc.reconcile_parcel("t", P, AT, client=fake)
    assert fake.writes == []


def test_parcels_in_notification():
    ents = [{"type": "AgriCrop", "hasAgriParcel": {"type": "Relationship", "object": P}},
            {"type": "AgriParcelOperation", "refAgriParcel": {"type": "Relationship", "object": "urn:ngsi-ld:AgriParcel:p2"}},
            {"type": "Other"}]
    assert svc.parcels_in_notification(ents) == {P, "urn:ngsi-ld:AgriParcel:p2"}


class PagedOrion(FakeOrion):
    def __init__(self, n):
        super().__init__()
        self.parcels = [{"id": f"urn:ngsi-ld:AgriParcel:p{i}", "type": "AgriParcel"} for i in range(n)]
        self.calls = []

    def query_entities(self, type=None, q=None, limit=100, offset=0, attrs=None, options=None):
        self.calls.append({"type": type, "limit": limit, "offset": offset, "attrs": attrs})
        if type == "AgriParcel":
            return self.parcels[offset:offset + limit]
        return super().query_entities(type=type, q=q, limit=limit, offset=offset, attrs=attrs, options=options)


def test_list_parcels_pages_and_never_filters_by_attrs():
    fake = PagedOrion(1203)
    ids = svc.list_parcels("t", client=fake)
    assert len(ids) == 1203 and ids[0] == "urn:ngsi-ld:AgriParcel:p0"
    assert all(c["attrs"] is None for c in fake.calls)
    assert [c["offset"] for c in fake.calls] == [0, 500, 1000]


def test_list_parcels_orion_down_raises():
    with pytest.raises(svc.OrionUnavailable):
        svc.list_parcels("t", client=FakeOrion(fail=True))


class OpsPagingOrion(FakeOrion):
    def __init__(self, n_ops):
        super().__init__(crops=[dict(CROP)])
        self.op_calls = []
        self.many = [dict(SOW, id=f"urn:ngsi-ld:AgriParcelOperation:x{i}") for i in range(n_ops)]

    def query_entities(self, type=None, q=None, limit=100, offset=0, attrs=None, options=None):
        if type == "AgriParcelOperation":
            self.op_calls.append({"q": q, "offset": offset, "limit": limit})
            return self.many[offset:offset + limit]
        return super().query_entities(type=type, q=q, limit=limit, offset=offset, attrs=attrs, options=options)


def test_operations_are_paged_and_filtered_to_cycle_boundaries():
    fake = OpsPagingOrion(1100)
    svc.timeline("t", P, AT, client=fake)
    assert [c["offset"] for c in fake.op_calls] == [0, 500, 1000]
    q = fake.op_calls[0]["q"]
    assert 'hasAgriParcel=="urn:ngsi-ld:AgriParcel:p1"' in q
    assert 'operationType=="sowing","harvesting","tillage"' in q
