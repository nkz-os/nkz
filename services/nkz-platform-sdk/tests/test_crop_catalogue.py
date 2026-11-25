"""Catalogue shape: loader, adapter and equivalence with the legacy shape (synthetic ids only)."""
import logging
import re
from datetime import date

import httpx
import pytest

from nkz_platform_sdk.crop_cycles import (
    ParcelCropData,
    _as_date,
    has_catalogue_shape,
    resolve_crop_cycles,
    resolve_parcel_crops,
)
from nkz_platform_sdk.crop_timeline import (
    CatalogueShapeError,
    CropDataRejected,
    CropDataUnavailable,
    ParcelNotFound,
    fetch_crop_timeline,
    load_crop_data,
    load_parcel_crops,
)

T = "acme"
P = "urn:ngsi-ld:AgriParcel:p1"
APP = f"urn:ngsi-ld:AgriApp:{T}:eppo-gd"
AT = date(2026, 10, 9)


def curn(key):
    return f"urn:ngsi-ld:AgriCrop:{T}:{key}"


def oid(key):
    return f"urn:ngsi-ld:AgriParcelOperation:{T}:{key}"


def cat(key, name=None, eppo=None, fallow=False, **extra):
    ent = {"id": curn(key), "type": "AgriCrop", **extra}
    if name:
        ent["name"] = name
    if eppo:
        ent["relatedSource"] = {"application": APP, "applicationEntityId": eppo}
    if fallow:
        ent["agroVocConcept"] = "http://aims.fao.org/aos/agrovoc/c_34007"
    return ent


WHEAT = cat("TRZAX", "wheat", "TRZAX")
BARLEY = cat("HORVX", "barley", "HORVX")
MAIZE = cat("ZEAMX", "maize", "ZEAMX")
OLIVE = cat("OLVEU", "olive", "OLVEU")
VETCH = cat("VICSA", "vetch", "VICSA")
FALLOW = cat("c_34007", "fallow", fallow=True)
CATALOGUE = {c["id"]: c for c in (WHEAT, BARLEY, MAIZE, OLIVE, VETCH, FALLOW)}


def op(key, typ, day, crop=None, status="completed", **a):
    o = {"id": oid(key), "type": "AgriParcelOperation", "hasAgriParcel": P, "operationType": typ,
         "status": status, "endedAt": f"{day}T10:00:00Z", **a}
    if crop is not None:
        o["hasAgriCrop"] = crop["id"]
    return o


def plan(key, crop, start, **a):
    o = {"id": oid(key), "type": "AgriParcelOperation", "hasAgriParcel": P, "operationType": "sowing",
         "status": "planned", "hasAgriCrop": crop["id"], "plannedStartAt": f"{start}T00:00:00Z",
         "dataSource": "crop_plan", "cropSeason": "2026", "cropRole": "main_crop", **a}
    return o


def sow(key, crop, day, **a):
    return op(key, "sowing", day, crop, **a)


def harvest(key, day, sowing=None, crop=None, **a):
    extra = {"hasSowingOperation": oid(sowing)} if sowing else {}
    return op(key, "harvesting", day, crop, **extra, **a)


def data(ops=(), parcel=None, legacy=(), products=None, crops=None):
    return ParcelCropData(parcel={"id": P, **(parcel or {})}, operations=tuple(ops), legacy_crops=tuple(legacy),
                          crops=CATALOGUE if crops is None else crops, products=products or {})


def run(d, at=AT):
    return resolve_parcel_crops(d, at)


def cyc(t, key):
    cid = oid(key) if not key.startswith("urn:") and "#" not in key else key
    return next(c for c in t.cycles if c.crop_id == cid)


def end(t, key):
    e = cyc(t, key).end
    return (e.date.isoformat() if e.date else None, e.provenance)


def unassigned(t):
    return [u["id"] for u in t.unassigned]


# ── Equivalence with the legacy shape ─────────────────────────────────────────

EPPO = re.compile(r"^[A-Z0-9]{5,6}$")
NEW_KEYS = {"kind", "crop", "crop_name", "irrigation_regime", "termination_method", "season",
            "species_lifecycle", "planned_sowing_end"}


def lcrop(cid, **a):
    return {"id": f"urn:ngsi-ld:AgriCrop:{T}:{cid}", "type": "AgriCrop", "hasAgriParcel": P, **a}


def lop(key, typ, day, status="completed", **a):
    return {"id": oid(key), "type": "AgriParcelOperation", "hasAgriParcel": P, "operationType": typ,
            "status": status, "endedAt": {"@type": "DateTime", "@value": f"{day}T10:00:00Z"}, **a}


