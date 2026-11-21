"""read_device_measurement: one reading out of a DeviceMeasurement in either stored shape.

Legacy shape (flag DEVICE_MEASUREMENT_SDM_SHAPE_ENABLED off): `controlledProperty` is the
channel, there is no `name`, `measurementType` is the sensor-profile type.
SDM shape (flag on): `controlledProperty` is the catalogue property, `name` the channel,
`measurementType` the statistic from a closed vocabulary.
"""

import pytest

from nkz_platform_sdk.device_measurement import (
    MEASUREMENT_TYPES,
    DeviceMeasurementReading,
    read_device_measurement,
)

DEVICE = "urn:ngsi-ld:Device:t1:dev-1"


def _prop(value, **extra):
    return {"type": "Property", "value": value, **extra}


def _legacy(channel="airTemperatureMin", value=12.5, mt="airTemperature"):
    return {
        "id": f"urn:ngsi-ld:DeviceMeasurement:t1:dev-1:{channel}",
        "type": "DeviceMeasurement",
        "refDevice": {"type": "Relationship", "object": DEVICE},
        "controlledProperty": _prop(channel),
        "measurementType": _prop(mt),
        "numValue": _prop(value, unitCode="CEL"),
        "dateObserved": _prop("2026-11-21T08:54:00.000Z"),
    }


def _sdm(channel="airTemperatureMin", prop="airTemperature", mt="min", value=12.5):
    return {
        "id": f"urn:ngsi-ld:DeviceMeasurement:t1:dev-1:{channel}",
        "type": "DeviceMeasurement",
        "refDevice": {"type": "Relationship", "object": DEVICE},
        "controlledProperty": _prop(prop),
        "name": _prop(channel),
        "measurementType": _prop(mt),
        "numValue": _prop(value, unitCode="CEL"),
        "dateObserved": _prop("2026-11-21T08:54:00.000Z"),
    }


def test_legacy_shape_channel_is_controlled_property():
    r = read_device_measurement(_legacy())
    assert r == DeviceMeasurementReading(
        shape="legacy",
        device_id=DEVICE,
        channel="airTemperatureMin",
        observed_property=None,
        statistic=None,
        value=12.5,
        unit_code="CEL",
        date_observed="2026-11-21T08:54:00.000Z",
    )


def test_sdm_shape_channel_is_name_and_property_is_controlled_property():
    r = read_device_measurement(_sdm())
    assert r.shape == "sdm"
    assert r.channel == "airTemperatureMin"
    assert r.observed_property == "airTemperature"
    assert r.statistic == "min"
    assert (r.device_id, r.value, r.unit_code) == (DEVICE, 12.5, "CEL")


def test_min_and_base_channels_stay_apart_in_sdm_shape():
    base = read_device_measurement(_sdm(channel="airTemperature", mt="instant"))
    low = read_device_measurement(_sdm(channel="airTemperatureMin", mt="min"))
    assert base.observed_property == low.observed_property == "airTemperature"
    assert base.channel != low.channel


def test_legacy_measurement_type_that_is_a_vocabulary_word_stays_legacy():
    # Without `name` the shape is legacy even if the profile type collides with the vocabulary.
    r = read_device_measurement(_legacy(channel="airTemperature", mt="mean"))
    assert r.shape == "legacy"
    assert r.statistic is None and r.observed_property is None


def test_stale_name_after_flag_rollback_keys_by_name_but_is_legacy():
    # Flag rolled back: the upsert keeps the old `name` (== channel) and measurementType goes
    # back to the profile type. The channel must not change, nor be read as SDM.
    e = _legacy(channel="airTemperatureMin", mt="airTemperature")
    e["name"] = _prop("airTemperatureMin")
    r = read_device_measurement(e)
    assert r.shape == "legacy"
    assert r.channel == "airTemperatureMin"
    assert r.observed_property is None and r.statistic is None


def test_name_with_invalid_measurement_type_keeps_name_as_channel():
    e = _sdm(channel="airTemperatureMin", mt="bogus")
    r = read_device_measurement(e)
    assert r.shape == "legacy"
    assert r.channel == "airTemperatureMin"  # never merged into controlledProperty


