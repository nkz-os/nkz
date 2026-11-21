"""Satellite observation reader: both stored shapes come back as the same observations."""

import asyncio
import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from nkz_platform_sdk.eo import (
    EO_FAMILIES,
    EO_VALUE_NAMES,
    EOObservation,
    EOReadIncomplete,
    fetch_eo_observations,
    fetch_eo_observations_sync,
    parse_eo_entities,
)

FIXTURES = Path(__file__).parent / "fixtures" / "eo"
PARCEL = "urn:ngsi-ld:AgriParcel:00000001-0000-4000-8000-000000000001"
ZONE = "urn:ngsi-ld:AgriParcelZone:tenant-a:00000001-0000-4000-8000-000000000001:zn000001-e11-flat"
NKZ = "https://nkz-os.org/ns/"
SDM = "https://smartdatamodels.org/dataModel.SatelliteImagery/"
DEFAULT = "https://uri.etsi.org/ngsi-ld/default-context/"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def legacy():
    return load("legacy_stored.json")


@pytest.fixture
def sdm():
    return load("sdm_stored.json")


def only(observations, **match):
    found = [o for o in observations if all(getattr(o, k) == v for k, v in match.items())]
    assert len(found) == 1, f"{len(found)} observations for {match}"
    return found[0]


def prop(value, **subs):
    return {"type": "Property", "value": value, **{k: {"type": "Property", "value": v} for k, v in subs.items()}}


def legacy_entity(day="2026-05-01", suffix="", **attrs):
    return {
        "id": f"urn:ngsi-ld:EOProduct:tenant-a:00000001-0{suffix}:{day}",
        "type": "EOProduct",
        "refAgriParcel": {"type": "Relationship", "object": PARCEL},
        "sensingDate": prop(day),
        **attrs,
    }


def sdm_entity(day="2026-05-01", family="s2-indices", analytics=None, **attrs):
    return {
        "id": f"urn:ngsi-ld:EOSatelliteImagery:tenant-a:parcel:00000001-0000-4000-8000-000000000001:{family}:{day}",
        "type": "EOSatelliteImagery",
        "hasAgriParcel": {"type": "Relationship", "object": PARCEL},
        "sensingDate": prop(day),
        "analytics": prop(analytics or [{"name": "ndvi", "value": "0.500000"}, {"name": "pixelCount", "value": "10"}]),
        **attrs,
    }


# ── shared golden file (the TypeScript reader is tested against the same one) ──


def test_both_fixture_files_give_the_expected_observations(legacy, sdm):
    got = [o.to_dict() for o in parse_eo_entities(legacy + sdm)]
    assert got == load("expected.json")


# ── earlier shape, as stored ─────────────────────────────────────────────────


def test_legacy_optical_parcel_reads_indices_statistics_and_interior(legacy):
    obs = only(parse_eo_entities(legacy), sensing_day=date(2025, 12, 8))
    assert (obs.shape, obs.family, obs.target) == ("legacy", "s2-indices", "parcel")
    assert obs.parcel_id == PARCEL and obs.zone_id is None
    assert obs.values["ndvi"] == 0.303142
    assert obs.values["ndvi.min"] == 0.150943
    assert obs.values["ndvi.interiorMean"] == 0.284618
    assert obs.values["ndvi.interiorPixelCount"] == 874 and isinstance(obs.values["ndvi.interiorPixelCount"], int)
    assert obs.values["ndvi.interiorMask"] == "core"
    assert obs.values["evi"] == 0.158414
    assert "cloudCoverage" in obs.values and "cloudCoverPercentage" not in obs.values
    assert 0 <= obs.values["parcelCloudFraction"] <= 1
    assert isinstance(obs.values["pixelCount"], int)
    assert obs.raster_url("ndvi").endswith("/NDVI.tif")
    assert obs.raster_url("lst") is None


def test_legacy_times_are_never_returned(legacy):
    assert all(o.sensing_time is None for o in parse_eo_entities(legacy))


def test_legacy_merged_entity_is_split_by_family(legacy):
    day = [o for o in parse_eo_entities(legacy) if o.sensing_day == date(2026, 10, 21)]
    assert sorted(o.family for o in day) == ["s1-soil-moisture", "s2-indices"]
    assert len({o.entity_id for o in day}) == 1
    moisture = only(day, family="s1-soil-moisture")
    assert moisture.values == {
        "soilMoistureIndex": 0.48,
        "soilMoistureIndex.min": 0.0154,
        "soilMoistureIndex.max": 0.9312,
        "soilMoistureIndex.std": 0.2376,
    }
    assert moisture.product_id.startswith("S1X_IW_GRDH")
    indices = only(day, family="s2-indices")
    assert "soilMoistureIndex" not in indices.values and "ndvi" in indices.values
    assert indices.product_id is None


def test_legacy_radar_moisture_alone_is_not_an_optical_observation(legacy):
    day = [o for o in parse_eo_entities(legacy) if o.sensing_day == date(2026, 11, 9) and o.target == "parcel"]
    assert sorted(o.family for o in day) == ["s1-backscatter", "s1-soil-moisture"]


def test_legacy_grd_parcel_takes_its_day_from_acquisition_date(legacy):
    obs = only(parse_eo_entities(legacy), family="s1-backscatter", target="parcel")
    assert obs.sensing_day == date(2026, 11, 9)
    assert obs.values == {"backscatterVV": -9.0721, "backscatterVH": -16.2495}
    assert obs.product_type == "GRD" and obs.parcel_id == PARCEL