def to_new(crops, ops):
    """The same scenario in the catalogue shape. Returns (ParcelCropData, new cycle id -> legacy crop id)."""
    ents, new_ops, cycle_op, idmap, products, declared = {}, [], {}, {}, {}, []
    for c in crops:
        key = c["id"].rsplit(":", 1)[-1]
        urn = curn(key)
        sp = c.get("species")
        ent = {"id": urn, "type": "AgriCrop"}
        if sp:
            ent["name"] = sp
            if EPPO.match(sp):
                ent["relatedSource"] = {"application": APP, "applicationEntityId": sp}
        ents[urn] = ent
        if "nkz:declaredCampaign" in c:
            entry = {"campaign": c["nkz:declaredCampaign"], "crop": urn}
            if "variety" in c:
                entry["variety"] = c["variety"]
            if "declaredYieldKgHa" in c:
                entry["yieldKgHa"] = c["declaredYieldKgHa"]
            declared.append(entry)
            idmap[f"{P}#declared-{c['nkz:declaredCampaign']}"] = c["id"]
            continue
        lc = c.get("cropLifecycle")
        pe = (c.get("expectedTerminationDate") if lc == "perennial"
              else c.get("harvestDate") or c.get("expectedTerminationDate"))
        o = {"id": oid(f"cycle-{key}"), "type": "AgriParcelOperation", "hasAgriParcel": P,
             "operationType": "sowing", "hasAgriCrop": urn, "cropRole": c.get("cropRole", "main_crop")}
        if lc:
            o["cropLifecycle"] = lc
        if pe:
            o["expectedTerminationDate"] = pe
        if c.get("variety"):
            purn = f"urn:ngsi-ld:AgriProductType:{T}:{key}-v"
            products[purn] = {"id": purn, "type": "AgriProductType", "name": c["variety"],
                              "category": ["cropVariety"], "root": True}
            o["hasAgriProductType"] = purn
        planting = _as_date(c.get("plantingDate"))
        if c.get("plantingDateSource") == "manual":
            o.update(status="completed", startedAt=f"{planting}T00:00:00Z", endedAt=f"{planting}T00:00:00Z",
                     plantingDateSource="manual")
        else:
            start = planting or _as_date(c.get("sowingWindowStart"))
            o.update(status="planned", dataSource="crop_plan", cropSeason="2026")
            if start:
                o["plannedStartAt"] = f"{start}T00:00:00Z"
        new_ops.append(o)
        cycle_op[c["id"]] = o["id"]
        idmap[o["id"]] = c["id"]
    for o in ops:
        target = o.get("hasAgriCrop") or o.get("refAgriCrop")
        if target is None:
            new_ops.append(o)
            continue
        n = {k: v for k, v in o.items() if k not in ("hasAgriCrop", "refAgriCrop")}
        n["hasAgriCrop"] = curn(target.rsplit(":", 1)[-1])
        if o["operationType"] != "sowing":
            n["hasSowingOperation"] = cycle_op[target]
        new_ops.append(n)
    parcel = {"id": P}
    if declared:
        parcel["declaredCampaigns"] = declared if len(declared) > 1 else declared[0]
    return ParcelCropData(parcel=parcel, operations=tuple(new_ops), legacy_crops=(), crops=ents,
                          products=products), idmap


def strip(t, idmap):
    def cy(x):
        if x is None:
            return None
        y = {k: v for k, v in x.items() if k not in NEW_KEYS and k != "status"}
        y["crop_id"] = idmap.get(y["crop_id"], y["crop_id"])
        return y

    out = dict(t)
    for k in ("current", "previous", "next", "pending"):
        out[k] = cy(t[k])
    out["cycles"] = sorted((cy(x) for x in t["cycles"]), key=lambda x: x["crop_id"])
    return out


def scenarios():
    s = {}
    s["future plan"] = ([lcrop("a", status="active", plantingDate="2026-10-15", harvestDate="2027-06-30")], [], AT)
    s["planned overdue -> pending"] = (
        [lcrop("a", status="planned", plantingDate="2026-10-01", harvestDate="2027-06-30")], [], AT)
    s["plan + machine sowing"] = (
        [lcrop("a", status="active", plantingDate="2026-03-01", harvestDate="2026-11-30")],
        [lop("s1", "sowing", "2026-03-12")], AT)
    s["declared manual planting"] = (
        [lcrop("a", status="active", plantingDate="2026-03-05", plantingDateSource="manual")], [], AT)
    s["cover then main with roller crimper"] = (
        [lcrop("c", cropRole="cover_crop", status="active", sowingWindowStart="2025-10-01",
               expectedTerminationDate="2026-04-20"),
         lcrop("m", cropRole="main_crop", status="active", plantingDateSource="manual", plantingDate="2026-05-01",
               harvestDate="2026-10-30")],
        [lop("c0", "sowing", "2025-10-05"), lop("r1", "tillage", "2026-04-25", tillageType="roller_crimper")], AT)
    s["sown annual harvested, plan without end"] = (
        [lcrop("w", species="TRZAX", cropLifecycle="annual", sowingWindowStart="2025-11-01")],
        [lop("s", "sowing", "2025-11-05"), lop("h", "harvesting", "2026-07-01", harvestedWeight=1000)],
        date(2026, 11, 24))
    s["annual with expectedTerminationDate"] = (
        [lcrop("w", species="TRZAX", cropLifecycle="annual", sowingWindowStart="2025-11-01",
               expectedTerminationDate="2026-07-10")],
        [lop("s", "sowing", "2025-11-05"), lop("h", "harvesting", "2026-07-01")], date(2026, 11, 24))
    olive = lcrop("o", cropRole="main_crop", cropLifecycle="perennial", status="active", plantingDate="2000-01-01",
                  plantingDateSource="manual", species="OLVEU")
    s["perennial yearly harvests and uprooting"] = (
        [olive], [lop("h1", "harvesting", "2025-11-20"), lop("h2", "harvesting", "2026-11-20"),
                  lop("u", "tillage", "2027-03-10", tillageType="uprooting")], date(2027, 4, 1))
    s["perennial open with harvests"] = (
        [olive], [lop("h1", "harvesting", "2025-11-20"), lop("h2", "harvesting", "2026-11-20")], date(2026, 12, 1))
    s["declared past campaign"] = (
        [lcrop("d", status="harvested", species="wheat", **{"nkz:declaredCampaign": 2025},
               variety="Chamorro", declaredYieldKgHa=4200.5)], [], AT)
    s["declared manual plantation with variety"] = (
        [lcrop("v", status="active", plantingDate="2015-02-01", plantingDateSource="manual",
               cropLifecycle="perennial", species="OLVEU", variety="Picual")], [], AT)
    s["two harvests of one cycle"] = (
        [lcrop("w", species="TRZAX", cropLifecycle="annual", sowingWindowStart="2025-11-01")],
        [lop("s", "sowing", "2025-11-05"), lop("h1", "harvesting", "2026-07-01", harvestedWeight=1000),
         lop("h2", "harvesting", "2026-07-04", harvestedWeight=500),
         lop("h3", "harvesting", "2026-10-15", harvestedWeight=9)], date(2026, 11, 24))
    s["harvest before sowing"] = (
        [lcrop("c", status="active", plantingDate="2026-05-01", plantingDateSource="manual",
               harvestDate="2026-09-30")], [lop("h0", "harvesting", "2026-04-20")], AT)
    s["machine sowing outside every window"] = (
        [lcrop("a", status="planned", plantingDate="2026-03-01", harvestDate="2026-07-01")],
        [lop("s9", "sowing", "2025-11-01")], AT)
    s["catch crop terminated"] = (
        [lcrop("k", cropRole="catch_crop", status="planned", sowingWindowStart="2025-08-01",
               expectedTerminationDate="2025-11-15")],
        [lop("s", "sowing", "2025-08-05"), lop("r1", "tillage", "2025-11-10", tillageType="roller_crimper")],
        date(2026, 1, 10))
    s["suggested sowing is a hint"] = (
        [lcrop("a", status="planned", plantingDate="2026-03-01")],
        [lop("g1", "sowing", "2026-03-04", status="suggested")], AT)
    s["sowing linked to the second plan"] = (
        [lcrop("a", cropLifecycle="annual", status="active", plantingDate="2026-03-01", harvestDate="2026-08-01"),
         lcrop("b", cropLifecycle="annual", status="active", plantingDate="2026-04-20", harvestDate="2026-09-01")],
        [lop("s1", "sowing", "2026-03-05", hasAgriCrop=lcrop("b")["id"])], date(2026, 6, 1))
    wheat = lcrop("w", status="active", plantingDateSource="manual", plantingDate="2026-03-01", harvestDate="2026-07-20")
    s["harvest linked, current on its last day"] = (
        [wheat], [lop("h", "harvesting", "2026-07-15", hasAgriCrop=wheat["id"])], date(2026, 7, 15))
    s["harvest under a cover goes to the stand"] = (
        [olive, lcrop("c", cropRole="cover_crop", status="active", plantingDate="2026-10-01",
                      plantingDateSource="manual", expectedTerminationDate="2027-03-15")],
        [lop("h1", "harvesting", "2026-11-20"), lop("m1", "tillage", "2027-03-01", tillageType="mowing")],
        date(2027, 3, 5))
    s["placeholder never a cycle"] = (
        [lcrop("x", status="pending", provenance="placeholder")], [], AT)
    return s


