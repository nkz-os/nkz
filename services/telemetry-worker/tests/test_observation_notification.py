"""Task 6: DeviceMeasurement notifications carry observation context in the payload."""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from telemetry_worker import notification_handler as nh
from telemetry_worker.config import Settings
from telemetry_worker.observations import Deployment, PropertyDef
from telemetry_worker.observation_lookup import TenantContextMissing
from test_device_measurement_notification import FakeProfileService

TENANT = "acmefarm"
DEVICE_URN = "urn:ngsi-ld:Device:acmefarm:sensor-001"
DEP_ID = "11111111-2222-3333-4444-555555555555"
AIR_TEMP = PropertyDef("airTemperature", "instantaneous", -60.0, 60.0, "°C")


def _dep(mobility="fixed", location=(-3.7, 40.4)):
    return Deployment(
        id=DEP_ID, device_urn=DEVICE_URN, observes_feature="urn:ngsi-ld:AgriParcel:p1",
        observation_context={"medium": "air"}, channel_overrides={},
        mobility=mobility, location=location,
    )


class FakeLookup:
    def __init__(self, dep=None, prop=AIR_TEMP, counter_max=None, raises=None, unit="°C"):
        self.dep, self.prop, self.counter_max, self.raises = dep, prop, counter_max, raises
        self.unit = unit
        self.calls = []

    async def deployment_at(self, tenant_id, device_urn, at):
        self.calls.append(("dep", tenant_id, device_urn, at))
        if self.raises:
            raise self.raises
        return self.dep

    async def measurement_def(self, tenant_id, profile_code, attr):
        self.calls.append(("def", tenant_id, profile_code, attr))
        d = {"unit": self.unit} if self.unit is not None else {}
        if self.counter_max is not None:
            d["counterMax"] = self.counter_max
        return d

    async def property(self, tenant_id, name):
        self.calls.append(("prop", tenant_id, name))
        return self.prop


def _entity(value=21.5, **extra):
    e = {
        "id": "urn:ngsi-ld:DeviceMeasurement:acmefarm:sensor-001:temperature",
        "type": "DeviceMeasurement",
        "refDevice": {"type": "Relationship", "object": DEVICE_URN},
        "controlledProperty": {"type": "Property", "value": "temperature"},
        "numValue": {"type": "Property", "value": value},
        "dateObserved": {"type": "Property", "value": "2026-08-29T10:00:00Z"},
        "observedProperty": {"type": "Property", "value": "airTemperature"},
        "deviceType": {"type": "Property", "value": "prof-x"},
        "timeSource": {"type": "Property", "value": "device"},
    }
    e.update(extra)
    return e


def _run(entity, lookup, tenant=TENANT):
    nh.init_handler(
        settings=Settings(postgres_url="postgresql://test:test@localhost/test"),
        profile_service=FakeProfileService(),
        event_sink=None,
        observation_lookup=lookup,
    )
    try:
        return asyncio.run(nh._process_entity(entity, tenant))
    finally:
        nh.init_handler(
            settings=Settings(postgres_url="postgresql://test:test@localhost/test"),
            profile_service=FakeProfileService(), event_sink=None,
        )


def test_device_measurement_gets_observation():
    ev = _run(_entity(), FakeLookup(dep=_dep()))
    p = ev.payload
    assert p["observations"]["temperature"]["observedProperty"] == "airTemperature"
    assert p["observations"]["temperature"]["qualityFlag"] == "valid"
    assert p["deploymentId"] == DEP_ID
    assert p["sourceKind"] == "device"
    assert p["timeSource"] == "device"
    assert p["location"] == {"type": "Point", "coordinates": [-3.7, 40.4]}


def test_lookup_uses_urn_tenant_and_aware_instant():
    lk = FakeLookup(dep=_dep())
    _run(_entity(), lk)
    dep_call = [c for c in lk.calls if c[0] == "dep"][0]
    assert dep_call[1:3] == (TENANT, DEVICE_URN)
    assert dep_call[3] == datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)


def test_no_deployment_keeps_legacy_payload(caplog):
    with caplog.at_level(logging.WARNING):
        ev = _run(_entity(), FakeLookup(dep=None))
    for k in ("observations", "deploymentId", "sourceKind", "timeSource", "location"):
        assert k not in ev.payload
    assert ev.payload["measurements"] == {"temperature": 21.5}
    msg = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
    assert TENANT in msg and DEVICE_URN in msg and "2026-08-29T10:00:00" in msg


def test_profile_without_observed_property_skips_attr():
    e = _entity()
    del e["observedProperty"]
    ev = _run(e, FakeLookup(dep=_dep()))
    assert "observations" not in ev.payload


