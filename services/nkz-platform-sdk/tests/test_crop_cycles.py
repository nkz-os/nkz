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


def test_placeholder_never_a_cycle():
    t = tl([crop("x-default", status="pending", provenance="placeholder")])
    assert t["current"] is None and t["next"] is None and t["previous"] is None
    assert t["accumulation"] == {"start": "2026-01-01", "basis": "calendar_year"}


def test_future_planting_is_next_not_current():
    t = tl([crop("a", status="active", plantingDate={"@type": "Date", "@value": "2026-10-15"},
                 harvestDate={"@type": "Date", "@value": "2027-06-30"})])
    assert t["current"] is None
    assert t["next"]["start"] == {"date": "2026-10-15", "provenance": "declared"}
    assert t["next"]["campaign_label"] == 2027
    assert t["campaign"] == {"label": 2026, "start": "2026-01-01", "end": "2026-12-31"}
    assert t["accumulation"]["basis"] == "calendar_year"


def test_actual_sowing_overrides_planned_and_drives_accumulation():
    c = crop("a", status="active", plantingDate="2026-03-01", harvestDate="2026-11-30")
    t = tl([c], [op("s1", "sowing", "2026-03-12")])
    assert t["current"]["start"] == {"date": "2026-03-12", "provenance": "actual"}
    assert t["accumulation"] == {"start": "2026-03-12", "basis": "cycle_start"}


def test_manual_planting_is_declared():
    t = tl([crop("a", status="active", plantingDate="2026-03-05", plantingDateSource="manual")])
    assert t["current"]["start"] == {"date": "2026-03-05", "provenance": "declared"}


def test_cover_then_main_with_roller_crimper():
    cover = crop("c", cropRole="cover_crop", status="terminated", sowingWindowStart="2025-10-01",
                 expectedTerminationDate="2026-04-20")
    main = crop("m", cropRole="main_crop", status="active", plantingDateSource="manual", plantingDate="2026-05-01",
                harvestDate="2026-10-30")
    t = tl([cover, main], [op("r1", "tillage", "2026-04-25", tillageType="roller_crimper")])
    assert t["previous"]["crop_id"].endswith(":c")
    assert t["previous"]["end"] == {"date": "2026-04-25", "provenance": "actual"}
    assert t["current"]["crop_id"].endswith(":m") and t["current"]["role"] == "main_crop"


def test_roller_crimper_never_ends_a_main_crop():
    main = crop("m", cropRole="main_crop", status="active", plantingDateSource="manual", plantingDate="2026-05-01",
                harvestDate="2026-10-30")
    t = tl([main], [op("r1", "tillage", "2026-06-01", tillageType="roller_crimper")])
    assert t["current"]["end"]["provenance"] == "planned"
    assert [u["id"] for u in t["unassigned_operations"]] == ["urn:ngsi-ld:AgriParcelOperation:r1"]


def test_mowing_ends_a_cover_crop():
    cover = crop("c", cropRole="cover_crop", status="terminated", sowingWindowStart="2025-10-01",
                 expectedTerminationDate="2026-04-20")
    main = crop("m", cropRole="main_crop", status="active", plantingDate="2026-05-01",
                harvestDate="2026-10-30")
    t = tl([cover, main], [op("w1", "tillage", "2026-04-22", tillageType="mowing")])
    assert t["previous"]["crop_id"].endswith(":c")
    assert t["previous"]["end"] == {"date": "2026-04-22", "provenance": "actual"}


def test_termination_ends_a_catch_crop():
    catch = crop("k", cropRole="catch_crop", status="terminated", sowingWindowStart="2025-08-01",
                 expectedTerminationDate="2025-11-15")
    t = tl([catch], [op("r1", "tillage", "2025-11-10", tillageType="roller_crimper")], at=date(2026, 1, 10))
    assert t["previous"]["end"] == {"date": "2025-11-10", "provenance": "actual"}