SCENARIOS = scenarios()


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_both_shapes_give_the_same_timeline(name):
    crops, ops, at = SCENARIOS[name]
    legacy = resolve_crop_cycles(P, crops, ops, at).to_dict()
    live = [c for c in crops if c.get("provenance") != "placeholder"]
    new_data, idmap = to_new(live, ops)
    new = resolve_parcel_crops(new_data, at)
    assert has_catalogue_shape(new_data) is bool(live)
    assert strip(new.to_dict(), idmap) == strip(legacy, {}), name


def test_a_sown_cycle_reads_active_and_an_ended_one_harvested_or_terminated():
    for name, expected in (("plan + machine sowing", "active"), ("declared manual planting", "active"),
                           ("future plan", "planned"), ("sown annual harvested, plan without end", "harvested"),
                           ("catch crop terminated", "terminated")):
        crops, ops, at = SCENARIOS[name]
        data_, idmap = to_new(crops, ops)
        t = resolve_parcel_crops(data_, at)
        assert {c.status for c in t.cycles if c.crop_id in idmap} == {expected}, name


# ── Design section 8, rules 1-10 and the status table ─────────────────────────

def test_rule1_a_parcel_with_both_shapes_uses_only_the_new_one():
    old = lcrop("old", status="active", plantingDate="2026-03-01", plantingDateSource="manual", species="OLVEU")
    d = data([sow("s1", WHEAT, "2026-03-05")], legacy=[old])
    t = run(d)
    assert [c.crop_id for c in t.cycles] == [oid("s1")] and t.current.crop_id == oid("s1")
    # the same legacy crop alone is the legacy shape
    assert has_catalogue_shape(data([], legacy=[old])) is False
    assert resolve_parcel_crops(data([], legacy=[old]), AT).current.crop_id.endswith(":old")


def test_rule1_declared_campaigns_alone_also_switch_the_shape():
    old = lcrop("old", status="active", plantingDate="2026-03-01", plantingDateSource="manual")
    d = data([], parcel={"declaredCampaigns": {"campaign": 2025, "crop": WHEAT["id"]}}, legacy=[old])
    assert has_catalogue_shape(d) and [c.crop_id for c in run(d).cycles] == [f"{P}#declared-2025"]


def test_rule2_a_sowing_with_a_catalogue_crop_opens_the_cycle():
    t = run(data([sow("s1", WHEAT, "2026-03-05")]))
    assert t.current.crop_id == oid("s1") and t.current.start.provenance == "actual"
    assert t.current.crop == WHEAT["id"] and t.current.species == "TRZAX" and t.current.kind == "crop"


def test_rule2_a_machine_sowing_without_a_crop_joins_the_cycle_whose_window_holds_it():
    manual = sow("m", WHEAT, "2026-03-01", plantingDateSource="manual")
    t = run(data([manual, op("mach", "sowing", "2026-03-10")]))
    assert t.current.crop_id == oid("m") and t.current.start.date == date(2026, 3, 10)
    assert t.current.start.provenance == "actual" and unassigned(t) == []
    far = run(data([manual, op("mach", "sowing", "2025-06-10")]))
    assert unassigned(far) == [oid("mach")] and far.current.start.provenance == "declared"


