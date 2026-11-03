from datetime import date

from nkz_platform_sdk.crop_cycles import resolve_crop_cycles

P = "urn:ngsi-ld:AgriParcel:p1"
AT = date(2026, 10, 9)


def crop(cid, **a):
    return {"id": f"urn:ngsi-ld:AgriCrop:t:{cid}", "type": "AgriCrop", "refAgriParcel": P, **a}


def op(oid, typ, day, status="completed", **a):
    return {"id": f"urn:ngsi-ld:AgriParcelOperation:{oid}", "type": "AgriParcelOperation",
            "hasAgriParcel": P, "operationType": typ, "status": status,
            "endedAt": {"@type": "DateTime", "@value": f"{day}T10:00:00Z"}, **a}


def tl(crops, ops=(), at=AT):
    return resolve_crop_cycles(P, list(crops), list(ops), at).to_dict()


OLIVE = crop("o", cropRole="main_crop", cropLifecycle="perennial", status="active",
             plantingDate="2000-01-01", plantingDateSource="manual", species="OLVEU")
H1 = "urn:ngsi-ld:AgriParcelOperation:h1"


def test_perennial_harvest_does_not_close():
    t = tl([OLIVE], [op("h1", "harvesting", "2026-11-20")], at=date(2027, 2, 1))
    assert t["current"]["crop_id"].endswith(":o")
    assert t["current"]["end"] == {"date": None, "provenance": None}
    assert [h["id"] for h in t["current"]["harvests"]] == [H1]
    assert t["unassigned_operations"] == []


def test_old_plantation_receives_harvest_decades_later():
    t = tl([OLIVE], [op("h1", "harvesting", "2026-11-20")], at=date(2026, 12, 1))
    assert len(t["current"]["harvests"]) == 1


def test_harvest_date_is_not_planned_end_for_perennial():
    o = {**OLIVE, "harvestDate": "2026-11-30"}
    t = tl([o], at=date(2027, 3, 1))
    assert t["current"]["crop_id"].endswith(":o") and t["current"]["end"]["date"] is None


def test_uprooting_closes_perennial():
    t = tl([OLIVE], [op("u1", "tillage", "2027-03-10", tillageType="uprooting")], at=date(2027, 4, 1))
    assert t["current"] is None
    assert t["previous"]["end"] == {"date": "2027-03-10", "provenance": "actual"}


def test_uprooting_never_closes_annual_or_cover():
    wheat = crop("w", cropLifecycle="annual", status="active", plantingDate="2026-10-20",
                 plantingDateSource="manual", harvestDate="2027-07-01")
    cover = crop("c", cropRole="cover_crop", status="active", plantingDate="2026-10-01",
                 plantingDateSource="manual", expectedTerminationDate="2027-03-15")
    for c in (wheat, cover):
        t = tl([c], [op("u1", "tillage", "2027-01-10", tillageType="uprooting")], at=date(2027, 2, 1))
        assert t["current"]["crop_id"].endswith(c["id"][-2:])
        assert t["current"]["end"]["provenance"] == "planned"
        assert [u["id"] for u in t["unassigned_operations"]] == ["urn:ngsi-ld:AgriParcelOperation:u1"]


def test_harvest_under_cover_goes_to_perennial():
    cover = crop("c", cropRole="cover_crop", status="active", plantingDate="2026-10-01",
                 plantingDateSource="manual", expectedTerminationDate="2027-03-15")
    t = tl([OLIVE, cover], [op("h1", "harvesting", "2026-11-20"), op("m1", "tillage", "2027-03-01", tillageType="mowing")],
           at=date(2027, 3, 5))
    olive = next(c for c in t["cycles"] if c["crop_id"].endswith(":o"))
    cov = next(c for c in t["cycles"] if c["crop_id"].endswith(":c"))
    assert len(olive["harvests"]) == 1 and olive["end"]["date"] is None
    assert "harvests" not in cov  # only perennials carry the key
    assert cov["end"] == {"date": "2027-03-01", "provenance": "actual"}


def test_harvest_op_with_hasAgriCrop_wins():
    cover = crop("c", cropRole="cover_crop", status="active", plantingDate="2026-10-01",
                 plantingDateSource="manual", expectedTerminationDate="2027-03-15")
    h = op("h1", "harvesting", "2026-11-20", hasAgriCrop=cover["id"])
    t = tl([OLIVE, cover], [h], at=date(2026, 12, 1))
    olive = next(c for c in t["cycles"] if c["crop_id"].endswith(":o"))
    cov = next(c for c in t["cycles"] if c["crop_id"].endswith(":c"))
    assert olive["harvests"] == []
    assert cov["end"] == {"date": "2026-11-20", "provenance": "actual"}


def test_uprooting_then_annual_sowing():
    wheat = crop("w", status="active", plantingDate="2027-10-20", harvestDate="2028-07-01")
    t = tl([OLIVE, wheat], [op("u1", "tillage", "2027-03-10", tillageType="uprooting"),
                            op("s1", "sowing", "2027-10-25")], at=date(2027, 11, 15))
    assert t["previous"]["crop_id"].endswith(":o") and t["current"]["crop_id"].endswith(":w")


def test_harvest_before_planting_is_unassigned():
    t = tl([OLIVE], [op("h1", "harvesting", "1999-11-20")], at=date(2026, 12, 1))
    assert t["current"]["harvests"] == []
    assert [u["id"] for u in t["unassigned_operations"]] == [H1]


def test_status_harvested_on_perennial_still_closes():
    # Documented non-regression: nobody writes it for perennials, but it keeps closing (spec 6.3).
    o = {**OLIVE, "status": "harvested", "actualTerminationDate": "2026-12-01"}
    t = tl([o], at=date(2027, 2, 1))
    assert t["current"] is None and t["previous"]["crop_id"].endswith(":o")


def test_declared_only_cycle_never_receives_perennial_harvest():
    declared = crop("d", cropLifecycle="perennial", status="harvested", **{"nkz:declaredCampaign": 2025})
    t = tl([declared], [op("h1", "harvesting", "2026-11-20")], at=date(2026, 12, 1))
    cy = next(c for c in t["cycles"] if c["crop_id"].endswith(":d"))
    assert cy["harvests"] == []
    assert [u["id"] for u in t["unassigned_operations"]] == [H1]


def test_scouting_and_unknown_ops_ignored():
    ops = [op("x1", "scouting", "2026-11-20"), op("x2", "teleporting", "2026-11-21")]
    t = tl([OLIVE], ops, at=date(2026, 12, 1))
    assert t["current"]["harvests"] == [] and t["current"]["end"]["date"] is None
    assert t["unassigned_operations"] == []