def test_mowing_never_ends_a_main_or_perennial_crop():
    # Alfalfa and meadows are mowed several times a year: a cut is never the end of the stand.
    alfalfa = crop("a", cropRole="main_crop", status="active", plantingDateSource="manual", plantingDate="2025-09-01",
                   cropLifecycle="perennial")
    t = tl([alfalfa], [op("w1", "tillage", "2026-06-01", tillageType="mowing")])
    assert t["current"]["crop_id"].endswith(":a")
    assert t["current"]["end"]["provenance"] != "actual"
    assert [u["id"] for u in t["unassigned_operations"]] == ["urn:ngsi-ld:AgriParcelOperation:w1"]


def test_perennial_accumulates_from_campaign_start():
    t = tl([crop("v", status="active", plantingDateSource="manual", plantingDate="2015-02-01", cropLifecycle="perennial")])
    assert t["current"]["lifecycle"] == "perennial"
    assert t["accumulation"] == {"start": "2026-01-01", "basis": "campaign_start"}


def test_unknown_lifecycle_is_reported_and_treated_as_annual():
    t = tl([crop("a", status="active", plantingDateSource="manual", plantingDate="2026-03-01")])
    assert t["current"]["lifecycle"] == "unknown"
    assert t["accumulation"]["basis"] == "cycle_start"


def test_cross_year_label_is_harvest_year():
    t = tl([crop("w", status="active", plantingDateSource="manual", plantingDate="2025-10-20", harvestDate="2026-07-05")],
           at=date(2026, 3, 1))
    assert t["current"]["campaign_label"] == 2026


def test_operation_outside_tolerance_is_unassigned():
    c = crop("a", status="planned", plantingDate="2026-03-01", harvestDate="2026-07-01")
    t = tl([c], [op("s9", "sowing", "2025-11-01")])
    assert t["current"] is None and t["pending"]["start"]["provenance"] == "planned"
    assert len(t["unassigned_operations"]) == 1


def test_suggested_operation_is_hint_only():
    c = crop("a", status="planned", plantingDate="2026-03-01")
    t = tl([c], [op("g1", "sowing", "2026-03-04", status="suggested")])
    assert t["current"] is None and t["pending"]["start"]["provenance"] == "planned"
    assert t["hints"][0]["source"] == "suggested"


def test_normalized_input_and_other_parcels_ignored():
    norm = {"id": "urn:ngsi-ld:AgriCrop:t:n", "type": "AgriCrop",
            "hasAgriParcel": {"type": "Relationship", "object": P},
            "status": {"type": "Property", "value": "active"},
            "plantingDateSource": {"type": "Property", "value": "manual"},
            "plantingDate": {"type": "Property", "value": {"@type": "Date", "@value": "2026-02-01"}}}
    other = crop("o", status="active", plantingDateSource="manual", plantingDate="2026-01-01", refAgriParcel="urn:ngsi-ld:AgriParcel:zz")
    t = tl([norm, other])
    assert t["current"]["crop_id"] == "urn:ngsi-ld:AgriCrop:t:n"


def test_all_cycles_listed_with_campaign_labels():
    cover = crop("c", cropRole="cover_crop", status="terminated", sowingWindowStart="2025-10-01",
                 expectedTerminationDate="2026-04-20")
    main = crop("m", cropRole="main_crop", status="active", plantingDate="2026-05-01", harvestDate="2026-10-30")
    t = tl([main, cover])
    assert [c["crop_id"][-1] for c in t["cycles"]] == ["c", "m"]
    assert [c["campaign_label"] for c in t["cycles"]] == [2026, 2026]


def test_harvested_without_end_date_is_previous_not_current():
    t = tl([crop("h", status="harvested", plantingDate="2026-03-01")])
    assert t["current"] is None and t["previous"]["crop_id"].endswith(":h")


def test_harvest_goes_to_the_cycle_it_ends_not_the_next_one():
    a = crop("a", status="active", plantingDateSource="manual", plantingDate="2026-03-01", harvestDate="2026-07-15")
    b = crop("b", status="planned", sowingWindowStart="2026-08-01", expectedTerminationDate="2026-11-30")
    t = tl([a, b], [op("h1", "harvesting", "2026-07-20")], at=date(2026, 7, 25))
    assert t["previous"]["crop_id"].endswith(":a")
    assert t["previous"]["end"] == {"date": "2026-07-20", "provenance": "actual"}
    assert t["current"] is None and t["unassigned_operations"] == []