def test_legacy_zone_entities_carry_the_zone_and_no_parcel(legacy):
    zone = [o for o in parse_eo_entities(legacy) if o.target == "zone"]
    assert sorted(o.family for o in zone) == ["s1-backscatter", "s2-indices"]
    assert all(o.zone_id == ZONE and o.parcel_id is None for o in zone)
    radar = only(zone, family="s1-backscatter")
    assert radar.values["pixelCount"] == 165 and radar.values["validPixelFraction"] == 0.873
    optical = only(zone, family="s2-indices")
    assert optical.values["pixelCount"] == 379 and "ndvi.interiorMean" not in optical.values


def test_legacy_day_prefers_sensing_date_then_acquisition_date_then_the_identifier():
    entity = legacy_entity(day="2026-05-03", backscatterVV=prop(-9.0), acquisitionDate=prop("2026-05-02T00:00:00Z"))
    entity["sensingDate"] = prop("2026-05-01")
    assert only(parse_eo_entities([entity])).sensing_day == date(2026, 5, 1)
    del entity["sensingDate"]
    assert only(parse_eo_entities([entity])).sensing_day == date(2026, 5, 2)
    del entity["acquisitionDate"]
    assert only(parse_eo_entities([entity])).sensing_day == date(2026, 5, 3)


def test_legacy_composite_is_recognised_by_its_raster_folder():
    scene = legacy_entity(ndvi=prop(0.4, rasterUrl="s3://example-bucket/tenant-a/entities/p/scenes/s1/NDVI.tif"))
    composite = legacy_entity(
        day="2026-05-02", ndvi=prop(0.4, rasterUrl="s3://example-bucket/tenant-a/entities/p/composites/j1/NDVI.tif")
    )
    assert [o.family for o in parse_eo_entities([scene, composite])] == ["s2-indices", "s2-composite"]


def test_legacy_scene_and_composite_sharing_an_entity_are_split_index_by_index():
    entity = legacy_entity(
        ndvi=prop(0.41, min=0.1, rasterUrl="s3://example-bucket/tenant-a/entities/p/composites/j1/NDVI.tif"),
        evi=prop(0.22, rasterUrl="s3://example-bucket/tenant-a/entities/p/scenes/s1/EVI.tif"),
        savi=prop(0.3),
        cloudCoverPercentage=prop(12.0),
        pixelCount=prop(300),
    )
    found = parse_eo_entities([entity])
    scene = only(found, family="s2-indices")
    assert scene.values == {"evi": 0.22, "savi": 0.3, "cloudCoverage": 12.0}
    assert set(scene.legacy_raster_urls) == {"evi"}
    composite = only(found, family="s2-composite")
    assert composite.values == {"ndvi": 0.41, "ndvi.min": 0.1}
    assert composite.raster_url("ndvi").endswith("/composites/j1/NDVI.tif")


def test_legacy_composite_alone_keeps_its_quality_values():
    entity = legacy_entity(
        ndvi=prop(0.41, rasterUrl="s3://example-bucket/tenant-a/entities/p/composites/j1/NDVI.tif"),
        cloudCoverPercentage=prop(12.0),
        pixelCount=prop(300),
    )
    assert only(parse_eo_entities([entity])).values == {"ndvi": 0.41, "cloudCoverage": 12.0, "pixelCount": 300}


def test_legacy_statistics_are_read_under_any_name():
    entity = legacy_entity(ndvi={"type": "Property", "value": 0.4, DEFAULT + "min": {"type": "Property", "value": 0.1},
                                 NKZ + "rasterUrl": {"type": "Property", "value": "s3://example-bucket/a/NDVI.tif"}})
    obs = only(parse_eo_entities([entity]))
    assert obs.values == {"ndvi": 0.4, "ndvi.min": 0.1}
    assert obs.raster_url("ndvi") == "s3://example-bucket/a/NDVI.tif"


def test_a_count_that_is_not_whole_is_left_out(caplog):
    entity = legacy_entity(ndvi=prop(0.4), pixelCount=prop(874.9))
    with caplog.at_level(logging.WARNING):
        assert only(parse_eo_entities([entity])).values == {"ndvi": 0.4}
    assert "not a whole number" in caplog.text


def test_legacy_day_falls_back_to_the_identifier():
    entity = legacy_entity(ndvi=prop(0.4))
    del entity["sensingDate"]
    assert only(parse_eo_entities([entity])).sensing_day == date(2026, 5, 1)


def test_legacy_entity_without_any_day_is_skipped(caplog):
    entity = {"id": "urn:ngsi-ld:EOProduct:tenant-a:x", "type": "EOProduct", "ndvi": prop(0.4)}
    with caplog.at_level(logging.WARNING):
        assert parse_eo_entities([entity]) == []
    assert "no day" in caplog.text


# ── earlier shape, branches with no stored example (built from the writer) ───