def test_unknown_property_skips_attr_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        ev = _run(_entity(), FakeLookup(dep=_dep(), prop=None))
    assert "observations" not in ev.payload
    assert any(DEVICE_URN in r.getMessage() for r in caplog.records)


def test_counter_max_from_profile_code():
    prop = PropertyDef("rainfallTotal", "cumulative", None, None, "mm")
    e = _entity(value=500.0, deviceType={"type": "Property", "value": "prof-x"})
    e["observedProperty"]["value"] = "rainfallTotal"
    ev = _run(e, FakeLookup(dep=_dep(), prop=prop, counter_max=100, unit="mm"))
    assert ev.payload["observations"]["temperature"]["qualityFlag"] == "out_of_range"


def test_profile_lookup_uses_device_type_not_profile_code():
    lk = FakeLookup(dep=_dep())
    _run(_entity(), lk)
    assert [c[2] for c in lk.calls if c[0] == "def"] == ["prof-x"]
    # A DeviceMeasurement that only carries profileCode (not SDM deviceType) gets no profile
    # lookup: the unit guard is bypassed by an absent profile, never read from profileCode.
    e = _entity()
    del e["deviceType"]
    e["profileCode"] = {"type": "Property", "value": "prof-x"}
    lk = FakeLookup(dep=_dep())
    _run(e, lk)
    assert not [c for c in lk.calls if c[0] == "def"]


def test_missing_time_source_is_absent_not_guessed():
    e = _entity()
    del e["timeSource"]
    ev = _run(e, FakeLookup(dep=_dep()))
    assert "observations" in ev.payload
    assert "timeSource" not in ev.payload


def test_invalid_time_source_is_absent():
    ev = _run(_entity(timeSource={"type": "Property", "value": "gps"}), FakeLookup(dep=_dep()))
    assert "timeSource" not in ev.payload


def test_mobile_uses_reading_location_else_none():
    loc = {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [1.5, 42.0]}}
    ev = _run(_entity(location=loc), FakeLookup(dep=_dep("mobile")))
    assert ev.payload["location"] == {"type": "Point", "coordinates": [1.5, 42.0]}
    ev = _run(_entity(), FakeLookup(dep=_dep("mobile")))
    assert "location" not in ev.payload
    assert "observations" in ev.payload


def test_fixed_without_deployment_location_omits_location():
    ev = _run(_entity(), FakeLookup(dep=_dep(location=None)))
    assert "location" not in ev.payload


def test_fallback_observed_at_is_aware_utc():
    e = _entity()
    del e["dateObserved"]
    assert nh._extract_device_measurement_observed_at(e).tzinfo is not None
    assert nh._extract_observed_at({"id": "x"}).tzinfo is not None


def test_naive_timestamps_are_treated_as_utc():
    e = _entity(dateObserved={"type": "Property", "value": "2026-08-29T10:00:00"})
    got = nh._extract_device_measurement_observed_at(e)
    assert got == datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    ts = nh._extract_observed_at({"a": {"type": "Property", "value": 1, "observedAt": "2026-08-29T10:00:00"}})
    assert ts.tzinfo is not None


