from datetime import date

import pytest

from nkz_platform_sdk.crop_cycles import resolve_crop_cycles

P = "urn:ngsi-ld:AgriParcel:p1"
AT = date(2026, 10, 9)
IRI = "https://nkz-os.org/ns/declaredCampaign"


def crop(cid, **a):
    return {"id": f"urn:ngsi-ld:AgriCrop:t:{cid}", "type": "AgriCrop", "hasAgriParcel": P, **a}


def declared(cid, year, key="nkz:declaredCampaign", status="harvested", **a):
    return crop(cid, status=status, species="wheat", **{key: year}, **a)


def tl(crops, at=AT):
    return resolve_crop_cycles(P, list(crops), [], at)


def test_declared_harvested_is_previous_and_never_current_or_pending():
    t = tl([declared("a", 2025)])
    assert t.previous is not None and t.previous.crop_id.endswith(":a")
    assert t.previous.campaign_label == 2025 and t.previous.declared_campaign == 2025
    assert t.current is None and t.next is None and t.pending is None
    assert t.accumulation_basis == "calendar_year"


@pytest.mark.parametrize("key", ["https://nkz-os.org/ns/declaredCampaign", "nkz:declaredCampaign", "declaredCampaign"])
def test_three_term_forms_and_property_dict(key):
    assert tl([declared("a", 2025, key=key)]).previous.campaign_label == 2025
    assert tl([declared("a", {"type": "Property", "value": 2025}, key=key)]).previous.campaign_label == 2025


@pytest.mark.parametrize("bad", [True, 2025.0, "2025", None, {"value": "2025"}])
def test_non_integer_is_absent(bad):
    t = tl([declared("a", bad)])
    assert t.previous is None and t.cycles[0].declared_campaign is None


def test_two_declared_latest_campaign_is_previous():
    t = tl([declared("a", 2024), declared("b", 2025)])
    assert t.previous.crop_id.endswith(":b")


def test_dated_cycle_in_same_campaign_beats_declared():
    dated = crop("d", status="harvested", plantingDate="2024-10-20", harvestDate="2025-07-10",
                 plantingDateSource="manual")
    t = tl([declared("a", 2025), dated])
    assert t.previous.crop_id.endswith(":d")
    assert tl([dated, declared("a", 2025)]).previous.crop_id.endswith(":d")


def test_declared_current_campaign_is_past_only_from_rollover():
    nov, mar = date(2026, 11, 3), date(2026, 3, 1)
    assert tl([declared("a", 2026)], at=nov).previous.campaign_label == 2026
    assert tl([declared("a", 2026)], at=date(2026, 7, 1)).previous is not None
    assert tl([declared("a", 2026)], at=date(2026, 6, 30)).previous is None
    assert tl([declared("a", 2026)], at=mar).previous is None
    assert tl([declared("a", 2025)], at=mar).previous is not None
    assert tl([declared("a", 2027)], at=nov).previous is None
    assert tl([declared("a", 2026)], at=nov).current is None


def test_dated_cycle_wins_same_campaign_after_rollover():
    dated = crop("d", status="harvested", plantingDate="2025-10-20", harvestDate="2026-07-10",
                 plantingDateSource="manual")
    assert tl([declared("a", 2026), dated], at=date(2026, 11, 3)).previous.crop_id.endswith(":d")


def test_declared_not_closed_is_not_past():
    for status in ("active", "pending", None):
        t = tl([declared("a", 2025, status=status)])
        assert t.previous is None and t.current is None and t.pending is None


def test_no_declared_campaign_and_no_dates_stays_outside_past():
    t = tl([crop("a", status="harvested")])
    assert t.previous is None and t.cycles[0].campaign_label is None


def test_later_declared_beats_earlier_dated_cycle():
    dated = crop("d", status="harvested", plantingDate="2023-10-20", harvestDate="2024-07-10",
                 plantingDateSource="manual")
    assert tl([dated, declared("a", 2025)]).previous.crop_id.endswith(":a")


def test_dated_cycle_keeps_dates_label_over_declared():
    c = crop("d", status="harvested", plantingDate="2024-10-20", harvestDate="2025-07-10",
             plantingDateSource="manual", **{"nkz:declaredCampaign": 2020})
    cy = tl([c]).cycles[0]
    assert cy.campaign_label == 2025 and cy.declared_campaign == 2020


def test_dated_cycle_to_dict_unchanged_but_new_key():
    c = crop("d", status="harvested", plantingDate="2024-10-20", harvestDate="2025-07-10",
             plantingDateSource="manual")
    d = tl([c]).cycles[0].to_dict()
    assert d == {
        "crop_id": "urn:ngsi-ld:AgriCrop:t:d", "role": "main_crop", "species": None,
        "status": "harvested", "lifecycle": "unknown",
        "start": {"date": "2024-10-20", "provenance": "declared"},
        "end": {"date": "2025-07-10", "provenance": "planned"},
        "campaign_label": 2025, "pending": False, "declared_campaign": None,
        "planned_start": None, "planned_end": "2025-07-10", "harvests": [],
        "harvest_campaigns": [],
    }


def test_dated_only_previous_keeps_end_or_start_date_rule():
    wheat = crop("w", status="harvested", plantingDate="2025-10-20", plantingDateSource="manual")
    catch = crop("c", status="harvested", cropRole="catch_crop", plantingDate="2025-08-20",
                 harvestDate="2025-11-10", plantingDateSource="manual")
    assert tl([wheat, catch]).previous.crop_id.endswith(":c")
