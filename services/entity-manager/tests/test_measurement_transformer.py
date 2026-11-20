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

from tests.real_common import seed_real_common  # noqa: E402

seed_real_common(("context_aliases", "window_statistics"))

from blueprints.measurements import DeviceIdShapeError, build_measurements  # noqa: E402

DEVICE_ID = "urn:ngsi-ld:Device:acmefarm:sensor-001"
PARCEL_ID = "urn:ngsi-ld:AgriParcel:acmefarm:p1"

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
        assert leaf["id"] == "urn:ngsi-ld:DeviceMeasurement:acmefarm:sensor-001:leafTemperature"


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


class TestObservedAt:
    def test_attribute_observed_at_wins_over_wall_clock(self):
        entity = _entity(
            leafTemperature={
                "type": "Property",
                "value": 21.5,
                "observedAt": "2026-01-01T00:00:00Z",
            }
        )
        result = build_measurements(entity, PROFILE)
        leaf = _by_controlled_property(result, "leafTemperature")
        assert leaf["dateObserved"]["value"] == "2026-01-01T00:00:00Z"


class TestWindowStatistics:
    @staticmethod
    def _profile(statistics):
        m = {"type": "temp", "unit": "\u00b0C", "sdmAttribute": "airTemperature"}
        if statistics:
            m["statistics"] = statistics
        return {"id": 2, "sdm_entity_type": "Device", "mapping": {"version": 1, "measurements": [m]}}

    @staticmethod
    def _entity_min():
        return _entity(airTemperatureMin={"type": "Property", "value": 3.1,
                                          "observedAt": "2026-08-29T10:00:00Z"})

    def test_derived_attribute_becomes_a_measurement(self):
        result = build_measurements(self._entity_min(), self._profile(["mean", "min"]))
        m = _by_controlled_property(result, "airTemperatureMin")
        assert m["id"].endswith(":airTemperatureMin")

    def test_without_statistics_the_derived_attribute_is_skipped(self):
        result = build_measurements(self._entity_min(), self._profile(None))
        assert all(m["controlledProperty"]["value"] != "airTemperatureMin" for m in result)


class TestProvenance:
    def test_time_source_device_from_attribute_observed_at(self):
        result = build_measurements(_entity(), PROFILE)
        m = _by_controlled_property(result, "leafTemperature")
        assert m["timeSource"] == {"type": "Property", "value": "device"}
        assert m["dateObserved"]["value"] == "2026-08-29T10:00:00Z"

    def test_time_source_device_from_entity_observed_at(self):
        entity = _entity(
            observedAt={"type": "Property", "value": "2026-08-29T11:00:00Z"},
            leafTemperature={"type": "Property", "value": 20.0},
        )
        m = _by_controlled_property(build_measurements(entity, PROFILE), "leafTemperature")
        assert m["timeSource"]["value"] == "device"
        assert m["dateObserved"]["value"] == "2026-08-29T11:00:00Z"

    def test_time_source_server_on_fallback(self):
        entity = _entity(leafTemperature={"type": "Property", "value": 20.0})
        m = _by_controlled_property(build_measurements(entity, PROFILE), "leafTemperature")
        assert m["timeSource"] == {"type": "Property", "value": "server"}

    def test_device_type_carries_the_device_profile_code(self):
        # SDM Device.deviceType on the DeviceMeasurement; the source stays the Device's profileCode.
        entity = _entity(profileCode={"type": "Property", "value": "bench-x"})
        for m in build_measurements(entity, PROFILE):
            assert m["deviceType"] == {"type": "Property", "value": "bench-x"}
            assert "profileCode" not in m

    def test_device_type_absent_when_entity_lacks_profile_code(self):
        for m in build_measurements(_entity(), PROFILE):
            assert "deviceType" not in m
            assert "profileCode" not in m


FLAG = "DEVICE_MEASUREMENT_SDM_SHAPE_ENABLED"

STAT_PROFILE = {
    "id": 3,
    "sdm_entity_type": "Device",
    "mapping": {
        "measurements": [
            {"type": "airTemp", "unit": "°C", "sdmAttribute": "airTemperature",
             "observedProperty": "airTemperature", "statistics": ["mean", "min"]},
            {"type": "windGust", "unit": "m/s", "sdmAttribute": "windGust",
             "statistics": ["max"]},
            {"type": "rainTotal", "unit": "mm", "sdmAttribute": "rain",
             "observedProperty": "precipitation", "statistics": ["sum"]},
            {"type": "lastTemp", "unit": "°C", "sdmAttribute": "soilTemperature",
             "statistics": ["last"]},
            {"type": "soilMoisture", "unit": "%", "sdmAttribute": "soilMoisture",
             "observedProperty": "soilMoisture"},
        ]
    },
}


