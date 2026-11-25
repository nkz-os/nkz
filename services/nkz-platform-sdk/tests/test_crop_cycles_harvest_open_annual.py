"""A harvest closes the annual main crop standing that day, also when the plan stored no end date."""
from datetime import date
from itertools import permutations

import pytest

from nkz_platform_sdk.crop_cycles import resolve_crop_cycles

P = "urn:ngsi-ld:AgriParcel:p1"
AT = date(2026, 11, 24)


def crop(cid, **a):
    return {"id": cid, "hasAgriParcel": P, "status": "planned", "cropRole": "main_crop", "species": "TRZAX",
            "cropLifecycle": "annual", **a}


def op(oid, kind, day, **a):
    return {"id": oid, "hasAgriParcel": P, "operationType": kind, "status": "completed", "endedAt": day, **a}


WHEAT = crop("wheat", sowingWindowStart="2025-11-01")
SOWN = op("s", "sowing", "2025-11-05")


def resolve(crops, ops, at=AT):
    return resolve_crop_cycles(P, crops, ops, at)


def cycle(timeline, cid="wheat"):
    return next(c for c in timeline.cycles if c.crop_id == cid)


def end(timeline, cid="wheat"):
    e = cycle(timeline, cid).end
    return (e.date.isoformat() if e.date else None, e.provenance)


def unassigned(timeline):
    return [u["id"] for u in timeline.unassigned]


def test_the_harvest_of_a_sown_annual_without_end_closes_it():
    t = resolve([WHEAT], [SOWN, op("h", "harvesting", "2026-07-01", harvestedWeight=1000)])
    assert end(t) == ("2026-07-01", "actual") and unassigned(t) == [] and t.current is None
    assert t.previous.crop_id == "wheat" and cycle(t).harvested_weight_kg == 1000


@pytest.mark.parametrize("start", [{"actualPlantingDate": "2025-11-05"},
                                   {"plantingDate": "2025-11-05", "plantingDateSource": "manual"}])
def test_a_sowing_without_its_operation_counts_as_sown(start):
    t = resolve([crop("wheat", **start)], [op("h", "harvesting", "2026-07-01")])
    assert end(t) == ("2026-07-01", "actual") and unassigned(t) == []


def test_a_plan_that_was_never_sown_is_not_closed_by_a_far_harvest():
    t = resolve([WHEAT], [op("h", "harvesting", "2026-07-01")])
    assert end(t) == (None, None) and unassigned(t) == ["h"]


def test_harvest_days_close_once_add_up_and_do_not_depend_on_arrival_order():
    ops = [SOWN, op("h1", "harvesting", "2026-07-01", harvestedWeight=1000),
           op("h2", "harvesting", "2026-07-04", harvestedWeight=500),
           op("h3", "harvesting", "2026-10-15", harvestedWeight=9)]  # more than 60 days after the first
    for order in permutations(ops):
        t = resolve([WHEAT], list(order))
        assert end(t) == ("2026-07-01", "actual") and unassigned(t) == ["h3"]
        assert cycle(t).harvested_weight_kg == 1500


def test_the_date_the_reconciler_copied_from_a_harvest_does_not_block_the_next_days():
    for copied in ("2026-07-01", "2026-07-02"):
        t = resolve([{**WHEAT, "actualTerminationDate": copied}],
                    [SOWN, op("h1", "harvesting", "2026-07-01"), op("h2", "harvesting", "2026-07-02"),
                     op("h3", "harvesting", "2026-07-04")])
        assert end(t) == ("2026-07-01", "actual") and unassigned(t) == []


@pytest.mark.parametrize("closed, expected", [
    ({"actualTerminationDate": "2026-05-01"}, ("2026-05-01", "actual")),
    ({"terminationDate": "2026-05-01"}, ("2026-05-01", "declared"))])
def test_a_cycle_closed_by_a_date_is_not_alive_afterwards(closed, expected):
    t = resolve([{**WHEAT, **closed}], [SOWN, op("h", "harvesting", "2026-07-01")])
    assert end(t) == expected and unassigned(t) == ["h"]


@pytest.mark.parametrize("status", ["harvested", "terminated", "cancelled"])
def test_a_closed_or_cancelled_cycle_is_not_alive(status):
    t = resolve([{**WHEAT, "status": status}], [SOWN, op("h", "harvesting", "2026-07-01")])
    assert "h" in unassigned(t)


@pytest.mark.parametrize("day, closes", [("2025-11-01", False), ("2026-11-06", True), ("2026-11-07", False)])
def test_the_harvest_must_fall_within_366_days_of_the_sowing(day, closes):
    t = resolve([WHEAT], [SOWN, op("h", "harvesting", day)])
    assert (end(t)[1] == "actual") is closes and (unassigned(t) == []) is closes


def test_the_sown_crop_is_closed_and_not_the_plan_of_the_next_campaign():
    nxt = crop("next", sowingWindowStart="2026-08-20")
    t = resolve([WHEAT, nxt], [SOWN, op("h", "harvesting", "2026-07-01")])
    assert end(t) == ("2026-07-01", "actual") and end(t, "next") == (None, None) and unassigned(t) == []


