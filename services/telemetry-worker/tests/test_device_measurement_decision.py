"""The shared shape/property decision (ingest and recompute call the same function)."""

import pytest

from telemetry_worker.device_measurement import (
    SKIP_NAME_UNUSABLE,
    SKIP_NO_DEVICE_TYPE,
    SKIP_NO_OBSERVED_PROPERTY,
    SKIP_PROPERTY_MISMATCH,
    SKIP_STATISTIC,
    decide_observation_property,
    device_measurement_shape,
)


def _dm(**attrs):
    return {k: {"type": "Property", "value": v} for k, v in attrs.items()}


def _decide(entity, profile="P", mdef=None):
    return decide_observation_property(device_measurement_shape(entity), profile, mdef)


DEF = {"observedProperty": "airTemperature", "unit": "°C"}


@pytest.mark.parametrize("mtype", ["instant", "mean", "last", "sum"])
def test_sdm_context_types_resolve_controlled_property(mtype):
    d = _decide(_dm(name="airTemperature", controlledProperty="airTemperature", measurementType=mtype), mdef=DEF)
    assert d.observed_property == "airTemperature" and d.skip is None


@pytest.mark.parametrize("mtype", ["min", "max", "stddev"])
def test_sdm_spread_types_are_gated(mtype):
    d = _decide(_dm(name="airTemperatureMin", controlledProperty="airTemperature", measurementType=mtype), mdef=DEF)
    assert d.skip == SKIP_STATISTIC and d.observed_property is None


def test_sdm_profile_property_mismatch():
    d = _decide(_dm(name="x", controlledProperty="soilMoisture", measurementType="instant"), mdef=DEF)
    assert d.skip == SKIP_PROPERTY_MISMATCH and d.def_property == "airTemperature"


def test_sdm_without_profile_property_is_not_opted_in():
    d = _decide(_dm(name="x", controlledProperty="airTemperature", measurementType="instant"), mdef={"unit": "°C"})
    assert d.skip == SKIP_NO_OBSERVED_PROPERTY


def test_legacy_uses_profile_property_and_ignores_measurement_type():
    d = _decide(_dm(controlledProperty="temperature", measurementType="sum"), mdef=DEF)
    assert d.observed_property == "airTemperature"


@pytest.mark.parametrize("statistic", ["min", "max", "stddev", "unknown"])
def test_legacy_profile_statistic_gate(statistic):
    d = _decide(_dm(controlledProperty="temperature"), mdef={**DEF, "statistic": statistic})
    assert d.skip == SKIP_STATISTIC


def test_legacy_without_device_type():
    assert _decide(_dm(controlledProperty="temperature"), profile=None).skip == SKIP_NO_DEVICE_TYPE


def test_neither_name_nor_device_type_has_no_property():
    assert _decide({}, profile=None).observed_property is None


@pytest.mark.parametrize("bad", ["", None, 3])
def test_unusable_name_is_never_decided(bad):
    d = _decide(_dm(name=bad, controlledProperty="airTemperature", measurementType="instant"), mdef=DEF)
    assert d.skip == SKIP_NAME_UNUSABLE