def test_end_before_cycle_start_is_reported_not_dropped():
    c = crop("c", status="active", plantingDate="2026-05-01", plantingDateSource="manual",
             harvestDate="2026-09-30")
    t = tl([c], [op("h0", "harvesting", "2026-04-20")])
    assert t["current"]["end"]["provenance"] == "planned"
    assert [u["id"] for u in t["unassigned_operations"]] == ["urn:ngsi-ld:AgriParcelOperation:h0"]


def test_actually_sown_cycle_beats_an_overdue_older_one():
    a = crop("a", status="active", plantingDate="2026-03-01", harvestDate="2026-07-15")
    b = crop("b", status="planned", sowingWindowStart="2026-08-01", expectedTerminationDate="2026-11-30")
    t = tl([a, b], [op("s2", "sowing", "2026-08-02")], at=date(2026, 8, 10))
    assert t["current"]["crop_id"].endswith(":b")
    assert t["current"]["start"] == {"date": "2026-08-02", "provenance": "actual"}


def test_cycle_is_current_on_its_end_day_and_not_after():
    # Both boundaries are inclusive: on harvest day the crop still holds the parcel.
    wheat = crop("w", status="active", plantingDateSource="manual", plantingDate={"@type": "Date", "@value": "2026-03-01"},
                 harvestDate={"@type": "Date", "@value": "2026-07-20"})
    harvest = op("h", "harvesting", "2026-07-15", hasAgriCrop="urn:ngsi-ld:AgriCrop:t:w")
    on_end = tl([wheat], [harvest], at=date(2026, 7, 15))
    after = tl([wheat], [harvest], at=date(2026, 7, 16))
    assert on_end["current"] is not None and on_end["current"]["end"]["date"] == "2026-07-15"
    assert after["current"] is None and after["previous"]["crop_id"] == "urn:ngsi-ld:AgriCrop:t:w"


# ── Only a sowing that is a fact makes a cycle current (owner decision A) ──

def test_planned_start_in_the_past_is_pending_not_current():
    c = crop("a", status="planned", plantingDate="2026-10-01", harvestDate="2027-06-30")
    t = tl([c])
    assert t["current"] is None
    assert t["pending"]["crop_id"].endswith(":a")
    assert t["pending"]["start"] == {"date": "2026-10-01", "provenance": "planned"}
    assert t["accumulation"]["basis"] == "calendar_year"


def test_pending_cycle_is_not_previous():
    c = crop("a", status="planned", sowingWindowStart="2026-03-01", expectedTerminationDate="2026-07-01")
    t = tl([c])
    assert t["previous"] is None and t["pending"]["crop_id"].endswith(":a")


def test_cycles_list_keeps_every_cycle_with_its_pending_flag():
    past = crop("p", status="planned", sowingWindowStart="2026-09-01")
    future = crop("f", status="planned", sowingWindowStart="2026-11-01")
    sown = crop("s", status="active", plantingDate="2026-03-01", plantingDateSource="manual")
    cycles = {c["crop_id"][-1]: c for c in tl([past, future, sown])["cycles"]}
    assert set(cycles) == {"p", "f", "s"}
    assert cycles["p"]["pending"] is True and cycles["p"]["planned_start"] == "2026-09-01"
    assert cycles["f"]["pending"] is False and cycles["s"]["pending"] is False


def test_declared_sowing_is_current_and_clears_pending():
    c = crop("a", status="active", plantingDate="2026-10-01", plantingDateSource="manual")
    t = tl([c])
    assert t["current"]["start"]["provenance"] == "declared" and t["pending"] is None


def test_closed_planned_cycle_is_not_pending():
    c = crop("a", status="harvested", sowingWindowStart="2026-03-01")
    t = tl([c])
    assert t["pending"] is None


def test_active_crop_with_planting_date_is_declared():
    """Marking a crop active with its planting date is a person saying it is in the
    field; only plans (status planned, sowing windows) wait for a sowing."""
    t = tl([crop("a", status="active", plantingDate="2026-03-01")])
    assert t["current"]["start"] == {"date": "2026-03-01", "provenance": "declared"}
    assert t["pending"] is None