def _stat_entity():
    attrs = {}
    for name in ("airTemperature", "airTemperatureMin", "windGustMax", "rain",
                 "soilTemperature", "soilMoisture"):
        attrs[name] = {"type": "Property", "value": 1.5, "observedAt": "2026-08-29T10:00:00Z"}
    return _entity(
        profileCode={"type": "Property", "value": "bench-x"}, **attrs
    )


def _by_id_channel(result, channel):
    for m in result:
        if m["id"].endswith(":" + channel):
            return m
    raise AssertionError(f"no DeviceMeasurement for channel {channel!r}")


class TestSdmShapeFlag:
    def test_flag_off_legacy_shape(self, monkeypatch):
        monkeypatch.delenv(FLAG, raising=False)
        result = build_measurements(_stat_entity(), STAT_PROFILE)
        m = _by_id_channel(result, "airTemperatureMin")
        assert m["controlledProperty"] == {"type": "Property", "value": "airTemperatureMin"}
        assert "name" not in m
        assert m["measurementType"] == {"type": "Property", "value": "airTemp"}
        assert m["deviceType"] == {"type": "Property", "value": "bench-x"}
        assert m["timeSource"] == {"type": "Property", "value": "device"}

    def test_flag_on_sdm_shape_instant(self, monkeypatch):
        monkeypatch.setenv(FLAG, "true")
        result = build_measurements(_stat_entity(), STAT_PROFILE)
        m = _by_id_channel(result, "soilMoisture")
        assert m["controlledProperty"] == {"type": "Property", "value": "soilMoisture"}
        assert m["name"] == {"type": "Property", "value": "soilMoisture"}
        assert m["measurementType"] == {"type": "Property", "value": "instant"}
        assert m["deviceType"] == {"type": "Property", "value": "bench-x"}
        assert m["timeSource"]["value"] == "device"
        assert m["id"].endswith(":soilMoisture")

    def test_flag_on_window_statistic_measurement_type(self, monkeypatch):
        monkeypatch.setenv(FLAG, "true")
        result = build_measurements(_stat_entity(), STAT_PROFILE)
        mn = _by_id_channel(result, "airTemperatureMin")
        # channel in name, catalogue name in controlledProperty, statistic in measurementType
        assert mn["name"]["value"] == "airTemperatureMin"
        assert mn["controlledProperty"]["value"] == "airTemperature"
        assert mn["measurementType"]["value"] == "min"
        assert _by_id_channel(result, "airTemperature")["measurementType"]["value"] == "mean"
        assert _by_id_channel(result, "rain")["measurementType"]["value"] == "sum"
        assert _by_id_channel(result, "soilTemperature")["measurementType"]["value"] == "last"
        # no catalogue name: fallback is the BASE attribute, never the expanded channel
        mx = _by_id_channel(result, "windGustMax")
        assert mx["name"]["value"] == "windGustMax"
        assert mx["controlledProperty"]["value"] == "windGust"
        assert mx["measurementType"]["value"] == "max"

    @pytest.mark.parametrize("value", ["", "1", "yes", "TRUE ", "on", "false", "tru"])
    def test_flag_unparseable_is_off(self, monkeypatch, value):
        monkeypatch.setenv(FLAG, value)
        result = build_measurements(_stat_entity(), STAT_PROFILE)
        for m in result:
            assert "name" not in m
            assert m["controlledProperty"]["value"] == m["id"].rsplit(":", 1)[1]

    def test_flag_value_is_case_insensitive_true(self, monkeypatch):
        monkeypatch.setenv(FLAG, "True")
        assert "name" in _by_id_channel(build_measurements(_stat_entity(), STAT_PROFILE),
                                        "soilMoisture")

    @pytest.mark.parametrize("flag", [None, "true", "false"])
    def test_never_emits_observed_property(self, monkeypatch, flag):
        if flag is None:
            monkeypatch.delenv(FLAG, raising=False)
        else:
            monkeypatch.setenv(FLAG, flag)
        for m in build_measurements(_stat_entity(), STAT_PROFILE):
            assert "observedProperty" not in m

    def test_measurement_type_always_present_when_on(self, monkeypatch):
        monkeypatch.setenv(FLAG, "true")
        vocab = {"instant", "mean", "min", "max", "stddev", "last", "sum"}
        result = build_measurements(_stat_entity(), STAT_PROFILE)
        assert len(result) == 6
        for m in result:
            assert m["measurementType"]["value"] in vocab
        # profile without a catalogue name or statistics still gets `instant`
        for m in build_measurements(_entity(), PROFILE):
            assert m["measurementType"]["value"] == "instant"
