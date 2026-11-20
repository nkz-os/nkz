"""Profile measurements may carry `statistics`; the mapping may carry `windowSeconds`."""
from unittest.mock import patch

import pytest

import dataloggers
import observation_catalogue
from blueprints.sensors import _validated_profile

TENANT = "tenant-a"
PROPS = {
    "windSpeed": {"name": "windSpeed", "temporal_semantics": "instant"},
    "windSpeedMax": {"name": "windSpeedMax", "temporal_semantics": "instant"},
    "airTemperature": {"name": "airTemperature", "temporal_semantics": "instant"},
    "precipitation": {"name": "precipitation", "temporal_semantics": "cumulative"},
}


def _validate(window=None, **extra):
    m = {"type": "t", "sdmAttribute": "airTemperature", "unit": "°C", **extra}
    mapping = {"measurements": [m]}
    if window is not None:
        mapping["windowSeconds"] = window
    with patch.object(observation_catalogue, "get_property",
                      lambda tenant, name: PROPS.get(name)):
        return _validated_profile({"name": "P", "mapping": mapping}, TENANT)


def _rejected(**kw):
    with pytest.raises(dataloggers.ApiError) as e:
        _validate(**kw)
    assert e.value.status == 400


def test_statistics_accepted_in_given_order():
    mapping = _validate(statistics=["mean", "min", "max", "stddev"])[2]
    assert mapping["measurements"][0]["statistics"] == ["mean", "min", "max", "stddev"]


@pytest.mark.parametrize("bad", [[], ["median"], ["mean", "mean"], "mean", {"mean": 1},
                                 [1], ["mean", "last"], ["sum", "mean"]])
def test_invalid_statistics_rejected(bad):
    _rejected(statistics=bad)


def test_counter_max_only_allows_last():
    mapping = _validate(observedProperty="precipitation", counterMax=100,
                        statistics=["last"])[2]
    assert mapping["measurements"][0]["statistics"] == ["last"]
    _rejected(observedProperty="precipitation", counterMax=100, statistics=["mean"])


def test_expression_only_allows_mean_last_sum():
    _rejected(expression="t * 2", statistics=["mean", "max"])
    mapping = _validate(expression="t * 2", statistics=["mean"])[2]
    assert mapping["measurements"][0]["statistics"] == ["mean"]


@pytest.mark.parametrize("ok", [60, 600, 86400])
def test_window_seconds_accepted(ok):
    assert _validate(window=ok)[2]["windowSeconds"] == ok


@pytest.mark.parametrize("bad", [59, 7, 90000, "60", True, 60.0, 0, -60])
def test_window_seconds_rejected(bad):
    _rejected(window=bad)


def test_without_new_fields_mapping_is_unchanged():
    mapping = _validate()[2]
    assert mapping == {"measurements": [
        {"type": "t", "sdmAttribute": "airTemperature", "unit": "°C"}]}


def _validate_many(measurements):
    with patch.object(observation_catalogue, "get_property",
                      lambda tenant, name: PROPS.get(name)):
        return _validated_profile(
            {"name": "P", "mapping": {"measurements": measurements}}, TENANT)


def _rejected_many(measurements):
    with pytest.raises(dataloggers.ApiError) as e:
        _validate_many(measurements)
    assert e.value.status == 400


def test_derived_attribute_colliding_with_another_measurement_rejected():
    _rejected_many([
        {"type": "w", "sdmAttribute": "windSpeed", "unit": "m/s", "statistics": ["mean", "max"]},
        {"type": "wmax", "sdmAttribute": "windSpeedMax", "unit": "m/s"}])


def test_derived_key_colliding_with_another_measurement_rejected():
    _rejected_many([
        {"type": "t", "sdmAttribute": "airTemperature", "unit": "°C", "statistics": ["mean", "min"]},
        {"type": "tMin", "sdmAttribute": "windSpeed", "unit": "m/s"}])


def test_several_measurements_with_statistics_accepted():
    mapping = _validate_many([
        {"type": "t", "sdmAttribute": "airTemperature", "unit": "°C", "statistics": ["mean", "min", "max"]},
        {"type": "w", "sdmAttribute": "windSpeed", "unit": "m/s", "statistics": ["mean", "max"]}])[2]
    assert len(mapping["measurements"]) == 2
