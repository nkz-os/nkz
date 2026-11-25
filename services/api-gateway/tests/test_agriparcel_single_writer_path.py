"""The AgriParcel single-writer guard matches the entity id, not attribute names.

Writes to a parcel entity are reserved for entity-manager. Other entities may
still carry a parcel relationship, and removing it targets an attribute path
such as `/attrs/hasAgriParcel`, which must reach the broker.
"""

import pytest

PARCEL_ID = "urn:ngsi-ld:AgriParcel:tenant-a:p1"
DEVICE_ID = "urn:ngsi-ld:Device:tenant-a:d1"


def _guard(gw, path, method):
    with gw.app.test_request_context(path, method=method):
        return gw.enforce_agriparcel_single_writer()


@pytest.mark.parametrize("prefix", ["/ngsi-ld/v1/entities", "/api/ngsi-ld/v1/entities"])
@pytest.mark.parametrize("suffix", ["", "/attrs", "/attrs/name"])
@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_writes_to_a_parcel_entity_are_blocked(prefix, suffix, method):
    import fiware_api_gateway as gw

    result = _guard(gw, f"{prefix}/{PARCEL_ID}{suffix}", method)

    assert result is not None
    assert result[1] == 403


@pytest.mark.parametrize("prefix", ["/ngsi-ld/v1/entities", "/api/ngsi-ld/v1/entities"])
@pytest.mark.parametrize(
    "suffix, method",
    [("", "PATCH"), ("/attrs/hasAgriParcel", "DELETE"), ("/attrs/refAgriParcel", "DELETE")],
)
def test_parcel_relationship_on_another_entity_is_allowed(prefix, suffix, method):
    import fiware_api_gateway as gw

    assert _guard(gw, f"{prefix}/{DEVICE_ID}{suffix}", method) is None


@pytest.mark.parametrize("prefix", ["/ngsi-ld/v1/entities", "/api/ngsi-ld/v1/entities"])
@pytest.mark.parametrize("name", ["AgriParcelOperation", "AgriParcelRecord", "AgriParcelZone"])
@pytest.mark.parametrize("suffix", ["", "/attrs", "/attrs/name"])
@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_types_that_start_with_the_parcel_name_are_allowed(prefix, name, suffix, method):
    import fiware_api_gateway as gw

    assert _guard(gw, f"{prefix}/urn:ngsi-ld:{name}:tenant-a:1{suffix}", method) is None


@pytest.mark.parametrize(
    "entity_id",
    ["urn%253Angsi-ld%253AAgriParcel%253A1", "urn:ngsi-ld:%2541griParcel:1", "AgriParcel-1"],
)
def test_encoded_and_unreadable_parcel_ids_are_blocked(entity_id):
    import fiware_api_gateway as gw

    result = _guard(gw, f"/ngsi-ld/v1/entities/{entity_id}", "PATCH")

    assert result is not None
    assert result[1] == 403