def test_legacy_surface_temperature_family_follows_the_source_scene():
    landsat = legacy_entity(lst=prop(21.5, min=18.0, max=25.0, std=1.2), lstSourceScene=prop("LC09_L2SP_000000_20260501"))
    clms = legacy_entity(day="2026-05-02", lst=prop(19.0), lstSourceScene=prop("CLMS:c_gls_LST_202605020900"))
    found = parse_eo_entities([landsat, clms])
    first = only(found, family="landsat-lst")
    assert first.values == {"lst": 21.5, "lst.min": 18.0, "lst.max": 25.0, "lst.std": 1.2}
    assert first.product_id == "LC09_L2SP_000000_20260501"
    assert only(found, family="clms-lst").values == {"lst": 19.0}


def test_legacy_change_flag_keeps_its_text_and_renames_its_parts():
    entity = legacy_entity(
        sarChangeFlag={**prop("tillage", confidence=0.81, deltaVV=-2.41), "observedAt": "2026-05-01T06:00:00.000Z"},
        sarSceneId=prop("S1X_IW_GRDH_1SDV_20260501T000000"),
    )
    obs = only(parse_eo_entities([entity]))
    assert obs.family == "s1-change"
    assert obs.values == {"changeFlag": "tillage", "changeConfidence": 0.81, "deltaVV": -2.41}
    assert obs.product_id == "S1X_IW_GRDH_1SDV_20260501T000000"


def test_legacy_pixel_counters_are_dropped_when_two_raster_products_share_the_entity():
    both = legacy_entity(ndvi=prop(0.4), lst=prop(21.5), pixelCount=prop(300), validPixelFraction=prop(0.9))
    for obs in parse_eo_entities([both]):
        assert "pixelCount" not in obs.values and "validPixelFraction" not in obs.values
    single = legacy_entity(lst=prop(21.5), pixelCount=prop(300))
    assert only(parse_eo_entities([single])).values["pixelCount"] == 300


def test_legacy_pixel_counters_are_dropped_when_the_other_raster_product_is_unreadable():
    entity = legacy_entity(ndvi=prop("N/A"), lst=prop(21.5), pixelCount=prop(300))
    assert only(parse_eo_entities([entity])).values == {"lst": 21.5}


def test_legacy_zone_is_recognised_by_its_identifier_when_the_relation_is_missing():
    entity = legacy_entity(ndvi=prop(0.4))
    del entity["refAgriParcel"]
    entity["id"] = "urn:ngsi-ld:EOProduct:tenant-a:zone:00000001-0:zn000001-e11-flat:2026-05-01"
    obs = only(parse_eo_entities([entity]))
    assert (obs.target, obs.zone_id, obs.parcel_id) == ("zone", None, None)


@pytest.mark.parametrize("bad", [10 ** 400, "1_0", "0x10", " ", "1,5", "--1"])
def test_legacy_odd_numbers_are_left_out_without_stopping_the_read(bad):
    entity = legacy_entity(ndvi=prop(0.4), evi=prop(bad))
    assert only(parse_eo_entities([entity])).values == {"ndvi": 0.4}


@pytest.mark.parametrize("text,number", [("0.5", 0.5), (" -0.25 ", -0.25), ("1e-3", 0.001), ("+2", 2.0), (".5", 0.5)])
def test_numbers_written_as_text_are_read(text, number):
    assert only(parse_eo_entities([legacy_entity(ndvi=prop(text))])).values == {"ndvi": number}


def test_a_negative_count_is_left_out():
    entity = legacy_entity(ndvi=prop(0.4), pixelCount=prop(-3))
    assert only(parse_eo_entities([entity])).values == {"ndvi": 0.4}


def test_legacy_incidence_angle_goes_with_the_moisture():
    entity = legacy_entity(sarMoisture=prop(0.5), sarIncidenceDeg=prop(38.2))
    assert only(parse_eo_entities([entity])).values == {"soilMoistureIndex": 0.5, "incidenceAngle": 38.2}


@pytest.mark.parametrize("bad", ["N/A", None, True, float("nan"), {"a": 1}])
def test_legacy_unreadable_number_is_left_out_with_a_warning(bad, caplog):
    entity = legacy_entity(ndvi=prop(0.4), evi=prop(bad))
    with caplog.at_level(logging.WARNING):
        obs = only(parse_eo_entities([entity]))
    assert obs.values == {"ndvi": 0.4}
    assert "evi" in caplog.text


# ── attribute names in every stored form ─────────────────────────────────────


@pytest.mark.parametrize("prefix", ["", "nkz:", NKZ, SDM, DEFAULT])
def test_sensing_date_is_read_under_any_name(prefix):
    entity = legacy_entity(ndvi=prop(0.4))
    entity[prefix + "sensingDate"] = prop("2026-04-30")
    if prefix:
        del entity["sensingDate"]
    assert only(parse_eo_entities([entity])).sensing_day == date(2026, 4, 30)


def test_official_copy_wins_while_both_copies_exist():
    entity = legacy_entity(ndvi=prop(0.4))
    entity["sensingDate"] = prop("2026-04-29")
    entity[SDM + "sensingDate"] = prop("2026-04-30")
    assert only(parse_eo_entities([entity])).sensing_day == date(2026, 4, 30)


@pytest.mark.parametrize("prefix", ["", "nkz:", NKZ, SDM, DEFAULT])
def test_product_type_is_read_under_any_name(prefix):
    entity = legacy_entity(backscatterVV=prop(-9.0))
    entity[prefix + "productType"] = prop("GRD")
    assert only(parse_eo_entities([entity])).product_type == "GRD"