@pytest.mark.parametrize("key", ["name", "https://schema.org/name", "https://smartdatamodels.org/name"])
def test_name_accepted_under_every_iri(key):
    e = _sdm()
    e[key] = e.pop("name")
    assert read_device_measurement(e).channel == "airTemperatureMin"


@pytest.mark.parametrize(
    "key",
    [
        "refDevice",
        "hasDevice",
        "https://smartdatamodels.org/dataModel.Device/hasDevice",
        "https://smartdatamodels.org/dataModel.Device/refDevice",
    ],
)
def test_ref_device_accepted_under_every_name(key):
    e = _legacy()
    e[key] = e.pop("refDevice")
    assert read_device_measurement(e).device_id == DEVICE


def test_expanded_iris_for_every_attribute():
    sdm = "https://smartdatamodels.org/dataModel.Device/"
    e = {
        "id": "urn:ngsi-ld:DeviceMeasurement:t1:dev-1:airTemperatureMin",
        "type": sdm + "DeviceMeasurement",
        sdm + "hasDevice": {"type": "Relationship", "object": DEVICE},
        sdm + "controlledProperty": _prop("airTemperature"),
        "https://schema.org/name": _prop("airTemperatureMin"),
        sdm + "measurementType": _prop("min"),
        sdm + "numValue": _prop(12.5, unitCode="CEL"),
        "https://smartdatamodels.org/dateObserved": _prop("2026-11-21T08:54:00.000Z"),
    }
    r = read_device_measurement(e)
    assert (r.shape, r.channel, r.observed_property, r.statistic) == (
        "sdm", "airTemperatureMin", "airTemperature", "min")
    assert (r.device_id, r.value, r.unit_code, r.date_observed) == (
        DEVICE, 12.5, "CEL", "2026-11-21T08:54:00.000Z")


def test_key_values_representation():
    e = {
        "id": "urn:ngsi-ld:DeviceMeasurement:t1:dev-1:airTemperatureMin",
        "type": "DeviceMeasurement",
        "refDevice": DEVICE,
        "controlledProperty": "airTemperature",
        "name": "airTemperatureMin",
        "measurementType": "min",
        "numValue": 12.5,
        "dateObserved": "2026-11-21T08:54:00.000Z",
    }
    r = read_device_measurement(e)
    assert (r.shape, r.channel, r.observed_property, r.statistic, r.device_id, r.value) == (
        "sdm", "airTemperatureMin", "airTemperature", "min", DEVICE, 12.5)
    assert r.unit_code is None  # keyValues drops the unitCode


def test_ref_device_key_values_object_form():
    e = _legacy()
    e["refDevice"] = {"object": DEVICE}
    assert read_device_measurement(e).device_id == DEVICE


def test_text_value_when_no_num_value():
    e = _legacy(channel="status")
    del e["numValue"]
    e["textValue"] = _prop("ok")
    r = read_device_measurement(e)
    assert (r.value, r.unit_code) == ("ok", None)


def test_null_num_value_falls_back_to_text_value():
    e = _legacy()
    e["numValue"] = _prop(None)
    e["textValue"] = _prop("n/a")
    assert read_device_measurement(e).value == "n/a"


@pytest.mark.parametrize("bad", [None, "", 7, {"type": "Property"}])
def test_unusable_name_gives_no_channel(bad):
    e = _sdm()
    e["name"] = _prop(bad) if not isinstance(bad, dict) else bad
    assert read_device_measurement(e).channel is None


def test_missing_everything_gives_nones_not_errors():
    r = read_device_measurement({"id": "x", "type": "DeviceMeasurement"})
    assert r == DeviceMeasurementReading(
        shape="legacy", device_id=None, channel=None, observed_property=None,
        statistic=None, value=None, unit_code=None, date_observed=None)


def test_non_mapping_raises_type_error():
    with pytest.raises(TypeError):
        read_device_measurement(["not", "an", "entity"])  # type: ignore[arg-type]


def test_measurement_type_vocabulary_is_closed():
    assert MEASUREMENT_TYPES == frozenset(
        {"instant", "mean", "min", "max", "stddev", "last", "sum"})


def test_exported_from_package():
    import nkz_platform_sdk

    assert nkz_platform_sdk.read_device_measurement is read_device_measurement
    assert "read_device_measurement" in nkz_platform_sdk.__all__
    assert "DeviceMeasurementReading" in nkz_platform_sdk.__all__