def test_rule3_a_resowing_ten_days_later_keeps_the_first_id_and_starts_at_the_last_date():
    t = run(data([sow("s1", WHEAT, "2026-03-05"), sow("s2", WHEAT, "2026-03-15")]))
    assert [c.crop_id for c in t.cycles] == [oid("s1")]
    assert t.current.start.date == date(2026, 3, 15) and t.accumulation_start == date(2026, 3, 15)


def test_rule3_sixty_one_days_or_an_end_in_between_makes_two_cycles():
    far = run(data([sow("s1", WHEAT, "2026-01-01"), sow("s2", WHEAT, "2026-03-03")]))
    assert len(far.cycles) == 2
    near = run(data([sow("s1", WHEAT, "2026-01-01"), sow("s2", WHEAT, "2026-03-02")]))
    assert len(near.cycles) == 1
    ended = run(data([sow("s1", WHEAT, "2026-03-01"), harvest("h", "2026-03-10"), sow("s2", WHEAT, "2026-03-20")]))
    assert len(ended.cycles) == 2
    other_role = run(data([sow("s1", WHEAT, "2026-03-01"), sow("s2", WHEAT, "2026-03-05", cropRole="cover_crop")]))
    assert len(other_role.cycles) == 2


def test_rule3_the_open_cycle_beats_the_plan():
    t = run(data([sow("s1", WHEAT, "2026-03-05"), sow("s2", WHEAT, "2026-03-15"),
                  plan("p", WHEAT, "2026-03-01", expectedTerminationDate="2026-07-30")]))
    assert {c.crop_id for c in t.cycles} == {oid("p")}  # one cycle, the plan completed by the sowings
    assert t.current.crop_id == oid("p") and t.current.start.date == date(2026, 3, 15)


def test_rule4_a_sowing_inside_the_window_of_a_plan_of_the_same_crop_completes_it():
    t = run(data([plan("p", WHEAT, "2026-03-01", expectedTerminationDate="2026-07-30"),
                  sow("s1", WHEAT, "2026-03-20")]))
    assert [c.crop_id for c in t.cycles] == [oid("p")]
    assert t.current.crop_id == oid("p") and t.current.planned_start == date(2026, 3, 1)
    assert t.current.status == "active" and t.pending is None


def test_rule4_a_sowing_of_another_crop_leaves_the_plan_pending():
    t = run(data([plan("p", WHEAT, "2026-03-01", expectedTerminationDate="2026-07-30"),
                  sow("s1", BARLEY, "2026-03-20")]))
    assert t.current.crop_id == oid("s1") and t.current.species == "HORVX"
    assert t.pending.crop_id == oid("p") and t.pending.status == "planned"


def test_rule4_barley_sown_over_a_wheat_plan_is_the_scenario_of_the_review():
    t = run(data([plan("p", WHEAT, "2026-10-01", expectedTerminationDate="2027-07-15"),
                  sow("b", BARLEY, "2026-10-05")]))
    assert t.current.species == "HORVX" and t.pending.species == "TRZAX"
    assert t.accumulation_start == date(2026, 10, 5) and unassigned(t) == []


def test_rule4_a_sowing_far_from_the_plan_window_opens_its_own_cycle():
    t = run(data([plan("p", WHEAT, "2026-03-01", expectedTerminationDate="2026-07-30"),
                  sow("s1", WHEAT, "2025-06-01")]), at=date(2025, 7, 1))
    assert {c.crop_id for c in t.cycles} == {oid("p"), oid("s1")} and t.current.crop_id == oid("s1")


def test_rule5_provenance_actual_declared_planned_and_the_declared_planned_window_is_ignored():
    planned = run(data([plan("p", WHEAT, "2026-11-01")]))
    assert planned.next.start.provenance == "planned"
    actual = run(data([sow("s", WHEAT, "2026-03-05")]))
    assert actual.current.start.provenance == "actual"
    decl = run(data([sow("m", WHEAT, "2026-03-05", plantingDateSource="manual", plannedStartAt="2026-04-01T00:00:00Z",
                         cropSeason="2026")]))
    assert decl.current.start.provenance == "declared" and decl.current.planned_start is None


def test_rule6_an_end_with_hasSowingOperation_goes_to_that_cycle_even_far_from_its_window():
    s_ = sow("s", WHEAT, "2025-11-05", cropLifecycle="annual")
    t = run(data([s_, harvest("h", "2026-07-01", sowing="s")]), at=date(2026, 11, 24))
    assert end(t, "s") == ("2026-07-01", "actual") and cyc(t, "s").status == "harvested" and unassigned(t) == []
    unknown = sow("u", WHEAT, "2025-11-05")  # lifecycle unknown: only the explicit link closes it
    linked = run(data([unknown, harvest("h", "2026-07-01", sowing="u")]), at=date(2026, 11, 24))
    assert end(linked, "u") == ("2026-07-01", "actual")
    unlinked = run(data([unknown, harvest("h", "2026-07-01")]), at=date(2026, 11, 24))
    assert end(unlinked, "u") == (None, None) and unassigned(unlinked) == [oid("h")]


def test_rule6_without_the_link_a_harvest_closes_the_standing_annual_main_crop():
    t = run(data([sow("s", WHEAT, "2025-11-05", cropLifecycle="annual"), harvest("h", "2026-07-01")]),
            at=date(2026, 11, 24))
    assert end(t, "s") == ("2026-07-01", "actual") and unassigned(t) == [] and t.current is None
    far_plan = run(data([plan("p", WHEAT, "2025-11-01", cropLifecycle="annual"), harvest("h", "2026-07-01")]),
                   at=date(2026, 11, 24))
    assert end(far_plan, "p") == (None, None) and unassigned(far_plan) == [oid("h")]