def test_product_type_official_copy_wins_while_both_copies_exist():
    entity = legacy_entity(backscatterVV=prop(-9.0), productType=prop("old"))
    entity[SDM + "productType"] = prop("GRD")
    assert only(parse_eo_entities([entity])).product_type == "GRD"


def test_zone_relation_with_both_copies_is_read():
    entity = legacy_entity(ndvi=prop(0.4))
    del entity["refAgriParcel"]
    entity["hasAgriParcelZone"] = {"type": "Relationship", "object": ZONE}
    entity[NKZ + "hasAgriParcelZone"] = {"type": "Relationship", "object": ZONE}
    assert only(parse_eo_entities([entity])).zone_id == ZONE


@pytest.mark.parametrize("name", ["hasAgriParcelZone", NKZ + "hasAgriParcelZone", DEFAULT + "hasAgriParcelZone",
                                  "hasAgriManagementZone", NKZ + "hasAgriManagementZone"])
def test_zone_relation_is_read_under_any_name(name):
    entity = legacy_entity(ndvi=prop(0.4))
    del entity["refAgriParcel"]
    entity[name] = {"type": "Relationship", "object": ZONE}
    obs = only(parse_eo_entities([entity]))
    assert (obs.target, obs.zone_id) == ("zone", ZONE)


@pytest.mark.parametrize("name", ["hasAgriParcel", "refAgriParcel",
                                  "https://smartdatamodels.org/dataModel.Agrifood/hasAgriParcel"])
def test_parcel_relation_is_read_under_any_name(name):
    entity = legacy_entity(ndvi=prop(0.4))
    del entity["refAgriParcel"]
    entity[name] = {"type": "Relationship", "object": PARCEL}
    assert only(parse_eo_entities([entity])).parcel_id == PARCEL


def test_type_as_full_iri_is_recognised():
    entity = legacy_entity(ndvi=prop(0.4))
    entity["type"] = SDM + "EOProduct"
    assert len(parse_eo_entities([entity])) == 1


def test_other_types_are_ignored():
    assert parse_eo_entities([{"id": "urn:ngsi-ld:AgriParcel:x", "type": "AgriParcel", "ndvi": prop(0.4)}]) == []


# ── Smart Data Models shape ──────────────────────────────────────────────────


def test_sdm_values_are_numbers_with_integers_and_text_where_due(sdm):
    obs = only(parse_eo_entities(sdm), family="s2-indices", sensing_day=date(2026, 10, 31))
    assert (obs.shape, obs.target, obs.parcel_id, obs.zone_id) == ("sdm", "parcel", PARCEL, None)
    assert obs.values["ndvi"] == 0.412345
    assert obs.values["ndvi.interiorPixelCount"] == 540 and isinstance(obs.values["ndvi.interiorPixelCount"], int)
    assert obs.values["ndvi.interiorMask"] == "core"
    assert obs.values["pixelCount"] == 874 and isinstance(obs.values["pixelCount"], int)
    assert obs.values["cloudCoverage"] == 12.5
    assert obs.sensing_time == datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc)
    assert obs.product_id.startswith("S2X_MSIL2A") and obs.product_type == "S2MSI2A"
    assert obs.raster_url("ndvi") == "s3://example-bucket/tenant-a/scenes/00000002-0000-4000-8000-000000000002/ndvi.tif"
    assert obs.raster_url("lst") is None
    assert obs.raster_url("pixelCount") is None and obs.raster_url("ndvi.min") is None
    assert hash(obs) == hash(obs)


def test_sdm_one_element_analytics_comes_back_as_an_object(sdm):
    obs = only(parse_eo_entities(sdm), sensing_day=date(2026, 11, 5))
    assert obs.values == {"ndvi": 0.455}
    assert obs.sensing_time is None
    assert obs.raster_url("ndvi") is None


def test_sdm_zone_and_text_values(sdm):
    found = parse_eo_entities(sdm)
    zone = only(found, target="zone")
    assert (zone.family, zone.zone_id, zone.parcel_id) == ("s1-backscatter", ZONE, PARCEL)
    assert zone.values["backscatterVV"] == -8.3774 and zone.values["incidenceAngle"] == 38.2
    change = only(found, family="s1-change")
    assert change.values == {"changeFlag": "tillage", "changeConfidence": 0.81, "deltaVV": -2.41}


def test_sdm_key_values_representation_is_read_too():
    entity = {
        "id": "urn:ngsi-ld:EOSatelliteImagery:tenant-a:parcel:p1:s2-indices:2026-05-01",
        "type": "EOSatelliteImagery",
        "hasAgriParcel": PARCEL,
        "sensingDate": "2026-05-01T10:00:00Z",
        "analytics": {"name": "ndvi", "value": "0.5"},
    }
    obs = only(parse_eo_entities([entity]))
    assert obs.values == {"ndvi": 0.5} and obs.parcel_id == PARCEL


@pytest.mark.parametrize("bad_id", [
    "urn:ngsi-ld:EOSatelliteImagery:tenant-a:parcel:p1:unknown-family:2026-05-01",
    "urn:ngsi-ld:EOSatelliteImagery:tenant-a:p1:s2-indices:2026-05-01",
    "urn:ngsi-ld:EOSatelliteImagery:tenant-a:parcel:p1:s2-indices",
])
def test_sdm_identifier_outside_the_format_is_skipped(bad_id, caplog):
    entity = sdm_entity()
    entity["id"] = bad_id
    with caplog.at_level(logging.WARNING):
        assert parse_eo_entities([entity]) == []
    assert "identifier format" in caplog.text


