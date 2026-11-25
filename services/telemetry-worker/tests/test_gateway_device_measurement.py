"""Contract: the worker stores the gateway's beacon series as it comes (no code change expected).

The three `DeviceMeasurement` entities of a gateway (`uptime`, `batteryLevel`, `rssi`) have no
profile (`deviceType`) and no deployment. Each notification must give exactly one stored event with
the right key, value and instant: the key is `controlledProperty` in the legacy shape and `name` in
the sdm shape; the instant is `dateObserved`, which for a gateway is the arrival time. The
entities are built from the entity-manager's recorded IoT Agent results
(`entity-manager/tests/fixtures/gateway_jexl_golden.json`), so the shapes are the ones the
generated mappings produce.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_TEST_DIR, _SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from test_observation_notification import FakeLookup, _run, _run_many

GOLDEN = os.path.join(_SERVICES_DIR, "entity-manager", "tests", "fixtures", "gateway_jexl_golden.json")
ARRIVAL = "2026-11-25T10:35:00.123Z"
ARRIVAL_DT = datetime(2026, 11, 25, 10, 35, 0, 123000, tzinfo=timezone.utc)
SHAPES = ("legacy", "sdm")
WHOLE = {"uptime": 86400, "batteryLevel": 0.82, "rssi": -71}


def _golden():
    with open(GOLDEN, encoding="utf-8") as handle:
        return json.load(handle)


def _entities(shape, case_id="whole message", observed_at=ARRIVAL):
    """What Orion notifies for each series entity the agent wrote, from the recorded agent output."""
    [case] = [c for c in _golden()["shapes"][shape]["cases"] if c["id"] == case_id]
    out = {}
    for entity_id, attrs in case["written"].items():
        if entity_id == "<primary>":
            continue
        entity = {"id": entity_id, "type": "DeviceMeasurement"}
        for name, spec in attrs.items():
            if name == "refDevice":
                entity[name] = {"type": "Relationship", "object": spec["value"]}
                continue
            value = ARRIVAL if spec["value"] == "<NOW>" else spec["value"]
            attr = {"type": "Property", "value": value, "observedAt": observed_at}
            if "metadata" in spec:
                attr["unitCode"] = spec["metadata"]["unitCode"]["value"]
            entity[name] = attr
        out[entity_id.rsplit(":", 1)[1]] = entity
    return out


def test_the_recorded_entities_are_the_three_series_of_each_shape():
    for shape in SHAPES:
        entities = _entities(shape)
        assert set(entities) == set(WHOLE)
        for channel, entity in entities.items():
            assert "deviceType" not in entity  # no profile
            assert ("name" in entity) == (shape == "sdm")
            assert entity["controlledProperty"]["value"] == channel


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("lookup", ["no deployment", "no lookup configured"])
def test_each_series_entity_gives_one_event_with_key_value_and_instant(shape, lookup):
    for channel, entity in _entities(shape).items():
        fake = FakeLookup(dep=None)
        event = _run(entity, fake if lookup == "no deployment" else None)
        assert event is not None, channel
        # the key (hence `attribute_name` in the store) is the channel, whichever shape
        assert event.payload["measurements"] == {channel: WHOLE[channel]}
        assert event.observed_at == ARRIVAL_DT
        assert event.device_id == "gw-aaaaaaaaaaaa"
        assert event.entity_type == "DeviceMeasurement"
        assert event.entity_id == entity["id"]
        # no profile, no deployment: stored with no observation context
        for key in ("observations", "deploymentId", "sourceKind", "location"):
            assert key not in event.payload, key
        if lookup == "no deployment":
            assert not any(call[0] == "def" for call in fake.calls)  # no profile entry was looked up


@pytest.mark.parametrize("shape", SHAPES)
def test_the_instant_is_the_arrival_time_not_the_attribute_metadata(shape):
    """`dateObserved` is authoritative; an `observedAt` that differs does not move the reading."""
    for channel, entity in _entities(shape, observed_at="2020-01-01T00:00:00.000Z").items():
        event = _run(entity, FakeLookup(dep=None))
        assert event.observed_at == ARRIVAL_DT, channel


@pytest.mark.parametrize("shape", SHAPES)
def test_a_box_clock_is_not_in_the_series(shape):
    """With a `TimeInstant` in the message the recorded agent output is the same: arrival time."""
    for case_id in ("past TimeInstant", "future TimeInstant", "garbage TimeInstant"):
        for channel, entity in _entities(shape, case_id).items():
            assert entity["timeSource"]["value"] == "server"
            event = _run(entity, FakeLookup(dep=None))
            assert event.observed_at == ARRIVAL_DT, (case_id, channel)


@pytest.mark.parametrize("shape", SHAPES)
def test_the_three_series_of_one_gateway_are_independent_events(shape):
    entities = list(_entities(shape).values())
    events = _run_many(entities, FakeLookup(dep=None))
    assert [e is not None for e in events] == [True, True, True]
    assert sorted(next(iter(e.payload["measurements"])) for e in events) == sorted(WHOLE)
    assert len({e.entity_id for e in events}) == 3


@pytest.mark.parametrize("shape", SHAPES)
def test_no_deployment_is_not_an_error(shape, caplog):
    with caplog.at_level(logging.ERROR):
        for entity in _entities(shape).values():
            assert _run(entity, FakeLookup(dep=None)) is not None
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