def test_rule6_the_second_harvest_day_sums_and_the_third_far_one_is_unassigned():
    t = run(data([sow("s", WHEAT, "2025-11-05", cropLifecycle="annual"),
                  harvest("h1", "2026-07-01", harvestedWeight=1000), harvest("h2", "2026-07-04", harvestedWeight=500),
                  harvest("h3", "2026-10-15", harvestedWeight=9)]), at=date(2026, 11, 24))
    assert end(t, "s") == ("2026-07-01", "actual") and cyc(t, "s").harvested_weight_kg == 1500
    assert unassigned(t) == [oid("h3")]


def test_rule6_a_harvest_before_the_sowing_is_unassigned():
    t = run(data([sow("s", WHEAT, "2026-05-01", plantingDateSource="manual"), harvest("h", "2026-04-20")]))
    assert end(t, "s")[0] is None and unassigned(t) == [oid("h")]


def test_rule6_a_harvest_with_a_cover_alive_closes_the_main_crop_not_the_cover():
    t = run(data([sow("m", WHEAT, "2025-11-05", cropLifecycle="annual"),
                  sow("c", VETCH, "2026-05-22", cropRole="cover_crop", expectedTerminationDate="2026-07-20"),
                  harvest("h", "2026-07-01")]), at=date(2026, 11, 24))
    assert end(t, "m") == ("2026-07-01", "actual") and end(t, "c") == ("2026-07-20", "planned")
    linked = run(data([sow("m", WHEAT, "2025-11-05", cropLifecycle="annual"),
                       sow("c", VETCH, "2026-05-22", cropRole="cover_crop", expectedTerminationDate="2026-07-20"),
                       harvest("h", "2026-07-01", sowing="c")]), at=date(2026, 11, 24))
    assert end(linked, "c") == ("2026-07-01", "actual") and end(linked, "m") == (None, None)


def test_rule6_a_cover_expired_and_unfinished_does_not_hide_the_main_sown_after_it():
    t = run(data([sow("c", VETCH, "2025-08-28", cropRole="cover_crop", expectedTerminationDate="2025-10-20"),
                  sow("m", WHEAT, "2025-11-05", cropLifecycle="annual")]), at=date(2026, 3, 1))
    assert t.current.crop_id == oid("m")


def test_rule6_a_roller_ends_only_covers_and_uprooting_only_a_stand():
    main = sow("m", WHEAT, "2026-03-01", cropLifecycle="annual")
    t = run(data([main, op("r", "tillage", "2026-06-01", hasSowingOperation=oid("m"), tillageType="roller_crimper")]))
    assert end(t, "m")[0] is None and unassigned(t) == [oid("r")]
    cover = sow("c", VETCH, "2026-03-01", cropRole="cover_crop")
    t = run(data([cover, op("r", "tillage", "2026-04-20", hasSowingOperation=oid("c"), tillageType="roller_crimper")]))
    assert end(t, "c") == ("2026-04-20", "actual") and cyc(t, "c").status == "terminated"
    t = run(data([main, op("u", "tillage", "2026-06-01", hasSowingOperation=oid("m"), tillageType="uprooting")]))
    assert unassigned(t) == [oid("u")]


@pytest.mark.parametrize("lifecycle", [None, "unknown"])
def test_rule6_added_a_harvest_never_closes_a_perennial_species_of_unknown_lifecycle(lifecycle):
    extra = {} if lifecycle is None else {"cropLifecycle": lifecycle}
    stand = sow("o", OLIVE, "2000-01-01", plantingDateSource="manual", **extra)
    t = run(data([stand, harvest("h", "2026-11-20")]), at=date(2026, 12, 1))
    cy = cyc(t, "o")
    assert cy.end.date is None and [h["id"] for h in cy.harvests] == [oid("h")]
    assert cy.lifecycle == "unknown" and cy.species_lifecycle == "perennial" and t.current.crop_id == oid("o")
    up = run(data([stand, harvest("h", "2026-11-20"), op("u", "tillage", "2027-03-10", tillageType="uprooting")]),
             at=date(2027, 4, 1))
    assert end(up, "o") == ("2027-03-10", "actual") and up.current is None and cyc(up, "o").status == "terminated"


def test_rule6_an_annual_of_unknown_lifecycle_keeps_todays_behaviour():
    t = run(data([sow("s", WHEAT, "2026-03-01", plantingDateSource="manual", expectedTerminationDate="2026-07-10"),
                  harvest("h", "2026-07-01")]), at=date(2026, 9, 1))
    assert end(t, "s") == ("2026-07-01", "actual") and cyc(t, "s").lifecycle == "unknown"


def test_rule7_an_end_without_operation_is_the_planned_expected_termination():
    t = run(data([sow("s", WHEAT, "2026-03-01", cropLifecycle="annual", expectedTerminationDate="2026-07-10")]))
    assert end(t, "s") == ("2026-07-10", "planned")


def test_rule8_fallow_is_current_on_2_august_previous_on_1_august_next_year_and_sowing_replaces_it():
    parcel = {"declaredCampaigns": {"campaign": 2027, "crop": FALLOW["id"], "kind": "fallow"}}
    d = data([], parcel=parcel)
    t = run(d, date(2026, 8, 2))
    assert t.current.kind == "fallow" and t.current.crop_id == f"{P}#declared-2027" and t.current.status == "active"
    assert t.accumulation_basis == "calendar_year" and t.accumulation_start == date(2026, 1, 1)
    assert run(d, date(2027, 7, 31)).current.kind == "fallow" and run(d, date(2027, 7, 31)).previous is None
    after = run(d, date(2027, 8, 1))
    assert after.current is None and after.previous.kind == "fallow" and after.previous.status == "terminated"
    assert run(d, date(2026, 7, 31)).current is None
    sown = run(data([sow("s", WHEAT, "2026-10-01")], parcel=parcel), date(2026, 10, 9))
    assert sown.current.crop_id == oid("s") and sown.current.kind == "crop"