def test_sdm_unreadable_value_is_left_out(caplog):
    entity = sdm_entity(analytics=[{"name": "ndvi", "value": "NaN"}, {"name": "evi", "value": "0.2"}, {"value": "1"}])
    with caplog.at_level(logging.WARNING):
        assert only(parse_eo_entities([entity])).values == {"evi": 0.2}
    assert "ndvi" in caplog.text


def test_sdm_entity_without_sensing_date_is_skipped(caplog):
    entity = sdm_entity()
    del entity["sensingDate"]
    with caplog.at_level(logging.WARNING):
        assert parse_eo_entities([entity]) == []
    assert "sensingDate" in caplog.text


@pytest.mark.parametrize("stamp", ["2026-05-01T23:30:00-02:00", "2026-05-01T10:00:00", "2026-05-01Tnoon",
                                   "2026-05-01T10:00:00+02:00Z", "2026-05-01TnoonZ"])
def test_sdm_time_that_is_not_utc_is_ignored_and_the_day_kept(stamp, caplog):
    with caplog.at_level(logging.WARNING):
        obs = only(parse_eo_entities([sdm_entity(sensingDate=prop(stamp))]))
    assert (obs.sensing_day, obs.sensing_time) == (date(2026, 5, 1), None)
    assert "not UTC" in caplog.text


def test_sdm_relations_that_contradict_the_identifier_are_skipped(caplog):
    parcel_with_zone = sdm_entity(hasAgriManagementZone={"type": "Relationship", "object": ZONE})
    zone_without_relation = sdm_entity()
    zone_without_relation["id"] = "urn:ngsi-ld:EOSatelliteImagery:tenant-a:zone:z1:s2-indices:2026-05-01"
    with caplog.at_level(logging.WARNING):
        assert parse_eo_entities([parcel_with_zone, zone_without_relation]) == []
    assert caplog.text.count("contradict") == 2


@pytest.mark.parametrize("stamp", [
    "2026-W18-5", "20260501", "01/05/2026", "", None, 20260501,
    {"@type": "DateTime", "@value": "2026-05-01T10:00:00Z"},
])
def test_sdm_sensing_date_must_be_a_plain_string_starting_with_the_day(stamp, caplog):
    with caplog.at_level(logging.WARNING):
        assert parse_eo_entities([sdm_entity(sensingDate=prop(stamp))]) == []
    assert "plain ISO 8601" in caplog.text


def test_items_that_are_not_entities_are_skipped(caplog):
    with caplog.at_level(logging.WARNING):
        found = parse_eo_entities([None, "x", {"type": "EOProduct", "ndvi": prop(0.4)}, {"id": "", "type": "EOProduct"},
                                   sdm_entity()])
    assert len(found) == 1
    assert caplog.text.count("without an identifier") == 4


def test_an_entity_listed_twice_is_read_once():
    assert len(parse_eo_entities([sdm_entity(), sdm_entity()])) == 1


# ── selection ────────────────────────────────────────────────────────────────


def test_new_shape_replaces_the_earlier_one_for_the_same_target_family_and_day():
    old = legacy_entity(ndvi=prop(0.4), sarMoisture=prop(0.5))
    new = sdm_entity()
    found = parse_eo_entities([old, new])
    assert [(o.shape, o.family) for o in found] == [("legacy", "s1-soil-moisture"), ("sdm", "s2-indices")]


def test_names_bring_their_statistics_and_drop_empty_observations(legacy):
    found = parse_eo_entities(legacy, names={"ndvi"})
    assert found and all(set(n.split(".")[0] for n in o.values) == {"ndvi"} for o in found)
    assert {o.family for o in found} == {"s2-indices"}
    first = only(found, sensing_day=date(2025, 12, 8))
    assert "ndvi.interiorMean" in first.values
    assert set(first.legacy_raster_urls) == {"ndvi"}


def test_require_keeps_only_observations_holding_every_required_value():
    with_ndvi = sdm_entity(day="2026-05-01")
    without_ndvi = sdm_entity(day="2026-05-02", analytics=[{"name": "evi", "value": "0.2"}, {"name": "pixelCount", "value": "9"}])
    both = [with_ndvi, without_ndvi]
    loose = parse_eo_entities(both, names={"ndvi", "pixelCount"})
    assert [o.sensing_day.day for o in loose] == [1, 2] and "ndvi" not in loose[1].values
    strict = parse_eo_entities(both, names={"ndvi", "pixelCount"}, require={"ndvi"})
    assert [o.sensing_day.day for o in strict] == [1]
    assert strict[0].values == {"ndvi": 0.5, "pixelCount": 10}
    assert [o.sensing_day.day for o in parse_eo_entities(both, require={"ndvi", "evi"})] == []


@pytest.mark.parametrize("argument", ["names", "require", "families"])
def test_a_bare_string_is_refused(argument, legacy):
    with pytest.raises(TypeError, match="not a string"):
        parse_eo_entities(legacy, **{argument: "ndvi"})


