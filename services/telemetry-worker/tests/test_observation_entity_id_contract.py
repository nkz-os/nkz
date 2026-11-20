"""Contract: the reader's entity-id segment pruning keeps every stored observation row.

The worker (ingest and recompute) writes observation context only when
common.observation_context.entity_id_conforms holds; the reader prunes with LIKE patterns built
from the same measurement_entity_suffix. Both must agree with the real id producer.

timeseries-reader `sources.entity_like_patterns` filters telemetry_measurements with
`entity_id LIKE '%:{device_id}:{attribute}'` (contract 8.10, segment pruning). That holds only
if the DeviceMeasurement id minted by entity-manager `build_measurements` ends with the
`device_id` and the attribute name this worker stores for it (`_extract_device_measurement`).
"""
import importlib.util
import os
import re
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_READER_DIR = os.path.join(_SERVICES_DIR, "timeseries-reader")
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from telemetry_worker import notification_handler as nh  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _like(pattern: str, value: str) -> bool:
    """SQL LIKE with the default backslash escape."""
    rx = ""
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            rx += re.escape(pattern[i + 1])
            i += 2
            continue
        rx += ".*" if c == "%" else "." if c == "_" else re.escape(c)
        i += 1
    return re.fullmatch(rx, value, re.S) is not None


@pytest.fixture(scope="module")
def producers():
    measurements = _load("em_measurements",
                         os.path.join(_SERVICES_DIR, "entity-manager", "blueprints", "measurements.py"))
    # the reader dir only while importing its package: it also holds an `app` module that
    # must not shadow this service's own (test_telemetry_worker_smoke imports `app`)
    sys.path.insert(0, _READER_DIR)
    try:
        from observations import sources  # timeseries-reader package
    finally:
        sys.path.remove(_READER_DIR)
    return measurements, sources


@pytest.mark.parametrize("external_id", ["dev01", "logger_A-7", "site:loggers:L%9"])
def test_device_measurement_id_matches_reader_pruning_pattern(producers, external_id):
    measurements, sources = producers
    device = {
        "id": f"urn:ngsi-ld:Device:tenant_x:{external_id}", "type": "Device",
        "temperature": {"type": "Property", "value": 21.5},
        "profileCode": {"type": "Property", "value": "p1"},
    }
    profile = {"mapping": {"measurements": [
        {"type": "temperature", "unit": "°C", "sdmAttribute": "airTemperature",
         "observedProperty": "airTemperature"}]}}
    (dm,) = measurements.build_measurements(device, profile)
    device_id, values = nh._extract_device_measurement(dm)
    (attr,) = values
    patterns = sources.entity_like_patterns([device_id], [attr])
    assert any(_like(p, dm["id"]) for p in patterns), (dm["id"], patterns)
    # and the pattern of another device does not match
    assert not any(_like(p, dm["id"]) for p in sources.entity_like_patterns(["other"], [attr]))
    # the writer's check (worker/recompute) is the same rule as the reader's prune
    from common.observation_context import entity_id_conforms
    assert entity_id_conforms(dm["id"], device_id, attr)
    for other_dev, other_attr in (("other", attr), (device_id, "humidity")):
        pats = sources.entity_like_patterns([other_dev], [other_attr])
        assert entity_id_conforms(dm["id"], other_dev, other_attr) is any(
            _like(p, dm["id"]) for p in pats)
