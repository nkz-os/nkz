"""Contract for `common.device_model`: the official DeviceModel built from a sensor profile.

The entity-manager suite may replace `common` with a MagicMock, so the real modules are
pinned in `sys.modules` under their dotted names first (see `tests/real_common.py`).
"""

import json
import logging
import os
import sys
from unittest.mock import MagicMock

import pytest

_test_dir = os.path.dirname(os.path.abspath(__file__))
_em_dir = os.path.normpath(os.path.join(_test_dir, ".."))
_services_dir = os.path.normpath(os.path.join(_em_dir, ".."))
for _p in (_em_dir, _services_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

sys.modules.setdefault("common", MagicMock())

from tests.real_common import seed_real_common  # noqa: E402

seed_real_common()

from common.device_model import (  # noqa: E402
    OPTIONAL_PROFILE_ATTRS,
    build_device_model,
    device_model_id,
    device_profile_attrs,
    device_profile_code,
    profile_code_from_model,
    profile_codes,
)

TENANT = "acmefarm"
MODEL_IRI = "https://smartdatamodels.org/dataModel.Device/refDeviceModel"
PROFILE_CODE_IRI = "https://uri.etsi.org/ngsi-ld/default-context/profileCode"


def _profile(code="soil_probe", **extra):
    base = {
        "code": code,
        "name": "Soil probe",
        "mapping": {"version": 1, "measurements": [
            {"type": "soilMoisture", "sdmAttribute": "soilMoisture", "unit": "%"},
            {"type": "soilTemperature", "sdmAttribute": "soilTemperature", "unit": "CEL"},
        ]},
        "manufacturer_name": None,
        "brand_name": None,
        "model_name": None,
    }
    base.update(extra)
    return base


def _measuring(*names):
    return {"mapping": {"measurements": [{"sdmAttribute": n} for n in names]}}


def _value(entity, attr):
    return entity[attr]["value"]


class TestIds:
    def test_id_scheme(self):
        assert device_model_id(TENANT, "water_meter") == "urn:ngsi-ld:DeviceModel:acmefarm:water_meter"

    @pytest.mark.parametrize("tenant,code", [
        ("", "a"), ("acmefarm", ""), ("acme:farm", "a"), ("acmefarm", "a:b"),
        ("acmefarm", "bad code"), ("acmefarm", "x" * 65), ("acmefarm", "a/b"), ("acmefarm", None),
        (None, "a"), ("acme farm", "a"),
    ])
    def test_unparsable_parts_are_refused(self, tenant, code):
        with pytest.raises(ValueError):
            device_model_id(tenant, code)

    def test_every_id_written_parses_back(self):
        for code in ("a", "A_b.c-9", "x" * 64):
            assert profile_code_from_model(TENANT, device_model_id(TENANT, code)) == code


class TestProfileCodeFromModel:
    def test_valid(self):
        assert profile_code_from_model(TENANT, "urn:ngsi-ld:DeviceModel:acmefarm:water_meter") == "water_meter"

    @pytest.mark.parametrize("urn", [
        "urn:ngsi-ld:DeviceModel:otherfarm:water_meter",
        "urn:ngsi-ld:DeviceModel:acmefarm",
        "urn:ngsi-ld:DeviceModel:acmefarm:a:b",
        "urn:ngsi-ld:DeviceModel:acmefarm:bad code",
        "urn:ngsi-ld:DeviceModel:acmefarm:",
        "urn:ngsi-ld:DeviceModel:acmefarm:" + "x" * 65,
        "urn:ngsi-ld:Device:acmefarm:water_meter",
        "",
        None, 5, ["urn:ngsi-ld:DeviceModel:acmefarm:a"], {"object": "x"},
    ])
    def test_rejected(self, urn):
        assert profile_code_from_model(TENANT, urn) is None

    def test_empty_tenant_rejected(self):
        assert profile_code_from_model("", "urn:ngsi-ld:DeviceModel::a") is None


class TestBuildDeviceModel:
    def test_generic_profile_defaults(self):
        entity = build_device_model(TENANT, _profile())
        assert entity["id"] == "urn:ngsi-ld:DeviceModel:acmefarm:soil_probe"
        assert entity["type"] == "DeviceModel"
        assert "@context" not in entity
        assert _value(entity, "manufacturerName") == "generic"
        assert _value(entity, "brandName") == "generic"
        assert _value(entity, "modelName") == "soil_probe"
        assert _value(entity, "name") == "Soil probe"
        assert entity["manufacturerName"]["type"] == "Property"

    def test_declared_columns_are_kept_verbatim(self):
        entity = build_device_model(TENANT, _profile(
            manufacturer_name="Acme Instruments", brand_name="AcmeSense", model_name="AS-100"))
        assert _value(entity, "manufacturerName") == "Acme Instruments"
        assert _value(entity, "brandName") == "AcmeSense"
        assert _value(entity, "modelName") == "AS-100"

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n", None, 7])
    def test_blank_columns_count_as_not_declared(self, blank):
        entity = build_device_model(TENANT, _profile(
            manufacturer_name=blank, brand_name=blank, model_name=blank))
        assert _value(entity, "manufacturerName") == "generic"
        assert _value(entity, "brandName") == "generic"
        assert _value(entity, "modelName") == "soil_probe"

    def test_columns_may_be_missing_from_the_row(self):
        profile = _profile()
        for key in ("manufacturer_name", "brand_name", "model_name"):
            del profile[key]
        assert _value(build_device_model(TENANT, profile), "brandName") == "generic"

    def test_category_and_controlled_property_are_sorted_arrays(self):
        entity = build_device_model(TENANT, _profile())
        assert _value(entity, "category") == ["sensor"]
        assert _value(entity, "controlledProperty") == ["soilMoisture", "temperature"]

    def test_controlled_property_omitted_when_nothing_maps(self):
        entity = build_device_model(TENANT, _profile(
            code="dendrometer",
            mapping={"measurements": [{"sdmAttribute": "stemDiameter"}]}))
        assert "controlledProperty" not in entity

    def test_blank_name_is_omitted(self):
        assert "name" not in build_device_model(TENANT, _profile(name="  "))

    @pytest.mark.parametrize("code,names,expected", [
        ("water_meter", ["waterConsumption"], ["meter"]),
        ("energy_production", ["instantaneousPower", "cumulativeActivePowerImport"], ["meter"]),
        ("probe", ["soilMoisture"], ["sensor"]),
        ("empty", [], ["sensor"]),
    ])
    def test_meter_rule(self, code, names, expected):
        profile = _profile(code=code, **_measuring(*names))
        assert _value(build_device_model(TENANT, profile), "category") == expected
        assert _value(device_profile_attrs(TENANT, profile), "deviceCategory") == expected

    def test_meter_rule_uses_observed_property_first_then_canonical_then_base(self):
        by_observed = {"mapping": {"measurements": [{"observedProperty": "waterConsumption"}]}}
        by_base = {"mapping": {"measurements": [{"sdmAttribute": "waterConsumptionMax"}]}}
        by_observed_over_sdm = {"mapping": {"measurements": [
            {"observedProperty": "soilMoisture", "sdmAttribute": "waterConsumption"}]}}
        assert _value(build_device_model(TENANT, _profile(**by_observed)), "category") == ["meter"]
        assert _value(build_device_model(TENANT, _profile(**by_base)), "category") == ["meter"]
        assert _value(build_device_model(TENANT, _profile(**by_observed_over_sdm)), "category") == ["sensor"]

    @pytest.mark.parametrize("mapping", [
        None, "text", 5, [], {}, {"measurements": None}, {"measurements": "x"},
        {"measurements": [None, "a", 3, [], {"observedProperty": 4}]},
    ])
    def test_malformed_mapping_does_not_raise(self, mapping):
        entity = build_device_model(TENANT, _profile(mapping=mapping))
        assert _value(entity, "category") == ["sensor"]
        assert "controlledProperty" not in entity

    def test_unusable_code_is_refused(self):
        with pytest.raises(ValueError):
            build_device_model(TENANT, _profile(code="has:colon"))

    def test_entity_is_json_serialisable(self):
        json.dumps(build_device_model(TENANT, _profile()))


class TestDeviceProfileAttrs:
    def test_attrs(self):
        attrs = device_profile_attrs(TENANT, _profile())
        assert attrs["refDeviceModel"] == {
            "type": "Relationship", "object": "urn:ngsi-ld:DeviceModel:acmefarm:soil_probe"}
        assert attrs["deviceCategory"] == {"type": "Property", "value": ["sensor"]}
        assert attrs["controlledProperty"] == {
            "type": "Property", "value": ["soilMoisture", "temperature"]}
        assert set(attrs) == {"refDeviceModel", "deviceCategory", "controlledProperty"}

    def test_controlled_property_omitted_when_empty(self):
        attrs = device_profile_attrs(TENANT, _profile(mapping={}))
        assert set(attrs) == {"refDeviceModel", "deviceCategory"}

    def test_optional_attrs_are_the_removable_ones(self):
        assert OPTIONAL_PROFILE_ATTRS == ("controlledProperty",)


MODEL_URN = "urn:ngsi-ld:DeviceModel:acmefarm:soil_probe"


class TestDeviceProfileCode:
    @pytest.mark.parametrize("model_key", ["refDeviceModel", MODEL_IRI])
    @pytest.mark.parametrize("code_key", ["profileCode", PROFILE_CODE_IRI])
    def test_profile_code_wins_when_the_two_codes_differ(self, model_key, code_key):
        """Only a writer that knows nothing but `profileCode` makes them differ: it is the fresher."""
        entity = {
            model_key: {"type": "Relationship", "object": MODEL_URN},
            code_key: {"type": "Property", "value": "fresher_code"},
        }
        assert device_profile_code(TENANT, entity) == ("fresher_code", "legacy")

    def test_two_equal_codes_come_from_the_model(self):
        entity = {
            "refDeviceModel": {"type": "Relationship", "object": MODEL_URN},
            "profileCode": {"type": "Property", "value": "soil_probe"},
        }
        assert device_profile_code(TENANT, entity) == ("soil_probe", "model")

    @pytest.mark.parametrize("unusable", ["", None, 5, ["other"]])
    def test_the_model_is_used_when_profile_code_is_unusable(self, unusable):
        entity = {
            "refDeviceModel": {"type": "Relationship", "object": MODEL_URN},
            "profileCode": {"type": "Property", "value": unusable},
        }
        assert device_profile_code(TENANT, entity) == ("soil_probe", "model")

    @pytest.mark.parametrize("entity", [
        {"refDeviceModel": MODEL_URN},
        {MODEL_IRI: {"type": "Relationship", "object": MODEL_URN}},
        {MODEL_IRI: MODEL_URN},
        {"refDeviceModel": {"type": "Relationship", "object": [MODEL_URN]}},
        {"refDeviceModel": [MODEL_URN]},
        {MODEL_IRI: {"type": "Relationship", "object": [MODEL_URN]}},
        {MODEL_IRI: [MODEL_URN]},
    ])
    def test_model_forms(self, entity):
        assert device_profile_code(TENANT, entity) == ("soil_probe", "model")

    @pytest.mark.parametrize("entity", [
        {"profileCode": {"type": "Property", "value": "old_code"}},
        {"profileCode": "old_code"},
        {PROFILE_CODE_IRI: {"type": "Property", "value": "old_code"}},
        {PROFILE_CODE_IRI: "old_code"},
    ])
    def test_legacy_forms(self, entity):
        assert device_profile_code(TENANT, entity) == ("old_code", "legacy")

    @pytest.mark.parametrize("entity", [
        {}, {"profileCode": ""}, {"profileCode": {"type": "Property", "value": ""}},
        {"profileCode": {"type": "Property", "value": 5}}, {"profileCode": None},
        {"profileCode": {"type": "Property", "value": None}}, None, "text", [],
    ])
    def test_none(self, entity):
        assert device_profile_code(TENANT, entity) == (None, "none")

    @pytest.mark.parametrize("bad,reason_part", [
        ("urn:ngsi-ld:DeviceModel:otherfarm:soil_probe", "tenant"),
        ("urn:ngsi-ld:DeviceModel:acmefarm:a:b", "malformed"),
        ("not-a-urn", "malformed"),
        (5, "malformed"),
    ])
    def test_bad_model_falls_through_to_profile_code_and_gives_the_reason(self, caplog, bad, reason_part):
        entity = {
            "id": "urn:ngsi-ld:Device:acmefarm:s1",
            "refDeviceModel": {"type": "Relationship", "object": bad},
            "profileCode": {"type": "Property", "value": "old_code"},
        }
        with caplog.at_level(logging.DEBUG):
            assert device_profile_code(TENANT, entity) == ("old_code", "legacy")
            assert reason_part in profile_codes(TENANT, entity).model_error
        assert not caplog.records  # the caller logs it

    def test_bad_model_without_profile_code_is_none(self):
        entity = {"refDeviceModel": {"type": "Relationship", "object": "urn:ngsi-ld:DeviceModel:other:x"}}
        assert device_profile_code(TENANT, entity) == (None, "none")

    def test_foreign_model_is_never_used_as_a_code(self):
        entity = {"refDeviceModel": {"type": "Relationship", "object": "urn:ngsi-ld:DeviceModel:other:x"}}
        assert device_profile_code(TENANT, entity)[0] is None


# ── Conformance example ─────────────────────────────────────────────────────
# `config/sdm-examples/DeviceModel.json` is the builder output; refresh it after a deliberate
# change with SDM_EXAMPLES_WRITE=1.

_EXAMPLES_DIR = os.path.normpath(os.path.join(_services_dir, "..", "config", "sdm-examples"))


def _assert_example(file_name, entity):
    path = os.path.join(_EXAMPLES_DIR, file_name)
    if os.environ.get("SDM_EXAMPLES_WRITE") == "1":
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(entity, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    with open(path, encoding="utf-8") as handle:
        assert json.load(handle) == entity, f"{file_name} no longer matches what the builder emits"


def test_device_model_example():
    profile = _profile(manufacturer_name="Acme Instruments", brand_name="AcmeSense", model_name="AS-100")
    _assert_example("DeviceModel.json", build_device_model(TENANT, profile))


# ---- the codes a Device carries ----------------------------------------------

@pytest.mark.parametrize("entity,expected", [
    ({}, (None, None, None, False)),
    (None, (None, None, None, False)),
    ({"profileCode": {"type": "Property", "value": "p"}}, (None, None, "p", True)),
    ({"profileCode": {"type": "Property", "value": ""}}, (None, None, None, True)),
    ({"profileCode": {"type": "Property", "value": 7}}, (None, None, None, True)),
    ({"refDeviceModel": {"type": "Relationship", "object": "urn:ngsi-ld:DeviceModel:acmefarm:m"},
      "profileCode": {"type": "Property", "value": "p"}}, ("m", None, "p", True)),
    ({"https://smartdatamodels.org/dataModel.Device/refDeviceModel":
      {"type": "Relationship", "object": "urn:ngsi-ld:DeviceModel:acmefarm:m"}}, ("m", None, None, True)),
    ({"refDeviceModel": {"type": "Relationship", "object": "urn:ngsi-ld:DeviceModel:otherfarm:m"},
      "profileCode": {"type": "Property", "value": "p"}}, (None, "foreign tenant", "p", True)),
    ({"refDeviceModel": {"type": "Property", "value": 1}}, (None, "malformed: not a string", None, True)),
    # more than one target is nobody's model, not even when they are equal
    *(({"refDeviceModel": ref}, (None, "malformed: not a string", None, True)) for ref in (
        {"type": "Relationship", "object": [MODEL_URN, MODEL_URN]}, [MODEL_URN, MODEL_URN],
        {"type": "Relationship", "object": []}, [], {"type": "Relationship", "object": [[MODEL_URN]]},
        [5], [None])),
    ({"refDeviceModel": ["urn:ngsi-ld:DeviceModel:otherfarm:m"]}, (None, "foreign tenant", None, True)),
])
def test_profile_codes_reports_both_codes(entity, expected):
    assert tuple(profile_codes("acmefarm", entity)) == expected