@pytest.mark.parametrize("bad", ["NDVI", "ndvi.interiorMean", "sarMoisture"])
def test_an_unknown_value_name_is_refused(bad, legacy):
    with pytest.raises(ValueError, match="unknown name in"):
        parse_eo_entities(legacy, names={bad})
    with pytest.raises(ValueError, match="unknown name in"):
        parse_eo_entities(legacy, require={bad})


@pytest.mark.parametrize("argument", ["names", "require", "families"])
def test_an_iterator_argument_is_read_once(argument, legacy, sdm):
    value = "s2-indices" if argument == "families" else "ndvi"
    as_set = parse_eo_entities(legacy + sdm, **{argument: {value}})
    assert as_set and parse_eo_entities(legacy + sdm, **{argument: (v for v in [value])}) == as_set
    orion = FakeOrion(legacy + sdm)
    assert run(fetch_eo_observations(orion, parcel_id=PARCEL, **{argument: iter([value])})) == \
        run(fetch_eo_observations(FakeOrion(legacy + sdm), parcel_id=PARCEL, **{argument: {value}}))


@pytest.mark.parametrize("argument", ["names", "require", "families"])
def test_an_empty_collection_is_refused(argument, legacy):
    with pytest.raises(ValueError, match="is empty"):
        parse_eo_entities(legacy, **{argument: set()})


def test_value_names_cover_everything_the_fixtures_hold(legacy, sdm):
    held = {name.split(".")[0] for o in parse_eo_entities(legacy + sdm) for name in o.values}
    assert held <= set(EO_VALUE_NAMES)


def test_families_filter_and_unknown_family(legacy):
    assert {o.family for o in parse_eo_entities(legacy, families={"s1-backscatter"})} == {"s1-backscatter"}
    with pytest.raises(ValueError, match="unknown name in families"):
        parse_eo_entities(legacy, families={"s2"})


def test_observations_come_oldest_first(legacy, sdm):
    days = [o.sensing_day for o in parse_eo_entities(legacy + sdm)]
    assert days == sorted(days)


def test_families_constant_matches_the_contract():
    assert EO_FAMILIES == ("s2-indices", "s2-composite", "s1-backscatter", "s1-soil-moisture",
                           "s1-change", "landsat-lst", "clms-lst")


# ── reading from the broker ──────────────────────────────────────────────────


def _stored(entity, name):
    attr = entity.get(name)
    if isinstance(attr, dict):
        return attr.get("object", attr.get("value"))
    return attr


_TERM = re.compile(r'^(?P<name>\w+)(?P<op>==|>=|<)"(?P<value>[^"]*)"$')


def matches(entity, q):
    """The filters this reader sends, evaluated on short names as the broker does with the platform context."""
    for term in (q or "").split(";"):
        if not term:
            continue
        if term.startswith("!"):
            if term[1:] in entity:
                return False
            continue
        parsed = _TERM.match(term)
        assert parsed, f"filter not understood by the fake broker: {term}"
        stored = _stored(entity, parsed["name"])
        if not isinstance(stored, str):
            return False
        if parsed["op"] == "==" and stored != parsed["value"]:
            return False
        if parsed["op"] == ">=" and not stored >= parsed["value"]:
            return False
        if parsed["op"] == "<" and not stored < parsed["value"]:
            return False
    return True


class FakeOrion:
    """Serves stored entities by type and filter, one page at a time, and records what was asked.

    ``hasAgriParcel`` and ``refAgriParcel`` are one attribute for the broker; the fixtures hold the
    second spelling, so a filter on the first is tried on both.
    """

    def __init__(self, entities, sync=False):
        self.entities = entities
        self.calls = []
        self.sync = sync

    def _page(self, type=None, q=None, limit=100, offset=0, attrs=None, options=None):
        self.calls.append({"type": type, "q": q, "limit": limit, "offset": offset, "options": options})
        hits = []
        for entity in self.entities:
            if entity["type"] != type:
                continue
            aliased = {**entity, "hasAgriParcel": entity.get("hasAgriParcel", entity.get("refAgriParcel"))}
            if aliased["hasAgriParcel"] is None:
                del aliased["hasAgriParcel"]
            if matches(aliased, q):
                hits.append(entity)
        return hits[offset:offset + limit]

    def query_entities(self, **kwargs):
        if self.sync:
            return self._page(**kwargs)

        async def answer():
            return self._page(**kwargs)

        return answer()


def unfiltered(fetch_kwargs, entities):
    """What the reader returns when the broker applies no filter at all."""

    class Everything(FakeOrion):
        def _page(self, type=None, q=None, **kwargs):
            return super()._page(type=type, q=None, **kwargs)

    return run(fetch_eo_observations(Everything(entities), **fetch_kwargs))


def run(coro):
    return asyncio.run(coro)


def test_fetch_parcel_leaves_zones_out_and_asks_both_types(legacy, sdm):
    orion = FakeOrion(legacy + sdm)
    found = run(fetch_eo_observations(orion, parcel_id=PARCEL))
    assert found and all(o.target == "parcel" and o.parcel_id == PARCEL for o in found)
    assert [(c["type"], c["q"]) for c in orion.calls] == [
        ("EOSatelliteImagery", f'hasAgriParcel=="{PARCEL}";!hasAgriManagementZone'),
        ("EOProduct", f'hasAgriParcel=="{PARCEL}"'),
    ]
    assert all(c["options"] is None for c in orion.calls)


