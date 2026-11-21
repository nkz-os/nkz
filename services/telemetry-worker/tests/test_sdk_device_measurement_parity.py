"""Parity: the SDK reader helper and this worker read a DeviceMeasurement the same way.

Modules key readings with `nkz_platform_sdk.device_measurement.read_device_measurement`;
the worker stores them with `telemetry_worker.device_measurement.device_measurement_shape`
(`telemetry_measurements.attribute_name` = the channel). If the two drift, a module's series
stop matching the stored ones. The SDK suite has no CI job, so the case table lives here.

The SDK module is loaded by path: importing the `nkz_platform_sdk` package would pull its
HTTP dependencies, which this job does not install.
"""
import importlib.util
import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from common.window_statistics import INSTANT, STATISTICS  # noqa: E402
from telemetry_worker.device_measurement import (  # noqa: E402
    NAME_KEYS,
    SDM_MEASUREMENT_TYPES,
    device_measurement_shape,
)
from telemetry_worker.notification_handler import REF_DEVICE_KEYS, _ref_device_urn  # noqa: E402


def _load_sdk_module():
    name = "_sdk_device_measurement_under_test"
    path = os.path.join(_SERVICES_DIR, "nkz-platform-sdk", "nkz_platform_sdk", "device_measurement.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve postponed annotations through sys.modules
    spec.loader.exec_module(module)
    return module


sdk = _load_sdk_module()
DEVICE = "urn:ngsi-ld:Device:t1:dev-1"


def _prop(value, **extra):
    return {"type": "Property", "value": value, **extra}


def _dm(**attrs):
    entity = {
        "id": "urn:ngsi-ld:DeviceMeasurement:t1:dev-1:airTemperatureMin",
        "type": "DeviceMeasurement",
        "refDevice": {"type": "Relationship", "object": DEVICE},
        "numValue": _prop(12.5, unitCode="CEL"),
        "dateObserved": _prop("2026-11-21T08:54:00.000Z"),
    }
    entity.update(attrs)
    return entity


CASES = {
    "legacy": _dm(controlledProperty=_prop("airTemperatureMin"),
                  measurementType=_prop("airTemperature")),
    "legacy_vocabulary_word": _dm(controlledProperty=_prop("airTemperature"),
                                  measurementType=_prop("mean")),
    "sdm_min": _dm(controlledProperty=_prop("airTemperature"), name=_prop("airTemperatureMin"),
                   measurementType=_prop("min")),
    "sdm_instant": _dm(controlledProperty=_prop("airTemperature"), name=_prop("airTemperature"),
                       measurementType=_prop("instant")),
    "rollback_stale_name": _dm(controlledProperty=_prop("airTemperatureMin"),
                               name=_prop("airTemperatureMin"),
                               measurementType=_prop("airTemperature")),
    "corrupt_measurement_type": _dm(controlledProperty=_prop("airTemperature"),
                                    name=_prop("airTemperatureMin"),
                                    measurementType=_prop("bogus")),
    "missing_measurement_type": _dm(controlledProperty=_prop("airTemperature"),
                                    name=_prop("airTemperatureMin")),
    "name_schema_iri": _dm(controlledProperty=_prop("airTemperature"),
                           **{"https://schema.org/name": _prop("airTemperatureMin")},
                           measurementType=_prop("min")),
    "name_sdm_iri": _dm(controlledProperty=_prop("airTemperature"),
                        **{"https://smartdatamodels.org/name": _prop("airTemperatureMin")},
                        measurementType=_prop("max")),
    "name_empty": _dm(controlledProperty=_prop("airTemperature"), name=_prop(""),
                      measurementType=_prop("min")),
    "name_not_string": _dm(controlledProperty=_prop("airTemperature"), name=_prop(7),
                           measurementType=_prop("min")),
    "no_controlled_property": _dm(measurementType=_prop("airTemperature")),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_shape_and_channel_match_the_worker(case):
    entity = CASES[case]
    worker = device_measurement_shape(entity)
    reading = sdk.read_device_measurement(entity)
    assert reading.channel == worker.channel
    assert reading.shape == ("sdm" if worker.sdm else "legacy")
    if worker.sdm:
        assert reading.observed_property == worker.controlled_property
        assert reading.statistic == worker.measurement_type
    else:
        assert reading.observed_property is None and reading.statistic is None


@pytest.mark.parametrize("key", REF_DEVICE_KEYS)
def test_device_id_matches_the_worker(key):
    entity = CASES["sdm_min"].copy()
    entity[key] = entity.pop("refDevice")
    assert sdk.read_device_measurement(entity).device_id == _ref_device_urn(entity) == DEVICE


def test_vocabulary_matches_window_statistics():
    assert sdk.MEASUREMENT_TYPES == SDM_MEASUREMENT_TYPES == frozenset({INSTANT, *STATISTICS})


def test_name_keys_match():
    assert sdk._NAME_KEYS == NAME_KEYS


def test_ref_device_keys_match():
    assert sdk._REF_DEVICE_KEYS == REF_DEVICE_KEYS