def test_the_most_recently_sown_crop_is_the_one_standing():
    maize = crop("maize", species="ZEAMX", sowingWindowStart="2026-04-15")
    ops = [SOWN, op("s2", "sowing", "2026-04-20"), op("h", "harvesting", "2026-10-01")]
    t = resolve([WHEAT, maize], ops)
    assert end(t, "maize") == ("2026-10-01", "actual") and end(t) == (None, None)
    linked = resolve([WHEAT, maize], [*ops[:2], {**ops[2], "hasAgriCrop": "wheat"}])
    assert end(linked) == ("2026-10-01", "actual") and end(linked, "maize") == (None, None)


COVER = crop("cover", cropRole="cover_crop", species="VICSA", sowingWindowStart="2026-05-20",
             expectedTerminationDate="2026-07-20")
COVER_SOWN = op("s2", "sowing", "2026-05-22")


def test_a_harvest_under_a_cover_is_the_main_crops():
    t = resolve([WHEAT, COVER], [SOWN, COVER_SOWN, op("h", "harvesting", "2026-07-01")])
    assert end(t) == ("2026-07-01", "actual") and end(t, "cover") == ("2026-07-20", "planned")


def test_a_harvest_linked_to_the_cover_stays_with_the_cover():
    t = resolve([WHEAT, COVER], [SOWN, COVER_SOWN, op("h", "harvesting", "2026-07-01", hasAgriCrop="cover")])
    assert end(t, "cover") == ("2026-07-01", "actual") and end(t) == (None, None)


def test_a_cover_alone_is_harvested_as_before():
    t = resolve([COVER], [COVER_SOWN, op("h", "harvesting", "2026-07-01")])
    assert end(t, "cover") == ("2026-07-01", "actual")


def test_an_expired_cover_does_not_hide_the_main_crop_sown_after_it():
    old = crop("cover", cropRole="cover_crop", species="VICSA", sowingWindowStart="2025-08-25",
               expectedTerminationDate="2025-10-20")
    t = resolve([old, WHEAT], [op("s0", "sowing", "2025-08-28"), SOWN], at=date(2026, 3, 1))
    assert t.current.crop_id == "wheat"


@pytest.mark.parametrize("species", ["OLVEU", "MEDSA"])
def test_a_perennial_keeps_its_harvest_and_stays_open(species):
    stand = crop("stand", species=species, cropLifecycle="perennial", plantingDate="2020-03-01",
                 plantingDateSource="manual")
    t = resolve([stand], [op("h", "harvesting", "2026-07-01")])
    assert end(t, "stand") == (None, None) and [h["id"] for h in cycle(t, "stand").harvests] == ["h"]


@pytest.mark.parametrize("lifecycle", [None, "unknown", "biennial"])
@pytest.mark.parametrize("species", ["OLVEU", "MEDSA", "TRZAX"])
def test_an_unknown_lifecycle_is_never_closed_by_the_new_rule(lifecycle, species):
    c = {**WHEAT, "species": species}
    if lifecycle is None:
        del c["cropLifecycle"]
    else:
        c["cropLifecycle"] = lifecycle
    t = resolve([c], [SOWN, op("h", "harvesting", "2026-07-01")])
    assert end(t) == (None, None) and unassigned(t) == ["h"]


def test_a_harvest_near_the_planned_end_is_assigned_as_before():
    planned = {**WHEAT, "expectedTerminationDate": "2026-07-10"}
    later = crop("later", species="ZEAMX", sowingWindowStart="2026-06-20", expectedTerminationDate="2026-10-30")
    t = resolve([planned, later], [SOWN, op("s2", "sowing", "2026-06-25"), op("h", "harvesting", "2026-07-01")])
    assert end(t) == ("2026-07-01", "actual") and end(t, "later") == ("2026-10-30", "planned")


def test_tillage_does_not_close_a_main_crop():
    t = resolve([WHEAT], [SOWN, op("m", "tillage", "2026-06-20", tillageType="mowing")])
    assert end(t) == (None, None)


def test_a_perennial_that_is_only_a_plan_does_not_take_the_harvest():
    plan = crop("alfalfa", species="MEDSA", cropLifecycle="perennial", sowingWindowStart="2026-03-15",
                expectedTerminationDate="2029-06-30")
    ops = [SOWN, op("h", "harvesting", "2026-07-01")]
    t = resolve([WHEAT, plan], ops)
    assert end(t) == ("2026-07-01", "actual") and cycle(t, "alfalfa").harvests == ()
    # Sown, or in the field by its status: the stand keeps its harvest, as before.
    sown = resolve([WHEAT, plan], [*ops, op("s2", "sowing", "2026-03-20")])
    assert [h["id"] for h in cycle(sown, "alfalfa").harvests] == ["h"] and end(sown) == (None, None)
    active = resolve([WHEAT, {**plan, "status": "active"}], ops)
    assert [h["id"] for h in cycle(active, "alfalfa").harvests] == ["h"] and end(active) == (None, None)