def test_fetch_zone(legacy, sdm):
    orion = FakeOrion(legacy + sdm)
    found = run(fetch_eo_observations(orion, zone_id=ZONE))
    assert found and all(o.target == "zone" and o.zone_id == ZONE for o in found)
    by_name = [f'hasAgriManagementZone=="{ZONE}"', f'hasAgriParcelZone=="{ZONE}"']
    assert [(c["type"], c["q"]) for c in orion.calls] == [
        ("EOSatelliteImagery", by_name[0]), ("EOSatelliteImagery", by_name[1]),
        ("EOProduct", by_name[0]), ("EOProduct", by_name[1]),
    ]
    assert len(found) == len({(o.entity_id, o.family) for o in found})


def test_fetch_whole_tenant_returns_parcels_only(legacy, sdm):
    orion = FakeOrion(legacy + sdm)
    found = run(fetch_eo_observations(orion))
    assert found and {o.target for o in found} == {"parcel"}
    assert [c["q"] for c in orion.calls] == [None, None]


def test_fetch_window_is_applied_to_both_shapes_and_only_asked_of_the_new_one(legacy, sdm):
    orion = FakeOrion(legacy + sdm)
    found = run(fetch_eo_observations(orion, parcel_id=PARCEL, since=date(2026, 10, 21), until=date(2026, 10, 31)))
    assert {o.sensing_day for o in found} == {date(2026, 10, 21), date(2026, 10, 31)}
    assert orion.calls[0]["q"] == (
        f'hasAgriParcel=="{PARCEL}";!hasAgriManagementZone;sensingDate>="2026-10-21";sensingDate<"2026-11-01"'
    )
    assert orion.calls[1]["q"] == f'hasAgriParcel=="{PARCEL}"'


def test_fetch_never_asks_the_broker_for_the_family(legacy, sdm):
    orion = FakeOrion(legacy + sdm)
    found = run(fetch_eo_observations(orion, parcel_id=PARCEL, families={"s1-backscatter"}))
    assert {o.family for o in found} == {"s1-backscatter"}
    assert all("analysisType" not in (c["q"] or "") for c in orion.calls)


@pytest.mark.parametrize("kwargs", [
    {"parcel_id": PARCEL},
    {"parcel_id": PARCEL, "since": date(2026, 10, 31)},
    {"parcel_id": PARCEL, "until": date(2026, 10, 31), "latest": True},
    {"parcel_id": PARCEL, "since": date(2026, 11, 5), "until": date(2026, 11, 5)},
    {"zone_id": ZONE, "since": date(2026, 11, 1)},
    {"since": date(2026, 10, 1), "names": {"ndvi"}, "latest": True},
])
def test_broker_filters_never_change_the_answer(kwargs, legacy, sdm):
    odd = [
        sdm_entity(day="2026-10-30", sensingDate=prop("2026-10-30T23:59:59Z")),
        sdm_entity(day="2026-11-01", sensingDate=prop("2026-11-01")),
        sdm_entity(day="2026-10-29", sensingDate=prop("2026-W44-4")),
        sdm_entity(day="2026-10-28", sensingDate=prop("20261028")),
        sdm_entity(day="2026-10-27", sensingDate=prop({"@type": "DateTime", "@value": "2026-10-27T10:00:00Z"})),
        sdm_entity(day="2026-10-26", sensingDate=prop("2026-10-26 10:00")),
    ]
    stored = legacy + sdm + odd
    assert run(fetch_eo_observations(FakeOrion(stored), **kwargs)) == unfiltered(kwargs, stored)


def test_fetch_parcel_keeps_other_parcels_out_even_if_the_broker_returns_them():
    other = "urn:ngsi-ld:AgriParcel:00000009-0000-4000-8000-000000000009"
    theirs = legacy_entity(day="2026-05-02", suffix="9", ndvi=prop(0.9))
    theirs["refAgriParcel"] = {"type": "Relationship", "object": other}
    stored = [legacy_entity(ndvi=prop(0.4)), theirs]
    assert [o.parcel_id for o in unfiltered({"parcel_id": PARCEL}, stored)] == [PARCEL]
    assert [o.parcel_id for o in unfiltered({"parcel_id": other}, stored)] == [other]
    assert len(unfiltered({}, stored)) == 2


def test_fetch_whole_tenant_latest_is_per_parcel():
    other = "urn:ngsi-ld:AgriParcel:00000009-0000-4000-8000-000000000009"
    mine = [sdm_entity(day="2026-05-01"), sdm_entity(day="2026-05-03")]
    theirs = sdm_entity(day="2026-05-02")
    theirs["id"] = theirs["id"].replace("00000001-0000-4000-8000-000000000001", "00000009-0000-4000-8000-000000000009")
    theirs["hasAgriParcel"] = {"type": "Relationship", "object": other}
    found = run(fetch_eo_observations(FakeOrion(mine + [theirs]), names={"ndvi"}, latest=True))
    assert [(o.parcel_id, o.sensing_day.day) for o in found] == [(other, 2), (PARCEL, 3)]


