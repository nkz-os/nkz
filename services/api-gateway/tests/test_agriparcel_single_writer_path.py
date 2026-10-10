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