def test_tenant_none_skips_observations_and_logs(caplog):
    lk = FakeLookup(dep=_dep())
    with caplog.at_level(logging.WARNING):
        ev = _run(_entity(), lk, tenant=None)
    assert ev is not None and "observations" not in ev.payload
    assert lk.calls == []
    assert any(DEVICE_URN in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("exc", [RuntimeError("db down"), TenantContextMissing("x")])
def test_lookup_error_never_drops_event(exc, caplog):
    with caplog.at_level(logging.ERROR):
        ev = _run(_entity(), FakeLookup(raises=exc))
    assert ev is not None and "observations" not in ev.payload
    assert any(r.levelno >= logging.ERROR and TENANT in r.getMessage() for r in caplog.records)


def test_non_device_measurement_untouched():
    lk = FakeLookup(dep=_dep())
    e = {"id": "urn:ngsi-ld:AgriSensor:acmefarm:s1", "type": "AgriSensor",
         "temperature": {"type": "Property", "value": 20.0, "observedAt": "2026-08-29T10:00:00Z"}}
    ev = _run(e, lk)
    assert "observations" not in ev.payload and lk.calls == []


def test_notification_unit_mismatch_writes_no_observation_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        ev = _run(_entity(), FakeLookup(dep=_dep(), unit="µm"))
    assert "observations" not in ev.payload
    assert ev.payload["measurements"] == {"temperature": 21.5}
    recs = [r.getMessage() for r in caplog.records if "observation_unit_mismatch" in r.getMessage()]
    assert len(recs) == 1
    for needle in (TENANT, DEVICE_URN, "2026-08-29T10:00:00", "temperature", "µm", "°C"):
        assert needle in recs[0]


def test_notification_missing_profile_unit_writes_no_observation(caplog):
    with caplog.at_level(logging.WARNING):
        ev = _run(_entity(), FakeLookup(dep=_dep(), unit=None))
    assert "observations" not in ev.payload
    assert any("observation_unit_mismatch" in r.getMessage() for r in caplog.records)


def test_conforming_entity_id_gets_observation_nonconforming_does_not(caplog):
    """The reader prunes compressed batches by entity id (common.observation_context
    entity_id_conforms): a non-conforming row must carry no observation context at all."""
    ok = _run(_entity(), FakeLookup(dep=_dep()))
    assert "temperature" in ok.payload["observations"]
    for bad in ("urn:ngsi-ld:DeviceMeasurement:acmefarm:sensor-001:humidity",
                "urn:ngsi-ld:DeviceMeasurement:acmefarm:other-device:temperature",
                "urn:ngsi-ld:DeviceMeasurement:acmefarm:xsensor-001:temperature"):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            ev = _run(_entity(id=bad), FakeLookup(dep=_dep()))
        p = ev.payload
        assert "observations" not in p and "deploymentId" not in p and "location" not in p
        assert p["measurements"] == {"temperature": 21.5}  # the reading itself is kept
        msgs = [r.getMessage() for r in caplog.records
                if "observation_entity_id_nonconforming" in r.getMessage()]
        assert len(msgs) == 1
        for needle in (f"tenant_id={TENANT}", f"device_urn={DEVICE_URN}", f"entity_id={bad}",
                       "attribute=temperature", "observed_at=2026-08-29T10:00:00+00:00"):
            assert needle in msgs[0], (needle, msgs[0])


def _run_many(entities, lookup, tenant=TENANT):
    """Several readings under one handler wiring (the throttle state survives between them)."""
    nh.init_handler(
        settings=Settings(postgres_url="postgresql://test:test@localhost/test"),
        profile_service=FakeProfileService(), event_sink=None, observation_lookup=lookup,
    )

    async def go():
        return [await nh._process_entity(e, tenant) for e in entities]

    try:
        return asyncio.run(go())
    finally:
        nh.init_handler(
            settings=Settings(postgres_url="postgresql://test:test@localhost/test"),
            profile_service=FakeProfileService(), event_sink=None,
        )


def _warnings(caplog, needle):
    return [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and needle in r.getMessage()]


def _readings(n, **extra):
    return [_entity(value=20.0 + i, dateObserved={"type": "Property",
                                                  "value": f"2026-08-29T10:0{i}:00Z"}, **extra)
            for i in range(n)]


def test_undeployed_device_warns_once_not_per_reading(caplog):
    with caplog.at_level(logging.WARNING):
        events = _run_many(_readings(5), FakeLookup(dep=None))
    assert all(ev is not None and "observations" not in ev.payload for ev in events)
    lines = _warnings(caplog, "no deployment")
    assert len(lines) == 1
    assert TENANT in lines[0] and DEVICE_URN in lines[0] and "2026-08-29T10:00:00" in lines[0]


def test_missing_observed_property_warns_once(caplog):
    entities = _readings(4)
    for e in entities:
        del e["observedProperty"]
    with caplog.at_level(logging.WARNING):
        _run_many(entities, FakeLookup(dep=_dep()))
    assert len(_warnings(caplog, "no observedProperty")) == 1


def test_unit_mismatch_warns_once(caplog):
    with caplog.at_level(logging.WARNING):
        _run_many(_readings(3), FakeLookup(dep=_dep(), unit="µm"))
    assert len(_warnings(caplog, "observation_unit_mismatch")) == 1


def test_nonconforming_entity_id_warns_once(caplog):
    bad = "urn:ngsi-ld:DeviceMeasurement:acmefarm:other-device:temperature"
    with caplog.at_level(logging.WARNING):
        _run_many(_readings(3, id=bad), FakeLookup(dep=_dep()))
    assert len(_warnings(caplog, "observation_entity_id_nonconforming")) == 1


def test_throttle_is_per_device(caplog):
    other = "urn:ngsi-ld:Device:acmefarm:sensor-002"
    entities = _readings(2) + _readings(2, refDevice={"type": "Relationship", "object": other})
    with caplog.at_level(logging.WARNING):
        _run_many(entities, FakeLookup(dep=None))
    lines = _warnings(caplog, "no deployment")
    assert len(lines) == 2
    assert DEVICE_URN in lines[0] and other in lines[1]