def test_fetch_reads_an_entity_once_when_pages_overlap(monkeypatch):
    monkeypatch.setattr("nkz_platform_sdk.eo._PAGE", 2)
    entities = [legacy_entity(day=f"2026-05-{d:02d}", ndvi=prop(0.4)) for d in range(1, 4)]

    class Overlapping(FakeOrion):
        def _page(self, **kwargs):
            hits = super()._page(**kwargs)
            return hits if kwargs.get("offset") == 0 else [self.entities[1], *hits]

    assert len(run(fetch_eo_observations(Overlapping(entities), parcel_id=PARCEL))) == 3


def test_fetch_latest_is_per_family_and_needs_a_requested_name(legacy, sdm):
    orion = FakeOrion(legacy + sdm)
    found = run(fetch_eo_observations(orion, parcel_id=PARCEL, names={"ndvi", "backscatterVV"}, latest=True))
    assert [(o.family, o.sensing_day, o.shape) for o in found] == [
        ("s2-indices", date(2026, 11, 5), "sdm"),
        ("s1-backscatter", date(2026, 11, 9), "legacy"),
    ]


def test_fetch_latest_before_a_day(legacy, sdm):
    orion = FakeOrion(legacy + sdm)
    found = run(fetch_eo_observations(orion, parcel_id=PARCEL, names={"ndvi"}, until=date(2026, 10, 30), latest=True))
    assert [o.sensing_day for o in found] == [date(2026, 10, 21)]


def test_fetch_until_the_last_possible_day_sends_no_upper_bound(legacy, sdm):
    orion = FakeOrion(legacy + sdm)
    found = run(fetch_eo_observations(orion, parcel_id=PARCEL, until=date.max))
    assert found == run(fetch_eo_observations(FakeOrion(legacy + sdm), parcel_id=PARCEL))
    assert "sensingDate" not in orion.calls[0]["q"]


def test_fetch_reads_every_page(monkeypatch):
    monkeypatch.setattr("nkz_platform_sdk.eo._PAGE", 2)
    entities = [legacy_entity(day=f"2026-05-{d:02d}", ndvi=prop(0.4)) for d in range(1, 6)]
    orion = FakeOrion(entities)
    found = run(fetch_eo_observations(orion, parcel_id=PARCEL))
    assert len(found) == 5
    assert [c["offset"] for c in orion.calls if c["type"] == "EOProduct"] == [0, 2, 4]


def test_fetch_refuses_a_listing_it_cannot_finish(monkeypatch):
    monkeypatch.setattr("nkz_platform_sdk.eo._PAGE", 2)
    monkeypatch.setattr("nkz_platform_sdk.eo._MAX_PAGES", 2)
    entities = [legacy_entity(day=f"2026-05-{d:02d}", ndvi=prop(0.4)) for d in range(1, 6)]
    with pytest.raises(EOReadIncomplete):
        run(fetch_eo_observations(FakeOrion(entities), parcel_id=PARCEL, latest=True))


def test_fetch_propagates_a_broker_failure():
    class Broken:
        async def query_entities(self, **_):
            raise RuntimeError("503")

    with pytest.raises(RuntimeError, match="503"):
        run(fetch_eo_observations(Broken(), parcel_id=PARCEL))


@pytest.mark.parametrize("kwargs", [
    {"parcel_id": PARCEL, "zone_id": ZONE},
    {"parcel_id": "00000001-0000-4000-8000-000000000001"},
    {"zone_id": 'urn:x"|type=="y'},
    {"parcel_id": PARCEL, "families": {"nope"}},
    {"parcel_id": PARCEL, "names": {"NDVI"}},
    {"parcel_id": PARCEL, "names": "ndvi"},
    {"parcel_id": PARCEL, "require": {"ndvi.min"}},
    {"parcel_id": PARCEL, "since": datetime(2026, 10, 1, tzinfo=timezone.utc)},
    {"parcel_id": PARCEL, "until": "2026-10-01"},
    {"parcel_id": PARCEL, "since": date(2026, 10, 2), "until": date(2026, 10, 1)},
    {"parcel_id": PARCEL, "latest": "no"},
    {"parcel_id": PARCEL, "names": set()},
    {"parcel_id": PARCEL + "\n"},
    {"parcel_id": PARCEL + ";type"},
    {"zone_id": ZONE + "|x"},
    {"zone_id": ZONE + "\\"},
    {"parcel_id": 5},
])
def test_fetch_rejects_bad_arguments_before_asking(kwargs):
    orion = FakeOrion([])
    with pytest.raises((ValueError, TypeError)):
        run(fetch_eo_observations(orion, **kwargs))
    assert orion.calls == []


def test_sync_reader_gives_the_same_answer(legacy, sdm):
    kwargs = dict(parcel_id=PARCEL, names={"ndvi"}, since=date(2026, 10, 1))
    expected = run(fetch_eo_observations(FakeOrion(legacy + sdm), **kwargs))
    orion = FakeOrion(legacy + sdm, sync=True)
    assert fetch_eo_observations_sync(orion, **kwargs) == expected
    assert isinstance(expected[0], EOObservation)


def test_exported_from_the_package():
    import nkz_platform_sdk

    for name in ("EOObservation", "EOReadIncomplete", "EO_FAMILIES", "EO_VALUE_NAMES", "fetch_eo_observations",
                 "fetch_eo_observations_sync", "parse_eo_entities"):
        assert name in nkz_platform_sdk.__all__ and hasattr(nkz_platform_sdk, name)