def test_rule8_a_fallow_in_july_is_not_past_before_its_window_ends():
    d = data([], parcel={"declaredCampaigns": [{"campaign": 2026, "crop": FALLOW["id"], "kind": "fallow"}]})
    t = run(d, date(2026, 7, 15))
    assert t.current.kind == "fallow" and t.previous is None


def test_rule8_declared_campaigns_as_a_bare_object_and_as_a_list():
    entry = {"campaign": 2025, "crop": WHEAT["id"], "variety": "Chamorro", "yieldKgHa": 4200.5}
    for value in (entry, [entry], {"type": "Property", "value": entry}, {"type": "Property", "value": [entry]}):
        t = run(data([], parcel={"declaredCampaigns": value}))
        assert t.previous.crop_id == f"{P}#declared-2025" and t.previous.status == "harvested"
        assert t.previous.species == "TRZAX" and t.previous.variety == "Chamorro"
        assert t.previous.declared_yield_kg_ha == 4200.5 and t.previous.start.date is None
    both = run(data([], parcel={"declaredCampaigns": [entry, {"campaign": 2024, "crop": BARLEY["id"]}]}))
    assert {c.crop_id for c in both.cycles} == {f"{P}#declared-2025", f"{P}#declared-2024"}
    assert both.previous.campaign_label == 2025


def test_rule9_unidentified_only_on_the_current_main_cycle():
    mix = cat("x-mezcla", "mezcla")
    crops = {**CATALOGUE, mix["id"]: mix}
    cur = run(data([sow("s", mix, "2026-03-01")], crops=crops))
    assert cur.current.kind == "unidentified" and cur.current.species is None and cur.current.crop_name == "mezcla"
    assert cyc(cur, "s").kind == "unidentified"
    past = run(data([sow("s", mix, "2025-03-01", cropLifecycle="annual"), harvest("h", "2025-07-01", sowing="s")],
                    crops=crops))
    assert past.current is None and past.previous.kind == "crop" and past.previous.species == "mezcla"
    cover = run(data([sow("c", mix, "2026-03-01", cropRole="cover_crop")], crops=crops))
    assert cover.current.kind == "crop" and cover.current.species == "mezcla"
    fallow = run(data([sow("f", FALLOW, "2026-03-01")]))
    assert fallow.current.kind == "fallow"


def test_rule9_the_legacy_shape_never_emits_unidentified():
    t = run(data([], legacy=[lcrop("a", status="active", plantingDate="2026-03-01", plantingDateSource="manual")]))
    assert t.current.kind == "crop" and t.current.species is None


