"""Pure rules of the AgriParcel write guard."""

import pytest
from parcel_guard import entity_type_from_id, is_parcel_id


@pytest.mark.parametrize("name", ["AgriParcelOperation", "AgriParcelRecord", "AgriParcelZone"])
def test_other_types_are_not_parcels(name):
    assert entity_type_from_id(f"urn:ngsi-ld:{name}:tenant-a:1") == name
    assert is_parcel_id(f"urn:ngsi-ld:{name}:tenant-a:1") is False


@pytest.mark.parametrize(
    "entity_id",
    [
        "urn:ngsi-ld:AgriParcel:tenant-a:1",
        "urn%3Angsi-ld%3AAgriParcel%3A1",
        "urn%253Angsi-ld%253AAgriParcel%253A1",
        "urn:ngsi-ld:%41griParcel:1",
        "AgriParcel-1",
        "urn:ngsi-ld:AgriParcel",
        "urn:ngsi-ld:AgriParcel%3Fx:1",
    ],
)
def test_parcels_and_unreadable_ids_match(entity_id):
    assert is_parcel_id(entity_id) is True


def test_parcel_name_after_the_type_does_not_match():
    assert is_parcel_id("urn:ngsi-ld:Device:AgriParcel:1") is False


@pytest.mark.parametrize("entity_id", ["", "Device-1", "urn:ngsi-ld:Device:1"])
def test_ids_without_the_name_do_not_match(entity_id):
    assert is_parcel_id(entity_id) is False
