"""Contract for `blueprints.measurements.build_measurements`.

`build_measurements(entity, profile) -> list[dict]` turns a device entity notification into
`DeviceMeasurement` entities. Pure function — no Flask app, no DB, no Orion.

⚠️ The entity-manager suite stubs `common` in `sys.modules` globally, and that stub is
shared state across test files (see `tests/test_notifications.py`). This module needs the
REAL `common.unit_codes.to_unit_code` (Task 1), not a mock, so it loads that one submodule
from its actual file and registers it under the exact dotted key `common.unit_codes` *before*
importing `blueprints.measurements`. Python's import system resolves an exact
`sys.modules['common.unit_codes']` hit directly, without ever touching the `common` parent
package — so this works whether `common` itself is later stubbed to a MagicMock by another
test file or not (see `tests/test_sensor_canonical_entity.py` for the same
who-runs-first-doesn't-matter approach, applied to the parent instead of a submodule here).
"""

import importlib.util
import os
import sys

import pytest

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")

_test_dir = os.path.dirname(os.path.abspath(__file__))
_em_dir = os.path.normpath(os.path.join(_test_dir, ".."))
_services_dir = os.path.normpath(os.path.join(_em_dir, ".."))
for _p in (_em_dir, _services_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# `common` may already be a MagicMock from another test file, or may not exist in
# sys.modules at all yet — don't clobber it either way (`setdefault`, matching
# test_sensor_canonical_entity.py). `blueprints.measurements` only ever reaches into
# `common.unit_codes`, so the parent's identity doesn't actually matter for this file.
from unittest.mock import MagicMock

sys.modules.setdefault("common", MagicMock())

# The one submodule that DOES matter: load the real file and pin it under its exact dotted
# name. This is a direct `sys.modules` hit for `common.unit_codes`, so it wins regardless of
# what `common` resolves to — see module docstring.
_unit_codes_path = os.path.join(_services_dir, "common", "unit_codes.py")
_uc_spec = importlib.util.spec_from_file_location("common.unit_codes", _unit_codes_path)
_uc_mod = importlib.util.module_from_spec(_uc_spec)
assert _uc_spec.loader is not None
_uc_spec.loader.exec_module(_uc_mod)
sys.modules["common.unit_codes"] = _uc_mod

from blueprints.measurements import DeviceIdShapeError, build_measurements  # noqa: E402

DEVICE_ID = "urn:ngsi-ld:Device:montiko:sensor-001"
PARCEL_ID = "urn:ngsi-ld:AgriParcel:montiko:p1"

PROFILE = {
    "id": 1,
    "sdm_entity_type": "Device",
    "mapping": {
        "version": 1,
        "measurements": [
            {"type": "leafTemperature", "unit": "°C", "sdmAttribute": "leafTemperature"},
            {"type": "soilMoisture", "unit": "%", "sdmAttribute": "soilMoisture"},
            {"type": "batteryLevel", "unit": "", "sdmAttribute": "batteryLevel"},
            {"type": "deviceStatus", "unit": "", "sdmAttribute": "operationalStatus"},
            {"type": "activeState", "unit": "boolean", "sdmAttribute": "isActive"},
        ],
    },
}


def _entity(**extra_attrs):
    base = {
        "id": DEVICE_ID,
        "type": "Device",
        "name": {"type": "Property", "value": "Sensor 1"},
        "location": {
            "type": "GeoProperty",
            "value": {"type": "Point", "coordinates": [1, 2]},
        },
        "controlledAsset": {"type": "Relationship", "object": PARCEL_ID},
        "status": {"type": "Property", "value": "active"},
        "leafTemperature": {
            "type": "Property",
            "value": 21.5,
            "observedAt": "2026-08-29T10:00:00Z",
        },
        "soilMoisture": {
            "type": "Property",
            "value": 42,
            "observedAt": "2026-08-29T10:00:00Z",
        },
    }
    base.update(extra_attrs)
    return base


def _by_controlled_property(measurements, name):
    for m in measurements:
        if m["controlledProperty"]["value"] == name:
            return m
    raise AssertionError(f"no DeviceMeasurement for controlledProperty={name!r}")


class TestTwoMeasurementAttributes:
    def test_two_measurement_attrs_produce_two_entities(self):
        result = build_measurements(_entity(), PROFILE)
        names = {m["controlledProperty"]["value"] for m in result}
        assert names == {"leafTemperature", "soilMoisture"}
        assert len(result) == 2
        for m in result:
            assert m["type"] == "DeviceMeasurement"

    def test_measurement_type_comes_from_profile_type(self):
        result = build_measurements(_entity(), PROFILE)
        leaf = _by_controlled_property(result, "leafTemperature")
        assert leaf["measurementType"]["value"] == "leafTemperature"


class TestStableId:
    def test_id_is_stable_across_repeated_calls(self):
        first = build_measurements(_entity(), PROFILE)
        second = build_measurements(_entity(), PROFILE)
        first_ids = sorted(m["id"] for m in first)
        second_ids = sorted(m["id"] for m in second)
        assert first_ids == second_ids

    def test_id_scheme_is_tenant_externalid_property(self):
        result = build_measurements(_entity(), PROFILE)
        leaf = _by_controlled_property(result, "leafTemperature")
        assert leaf["id"] == "urn:ngsi-ld:DeviceMeasurement:montiko:sensor-001:leafTemperature"


class TestRefDevice:
    def test_ref_device_points_to_device_not_parcel(self):
        result = build_measurements(_entity(), PROFILE)
        leaf = _by_controlled_property(result, "leafTemperature")
        assert leaf["refDevice"] == {"type": "Relationship", "object": DEVICE_ID}
        assert leaf["refDevice"]["object"] != PARCEL_ID


class TestUnitCode:
    def test_unit_code_is_uncefact_not_raw_symbol(self):
        result = build_measurements(_entity(), PROFILE)
        leaf = _by_controlled_property(result, "leafTemperature")
        assert leaf["numValue"]["unitCode"] == "CEL"
        assert leaf["numValue"]["value"] == 21.5

    def test_no_unit_code_when_profile_unit_is_empty_string(self):
        entity = _entity(batteryLevel={"type": "Property", "value": 87})
        result = build_measurements(entity, PROFILE)
        battery = _by_controlled_property(result, "batteryLevel")
        assert "unitCode" not in battery["numValue"]
        assert battery["numValue"]["value"] == 87


class TestUndeclaredAttributesIgnored:
    def test_identity_and_metadata_attrs_are_not_measurements(self):
        result = build_measurements(_entity(), PROFILE)
        produced = {m["controlledProperty"]["value"] for m in result}
        assert produced.isdisjoint({"name", "location", "controlledAsset", "status"})

    def test_id_and_type_never_produce_a_measurement(self):
        result = build_measurements(_entity(), PROFILE)
        produced = {m["controlledProperty"]["value"] for m in result}
        assert produced.isdisjoint({"id", "type"})


class TestNonNumericValue:
    def test_string_value_goes_to_text_value_not_num_value(self):
        entity = _entity(operationalStatus={"type": "Property", "value": "ok"})
        result = build_measurements(entity, PROFILE)
        status = _by_controlled_property(result, "operationalStatus")
        assert status["textValue"]["value"] == "ok"
        assert "numValue" not in status

    def test_boolean_value_goes_to_text_value_not_num_value(self):
        # bool is a subclass of int in Python — must not slip into numValue.
        entity = _entity(isActive={"type": "Property", "value": True})
        result = build_measurements(entity, PROFILE)
        active = _by_controlled_property(result, "isActive")
        assert active["textValue"]["value"] is True
        assert "numValue" not in active
        # unitCode is defined only ever to live on numValue (see plan Task 2 schema); a boolean
        # routed to textValue must not carry one even though "boolean" maps to C62.
        assert "unitCode" not in active["textValue"]


class TestUnknownUnitFailsLoud:
    def test_unknown_unit_raises_not_silently_passes(self):
        profile = {
            "id": 2,
            "mapping": {
                "measurements": [
                    {"type": "windGust", "unit": "furlongs/fortnight", "sdmAttribute": "windGust"}
                ]
            },
        }
        entity = _entity(windGust={"type": "Property", "value": 3.2})
        with pytest.raises(ValueError):
            build_measurements(entity, profile)


class TestDeviceIdShape:
    def test_malformed_device_id_raises_explicitly(self):
        entity = _entity()
        entity["id"] = "not-a-urn-at-all"
        with pytest.raises(DeviceIdShapeError):
            build_measurements(entity, PROFILE)

    def test_too_few_segments_raises_explicitly(self):
        entity = _entity()
        entity["id"] = "urn:ngsi-ld:Device"
        with pytest.raises(DeviceIdShapeError):
            build_measurements(entity, PROFILE)