def test_rule9_variety_comes_from_the_product_and_a_missing_product_is_none_with_a_warning(caplog):
    prod = f"urn:ngsi-ld:AgriProductType:{T}:TRZAX-chamorro"
    products = {prod: {"id": prod, "type": "AgriProductType", "name": "Chamorro", "category": ["cropVariety"]}}
    o = sow("s", WHEAT, "2026-03-01", hasAgriProductType=prod)
    assert run(data([o], products=products)).current.variety == "Chamorro"
    with caplog.at_level(logging.WARNING):
        assert run(data([o])).current.variety is None
    assert any(prod in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


def test_status_table():
    assert run(data([plan("p", WHEAT, "2026-11-01")])).next.status == "planned"
    assert run(data([sow("s", WHEAT, "2026-03-01")])).current.status == "active"
    h = run(data([sow("s", WHEAT, "2026-03-01", cropLifecycle="annual"), harvest("h", "2026-07-01", sowing="s")]),
            date(2026, 9, 1))
    assert h.previous.status == "harvested"
    tl = run(data([sow("c", VETCH, "2026-03-01", cropRole="cover_crop"),
                   op("r", "tillage", "2026-04-20", hasSowingOperation=oid("c"), tillageType="mowing")]))
    assert tl.previous.status == "terminated"
    decl = run(data([], parcel={"declaredCampaigns": {"campaign": 2025, "crop": WHEAT["id"]}}))
    assert decl.previous.status == "harvested"
    cancelled = run(data([plan("p", WHEAT, "2026-11-01", status="cancelled")]))
    assert cancelled.cycles == ()


def test_a_plan_overdue_is_pending_and_a_future_one_is_next():
    t = run(data([plan("old", WHEAT, "2026-10-01"), plan("fut", BARLEY, "2026-11-15")]))
    assert t.pending.crop_id == oid("old") and t.next.crop_id == oid("fut") and t.current is None


def test_the_new_fields_reach_to_dict():
    t = run(data([sow("s", WHEAT, "2026-03-01", irrigationRegime="rainfed", terminationMethod="harvest",
                      cropSeason="2026", cropLifecycle="annual", cropManagement="organic")]))
    d = t.to_dict()["current"]
    assert d["kind"] == "crop" and d["crop"] == WHEAT["id"] and d["crop_name"] == "wheat"
    assert d["irrigation_regime"] == "rainfed" and d["termination_method"] == "harvest" and d["season"] == "2026"
    assert d["species_lifecycle"] == "annual" and d["planned_sowing_end"] is None


def test_the_planned_sowing_window_comes_out_complete():
    t = run(data([plan("p", WHEAT, "2026-11-01", plannedEndAt="2026-11-20T00:00:00Z")]))
    assert t.next.planned_start == date(2026, 11, 1) and t.next.planned_sowing_end == date(2026, 11, 20)
    assert t.to_dict()["next"]["planned_sowing_end"] == "2026-11-20"


def test_the_legacy_shape_fills_the_new_fields_from_its_crop():
    c = lcrop("a", status="planned", sowingWindowStart="2026-11-01", sowingWindowEnd="2026-11-20", species="TRZAX",
              irrigationRegime="irrigated", terminationMethod="harvest", cropSeason="2027")
    cy = run(data([], legacy=[c])).next
    assert (cy.irrigation_regime, cy.termination_method, cy.season) == ("irrigated", "harvest", "2027")
    assert cy.planned_sowing_end == date(2026, 11, 20) and cy.species_lifecycle == "annual" and cy.kind == "crop"


def test_aliases_ref_names_and_eppo_code_and_a_bare_related_source_are_read():
    legacy_style = {"id": curn("ALIAS"), "type": "AgriCrop", "name": "wheat", "eppoCode": "TRZAX"}
    o = sow("s", legacy_style, "2026-03-01")
    del o["hasAgriCrop"]
    o["refAgriCrop"] = legacy_style["id"]
    o["refAgriParcel"] = o.pop("hasAgriParcel")
    t = run(data([o], crops={legacy_style["id"]: legacy_style}))
    assert t.current.species == "TRZAX" and t.current.kind == "crop"
    listed = {**WHEAT, "relatedSource": [{"application": APP, "applicationEntityId": "TRZAX"}]}
    assert run(data([sow("s", listed, "2026-03-01")], crops={listed["id"]: listed})).current.species == "TRZAX"


def test_a_crop_with_a_parcel_link_is_not_catalogue():
    legacy_crop = {**WHEAT, "hasAgriParcel": "urn:ngsi-ld:AgriParcel:other"}
    d = data([sow("s", legacy_crop, "2026-03-01")], crops={legacy_crop["id"]: legacy_crop})
    assert has_catalogue_shape(d) is False


# ── Loader ────────────────────────────────────────────────────────────────────

def http_error(status):
    req = httpx.Request("GET", "http://orion.invalid/x")
    return httpx.HTTPStatusError("e", request=req, response=httpx.Response(status, request=req))


class FakeClient:
    def __init__(self, parcel=None, legacy=(), ops=(), entities=(), fail=None):
        self.parcel = {"id": P, "type": "AgriParcel", **(parcel or {})}
        self.legacy, self.ops = list(legacy), list(ops)
        self.entities = {e["id"]: e for e in entities}
        self.fail = fail or {}
        self.gets, self.queries = [], []

    async def get_entity(self, urn, options=None):
        self.gets.append(urn)
        if urn in self.fail:
            raise self.fail[urn]
        if urn == P:
            return self.parcel
        if urn in self.entities:
            return self.entities[urn]
        raise http_error(404)

    async def query_entities(self, type=None, q=None, limit=100, offset=0, attrs=None, options=None):
        self.queries.append((type, q, limit, offset))
        src = self.legacy if type == "AgriCrop" else self.ops
        return src[offset:offset + limit]

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_loader_reads_crops_and_varieties_one_by_one_without_repeating():
    prod = f"urn:ngsi-ld:AgriProductType:{T}:TRZAX-v"
    ops = [sow("s1", WHEAT, "2026-03-01", hasAgriProductType=prod), harvest("h1", "2026-07-01", sowing="s1", crop=WHEAT),
           sow("s2", BARLEY, "2026-03-01")]
    client = FakeClient(ops=ops, entities=[WHEAT, BARLEY, {"id": prod, "type": "AgriProductType", "name": "V"}])
    d = await load_parcel_crops(client, P)
    assert sorted(client.gets) == sorted([P, WHEAT["id"], BARLEY["id"], prod])
    assert set(d.crops) == {WHEAT["id"], BARLEY["id"]} and set(d.products) == {prod}
    assert has_catalogue_shape(d) and run(d).current is not None


@pytest.mark.asyncio
async def test_the_queries_do_not_grow_with_the_number_of_operations():
    few = FakeClient()
    many = FakeClient(ops=[sow(f"s{i}", WHEAT, "2026-03-01") for i in range(60)], entities=[WHEAT])
    await load_parcel_crops(few, P)
    await load_parcel_crops(many, P)
    assert [q[:2] for q in few.queries] == [q[:2] for q in many.queries]
    assert few.queries[0][1] == f'hasAgriParcel=="{P}"' and "sowing" in few.queries[1][1]
    assert many.gets.count(WHEAT["id"]) == 1  # 60 operations, one read


@pytest.mark.asyncio
async def test_more_than_500_operations_are_paged():
    ops = [op(f"t{i}", "tillage", "2026-01-01") for i in range(1100)]
    client = FakeClient(ops=ops)
    d = await load_parcel_crops(client, P)
    assert [q[3] for q in client.queries if q[0] == "AgriParcelOperation"] == [0, 500, 1000]
    assert len(d.operations) == 1100


@pytest.mark.asyncio
async def test_a_missing_crop_is_unavailable_and_visible():
    client = FakeClient(ops=[sow("s", WHEAT, "2026-03-01"), sow("b", BARLEY, "2026-03-01")],
                        entities=[BARLEY])  # WHEAT not in the broker, BARLEY proves the catalogue shape
    with pytest.raises(CropDataUnavailable) as exc:
        await load_parcel_crops(client, P)
    assert not isinstance(exc.value, ParcelNotFound) and WHEAT["id"] in str(exc.value)


@pytest.mark.asyncio
async def test_the_parcels_own_crop_link_is_never_requested():
    client = FakeClient(parcel={"hasAgriCrop": WHEAT["id"]})
    await load_parcel_crops(client, P)
    assert client.gets == [P]


@pytest.mark.asyncio
async def test_a_catalogue_parcel_with_one_readable_and_one_missing_target_is_unavailable():
    client = FakeClient(ops=[sow("s1", WHEAT, "2026-03-01"), sow("s2", BARLEY, "2026-03-02")], entities=[WHEAT])
    with pytest.raises(CropDataUnavailable):
        await load_parcel_crops(client, P)


@pytest.mark.asyncio
async def test_declared_campaigns_with_a_missing_crop_are_unavailable_only_if_another_declared_crop_exists(caplog):
    both = {"declaredCampaigns": [{"campaign": 2025, "crop": WHEAT["id"]}, {"campaign": 2024, "crop": BARLEY["id"]}]}
    with pytest.raises(CropDataUnavailable):
        await load_parcel_crops(FakeClient(parcel=both, entities=[WHEAT]), P)
    only = {"declaredCampaigns": {"campaign": 2025, "crop": WHEAT["id"]}}
    with caplog.at_level(logging.WARNING):
        d = await load_parcel_crops(FakeClient(parcel=only), P)
    assert run(d).previous.species is None and any(WHEAT["id"] in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_missing_variety_is_only_absent_and_logged(caplog):
    prod = f"urn:ngsi-ld:AgriProductType:{T}:TRZAX-gone"
    client = FakeClient(ops=[sow("s", WHEAT, "2026-03-01", hasAgriProductType=prod)], entities=[WHEAT])
    with caplog.at_level(logging.WARNING):
        d = await load_parcel_crops(client, P)
    assert prod not in d.products and run(d).current.variety is None
    assert any(prod in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("what", ["variety", "crop"])
@pytest.mark.parametrize("failure, expected", [
    (http_error(500), CropDataUnavailable), (http_error(503), CropDataUnavailable),
    (http_error(429), CropDataUnavailable), (httpx.ReadTimeout("slow"), CropDataUnavailable),
    (http_error(400), CropDataRejected), (ValueError("not json"), CropDataUnavailable)])
async def test_any_other_read_failure_is_unavailable_never_a_degraded_cycle(what, failure, expected):
    prod = f"urn:ngsi-ld:AgriProductType:{T}:TRZAX-v"
    target = prod if what == "variety" else WHEAT["id"]
    client = FakeClient(ops=[sow("s", WHEAT, "2026-03-01", hasAgriProductType=prod)], entities=[WHEAT],
                        fail={target: failure})
    with pytest.raises(expected) as exc:
        await load_parcel_crops(client, P)
    assert type(exc.value) is expected


@pytest.mark.asyncio
async def test_the_parcel_errors_keep_their_old_mapping():
    with pytest.raises(ParcelNotFound):
        await load_parcel_crops(FakeClient(fail={P: http_error(404)}), P)
    with pytest.raises(CropDataRejected):
        await load_parcel_crops(FakeClient(fail={P: http_error(400)}), P)


@pytest.mark.asyncio
async def test_load_crop_data_keeps_the_legacy_tuple_and_refuses_the_catalogue_shape():
    legacy = lcrop("a", status="active", plantingDate="2026-03-01", plantingDateSource="manual")
    parcel, crops, ops = await load_crop_data(FakeClient(legacy=[legacy], ops=[op("t", "tillage", "2026-01-01")]), P)
    assert parcel["id"] == P and crops == [legacy] and len(ops) == 1
    new = FakeClient(legacy=[legacy], ops=[sow("s", WHEAT, "2026-03-01")], entities=[WHEAT])
    with pytest.raises(CatalogueShapeError, match="catalogue shape: use load_parcel_crops"):
        await load_crop_data(new, P)
    with pytest.raises(CropDataUnavailable):
        await load_crop_data(FakeClient(parcel={"declaredCampaigns": [{"campaign": 2025, "crop": WHEAT["id"]}]},
                                        entities=[WHEAT]), P)


@pytest.mark.asyncio
async def test_load_parcel_crops_of_a_parcel_with_both_shapes_gives_only_the_new_cycles():
    legacy = lcrop("old", status="active", plantingDate="2026-03-01", plantingDateSource="manual")
    client = FakeClient(legacy=[legacy], ops=[sow("s", WHEAT, "2026-03-05")], entities=[WHEAT])
    d = await load_parcel_crops(client, P)
    assert d.legacy_crops == (legacy,)
    assert [c.crop_id for c in run(d).cycles] == [oid("s")]


@pytest.mark.asyncio
async def test_fetch_crop_timeline_reads_the_catalogue_shape(monkeypatch):
    import nkz_platform_sdk.crop_timeline as ct

    client = FakeClient(ops=[sow("s", WHEAT, "2026-03-05")], entities=[WHEAT])
    monkeypatch.setattr(ct, "OrionClient", lambda *a, **k: client)
    t = await fetch_crop_timeline("acme", "p1", date(2026, 10, 9))
    assert t["current"]["crop_id"] == oid("s") and t["current"]["crop"] == WHEAT["id"]
    broken = FakeClient(ops=[sow("s", WHEAT, "2026-03-05"), sow("b", BARLEY, "2026-03-05")],
                        entities=[BARLEY])  # one catalogue crop missing: visible as None
    monkeypatch.setattr(ct, "OrionClient", lambda *a, **k: broken)
    assert await fetch_crop_timeline("acme", "p1", date(2026, 10, 9)) is None


@pytest.mark.asyncio
async def test_a_dangling_crop_link_in_a_legacy_only_parcel_keeps_todays_timeline(monkeypatch, caplog):
    import nkz_platform_sdk.crop_timeline as ct

    legacy = lcrop("a", status="planned", plantingDate="2026-03-01", harvestDate="2026-07-01")
    ops = [lop("s", "sowing", "2026-03-10"), lop("h", "harvesting", "2026-07-01", hasAgriCrop=curn("deleted"))]
    client = FakeClient(legacy=[legacy], ops=ops)
    monkeypatch.setattr(ct, "OrionClient", lambda *a, **k: client)
    with caplog.at_level(logging.WARNING):
        got = await fetch_crop_timeline("acme", "p1", date(2026, 10, 9))
    assert got == resolve_crop_cycles(P, [legacy], ops, date(2026, 10, 9)).to_dict()
    assert any(curn("deleted") in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
