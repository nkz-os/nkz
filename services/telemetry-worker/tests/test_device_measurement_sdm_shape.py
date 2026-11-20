"""DeviceMeasurement SDM shape (design A.1/A.1b/A.2/A.5): the worker reads both shapes.

- New shape: `name` present -> channel = `name`, property = `controlledProperty` (only when
  the profile entry of that channel declares the same `observedProperty`).
- Legacy shape: no `name` -> channel = `controlledProperty`, property = the profile entry's
  `observedProperty` (`deviceType` + channel).
- Storage keys (payload `measurements`, hence `attribute_name`) are the CHANNEL in both.
- Statistic channels never get observation context (own reason, throttled at INFO).
- `name` and `refDevice` are read under the short name or either IRI.
"""

import logging
import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_TEST_DIR, _SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from telemetry_worker import notification_handler as nh
from test_observation_notification import (
    DEP_ID,
    DEVICE_URN,
    TENANT,
    FakeLookup,
    _dep,
    _run,
    _run_many,
)

DM_PREFIX = "urn:ngsi-ld:DeviceMeasurement:acmefarm:sensor-001"
GATE = "observation_skipped_statistic"


def _prop(value):
    return {"type": "Property", "value": value}


def _sdm(channel, controlled="airTemperature", mtype="instant", value=21.5,
         at="2026-08-29T10:00:00Z", name_key="name", **extra):
    e = {
        "id": f"{DM_PREFIX}:{channel}",
        "type": "DeviceMeasurement",
        "refDevice": {"type": "Relationship", "object": DEVICE_URN},
        name_key: _prop(channel),
        "controlledProperty": _prop(controlled),
        "measurementType": _prop(mtype),
        "numValue": _prop(value),
        "dateObserved": _prop(at),
        "deviceType": _prop("prof-x"),
        "timeSource": _prop("device"),
    }
    e.update(extra)
    return e


def _legacy(channel, mtype="temperature", value=21.5, at="2026-08-29T10:00:00Z", **extra):
    e = {
        "id": f"{DM_PREFIX}:{channel}",
        "type": "DeviceMeasurement",
        "refDevice": {"type": "Relationship", "object": DEVICE_URN},
        "controlledProperty": _prop(channel),
        "measurementType": _prop(mtype),
        "numValue": _prop(value),
        "dateObserved": _prop(at),
        "deviceType": _prop("prof-x"),
        "timeSource": _prop("device"),
    }
    e.update(extra)
    return e


def _gate_lines(caplog):
    return [r for r in caplog.records if GATE in r.getMessage()]


# ---------------------------------------------------------------- new shape


def test_new_shape_keys_by_name_and_resolves_controlled_property():
    lk = FakeLookup(dep=_dep())
    ev = _run(_sdm("airTemperature"), lk)
    assert ev.payload["measurements"] == {"airTemperature": 21.5}
    obs = ev.payload["observations"]["airTemperature"]
    assert obs["observedProperty"] == "airTemperature"
    assert ev.payload["deploymentId"] == DEP_ID
    assert ("def", TENANT, "prof-x", "airTemperature") in lk.calls
    assert ("prop", TENANT, "airTemperature") in lk.calls


def test_new_shape_name_differs_from_controlled_property():
    """A channel whose name is not the catalogue name (path_check pod gate shape)."""
    ev = _run(_sdm("temperatureSensor2", controlled="airTemperature"), FakeLookup(dep=_dep()))
    assert ev.payload["measurements"] == {"temperatureSensor2": 21.5}
    assert ev.payload["observations"]["temperatureSensor2"]["observedProperty"] == "airTemperature"


def test_new_shape_property_must_match_the_profile_opt_in():
    """controlledProperty falls back to the base attribute when the profile has no catalogue
    name; that fallback must not opt the channel in (contract 8.3, design A.2)."""
    ev = _run(_sdm("airTemperature"), FakeLookup(dep=_dep(), observed_property=None))
    assert "observations" not in ev.payload
    ev = _run(_sdm("airTemperature"), FakeLookup(dep=_dep(), observed_property="soilTemperature"))
    assert "observations" not in ev.payload
    ev = _run(_sdm("airTemperature"), FakeLookup(dep=_dep(), has_def=False))
    assert "observations" not in ev.payload
    assert ev.payload["measurements"] == {"airTemperature": 21.5}


def test_new_shape_property_mismatch_is_reported(caplog):
    with caplog.at_level(logging.WARNING):
        _run(_sdm("airTemperature"), FakeLookup(dep=_dep(), observed_property="soilTemperature"))
    msgs = [r.getMessage() for r in caplog.records
            if "observation_property_mismatch" in r.getMessage()]
    assert len(msgs) == 1
    for needle in (TENANT, DEVICE_URN, "attribute=airTemperature",
                   "controlledProperty='airTemperature'", "profile_observedProperty='soilTemperature'"):
        assert needle in msgs[0], needle


def test_both_shapes_give_the_same_payload():
    """Same profile, same reading: only `raw` differs between the shapes."""
    new = _run(_sdm("airTemperature"), FakeLookup(dep=_dep()))
    old = _run(_legacy("airTemperature"), FakeLookup(dep=_dep()))
    strip = lambda p: {k: v for k, v in p.items() if k != "raw"}  # noqa: E731
    assert strip(new.payload) == strip(old.payload)
    assert "observations" in new.payload
    assert new.device_id == old.device_id == "sensor-001"
    assert new.entity_id == old.entity_id


# ---------------------------------------------------------------- legacy shape


def test_legacy_shape_takes_property_from_the_profile():
    lk = FakeLookup(dep=_dep(), observed_property="airTemperature")
    ev = _run(_legacy("airTemperature"), lk)
    assert ev.payload["measurements"] == {"airTemperature": 21.5}
    assert ev.payload["observations"]["airTemperature"]["observedProperty"] == "airTemperature"
    assert ("def", TENANT, "prof-x", "airTemperature") in lk.calls


def test_legacy_measurement_type_is_the_profile_type_not_a_gate(caplog):
    """Legacy `measurementType` carries the profile `type`, never `instant`."""
    with caplog.at_level(logging.DEBUG):
        ev = _run(_legacy("airTemperature", mtype="temperature"), FakeLookup(dep=_dep()))
    assert "observations" in ev.payload
    assert not _gate_lines(caplog)


def test_legacy_without_device_type_gets_no_observation():
    e = _legacy("airTemperature")
    del e["deviceType"]
    ev = _run(e, FakeLookup(dep=_dep()))
    assert "observations" not in ev.payload
    assert ev.payload["measurements"] == {"airTemperature": 21.5}


# ---------------------------------------------------------------- statistic gate


def test_new_shape_min_and_max_kept_separate_without_observation(caplog):
    lk = FakeLookup(dep=_dep(), statistic=None)
    with caplog.at_level(logging.DEBUG):
        mn = _run(_sdm("airTemperatureMin", mtype="min", value=10.0), lk)
        mx = _run(_sdm("airTemperatureMax", mtype="max", value=30.0), lk)
    assert mn.payload["measurements"] == {"airTemperatureMin": 10.0}
    assert mx.payload["measurements"] == {"airTemperatureMax": 30.0}
    for ev in (mn, mx):
        for k in ("observations", "deploymentId", "sourceKind", "location", "timeSource"):
            assert k not in ev.payload, k
    lines = _gate_lines(caplog)
    assert len(lines) == 2
    assert all(r.levelno == logging.INFO for r in lines)
    for needle in (TENANT, DEVICE_URN, "attribute=airTemperatureMin", "measurementType='min'"):
        assert needle in lines[0].getMessage(), needle
    # Never the misleading unit-mismatch reason, never a warning.
    assert not [r for r in caplog.records if "observation_unit_mismatch" in r.getMessage()]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("mtype", ["min", "max", "stddev"])
def test_new_shape_spread_statistics_are_gated(mtype):
    ev = _run(_sdm("airTemperature", mtype=mtype), FakeLookup(dep=_dep()))
    assert "observations" not in ev.payload
    assert ev.payload["measurements"] == {"airTemperature": 21.5}


@pytest.mark.parametrize("mtype", ["instant", "mean", "last", "sum"])
def test_new_shape_representative_values_get_context(mtype):
    """Design A.2: mean/last/sum are published under the BASE channel and are its value."""
    stat = None if mtype == "instant" else mtype
    ev = _run(_sdm("airTemperature", mtype=mtype), FakeLookup(dep=_dep(), statistic=stat))
    assert ev.payload["observations"]["airTemperature"]["observedProperty"] == "airTemperature"


def _per_channel_lookup():
    """Profile airTemperature with statistics [mean, min, max], as expanded by channel_def."""
    stats = {"airTemperature": "mean", "airTemperatureMin": "min", "airTemperatureMax": "max"}

    class _Lookup(FakeLookup):
        async def channel_def(self, tenant_id, profile_code, attr):
            d = await super().channel_def(tenant_id, profile_code, attr)
            d["statistic"] = stats[attr]
            return d

    return _Lookup(dep=_dep())


@pytest.mark.parametrize("legacy", [False, True])
def test_mean_min_max_profile_base_gets_context_min_max_do_not(legacy, caplog):
    lk = _per_channel_lookup()
    if legacy:
        readings = [_legacy("airTemperature", value=20.0), _legacy("airTemperatureMin", value=10.0),
                    _legacy("airTemperatureMax", value=30.0)]
    else:
        readings = [_sdm("airTemperature", mtype="mean", value=20.0),
                    _sdm("airTemperatureMin", mtype="min", value=10.0),
                    _sdm("airTemperatureMax", mtype="max", value=30.0)]
    with caplog.at_level(logging.DEBUG):
        base, mn, mx = _run_many(readings, lk)
    assert base.payload["observations"]["airTemperature"]["observedProperty"] == "airTemperature"
    assert mn.payload["measurements"] == {"airTemperatureMin": 10.0}
    assert mx.payload["measurements"] == {"airTemperatureMax": 30.0}
    assert "observations" not in mn.payload and "observations" not in mx.payload
    assert len(_gate_lines(caplog)) == 2


def test_sum_channel_gets_context_in_both_shapes():
    for e in (_sdm("airTemperature", mtype="sum"), _legacy("airTemperature")):
        ev = _run(e, FakeLookup(dep=_dep(), statistic="sum"))
        assert "observations" in ev.payload


def test_stddev_is_gated_in_both_shapes():
    for e in (_sdm("airTemperatureStdDev", mtype="stddev"), _legacy("airTemperatureStdDev")):
        ev = _run(e, FakeLookup(dep=_dep(), statistic="stddev"))
        assert "observations" not in ev.payload
        assert ev.payload["measurements"] == {"airTemperatureStdDev": 21.5}


@pytest.mark.parametrize("mtype", [None, "", "INSTANT", "average", 3])
def test_new_shape_missing_or_unknown_measurement_type_is_gated(mtype):
    e = _sdm("airTemperature")
    if mtype is None:
        del e["measurementType"]
    else:
        e["measurementType"] = _prop(mtype)
    ev = _run(e, FakeLookup(dep=_dep()))
    assert "observations" not in ev.payload
    assert ev.payload["measurements"] == {"airTemperature": 21.5}


def test_legacy_expanded_statistic_channel_is_gated(caplog):
    lk = FakeLookup(dep=_dep(), statistic="min")
    with caplog.at_level(logging.DEBUG):
        ev = _run(_legacy("airTemperatureMin", value=10.0), lk)
    assert ev.payload["measurements"] == {"airTemperatureMin": 10.0}
    assert "observations" not in ev.payload
    lines = _gate_lines(caplog)
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    assert "statistic='min'" in lines[0].getMessage()


@pytest.mark.parametrize("stat", ["min", "max", "stddev", "unknown"])
def test_profile_statistic_gates_even_if_new_shape_says_instant(stat):
    ev = _run(_sdm("airTemperature"), FakeLookup(dep=_dep(), statistic=stat))
    assert "observations" not in ev.payload


def test_statistic_gate_is_throttled_per_channel(caplog):
    readings = [_sdm("airTemperatureMin", mtype="min", value=10.0 + i,
                     at=f"2026-08-29T10:0{i}:00Z") for i in range(4)]
    with caplog.at_level(logging.DEBUG):
        events = _run_many(readings, FakeLookup(dep=_dep()))
    assert all(ev is not None and "observations" not in ev.payload for ev in events)
    assert len(_gate_lines(caplog)) == 1


# ---------------------------------------------------------------- IRI tolerance

NAME_KEYS = ["name", "https://schema.org/name", "https://smartdatamodels.org/name"]
REF_DEVICE_KEYS = [
    "refDevice",
    "hasDevice",  # design A.5 R1: the old attribute compacts to the short `hasDevice` after the remap
    "https://smartdatamodels.org/dataModel.Device/hasDevice",
    "https://smartdatamodels.org/dataModel.Device/refDevice",
]


@pytest.mark.parametrize("key", NAME_KEYS)
def test_name_is_read_in_every_form(key):
    e = _sdm("airTemperatureMin", mtype="min", name_key=key, value=10.0)
    device_id, measurements = nh._extract_device_measurement(e)
    assert device_id == "sensor-001"
    assert measurements == {"airTemperatureMin": 10.0}
    ev = _run(e, FakeLookup(dep=_dep()))
    assert ev.payload["measurements"] == {"airTemperatureMin": 10.0}
    assert "observations" not in ev.payload


@pytest.mark.parametrize("key", NAME_KEYS)
def test_name_form_resolves_observation(key):
    ev = _run(_sdm("airTemperature", name_key=key), FakeLookup(dep=_dep()))
    assert ev.payload["observations"]["airTemperature"]["observedProperty"] == "airTemperature"


@pytest.mark.parametrize("key", REF_DEVICE_KEYS)
def test_ref_device_is_read_in_every_form(key):
    for e in (_sdm("airTemperature"), _legacy("airTemperature")):
        rel = e.pop("refDevice")
        e[key] = rel
        lk = FakeLookup(dep=_dep())
        ev = _run(e, lk)
        assert ev is not None
        assert ev.device_id == "sensor-001"
        dep_calls = [c for c in lk.calls if c[0] == "dep"]
        assert dep_calls and dep_calls[0][2] == DEVICE_URN
        assert "observations" in ev.payload


def test_ref_device_short_name_wins_over_iri_forms():
    e = _sdm("airTemperature")
    e["https://smartdatamodels.org/dataModel.Device/hasDevice"] = {
        "type": "Relationship", "object": "urn:ngsi-ld:Device:acmefarm:other"}
    device_id, _ = nh._extract_device_measurement(e)
    assert device_id == "sensor-001"


def test_ref_device_missing_in_every_form_drops_the_reading():
    e = _sdm("airTemperature")
    del e["refDevice"]
    assert _run(e, FakeLookup(dep=_dep())) is None


@pytest.mark.parametrize("bad", ["", None, 5, ["airTemperatureMin"]])
def test_new_shape_with_unusable_name_is_skipped_never_keyed_by_controlled_property(bad):
    """Falling back to controlledProperty would collapse Min/Max into the base channel."""
    e = _sdm("airTemperatureMin", mtype="min")
    e["name"] = _prop(bad)
    assert nh._extract_device_measurement(e) == ("sensor-001", {})
    assert _run(e, FakeLookup(dep=_dep())) is None


def test_new_shape_name_without_value_is_skipped():
    e = _sdm("airTemperatureMin", mtype="min")
    e["name"] = {"type": "Property"}
    assert _run(e, FakeLookup(dep=_dep())) is None


def test_new_shape_with_unusable_name_warns_once(caplog):
    readings = []
    for i in range(3):
        e = _sdm("airTemperatureMin", mtype="min", at=f"2026-08-29T10:0{i}:00Z")
        e["name"] = _prop("")
        readings.append(e)
    with caplog.at_level(logging.WARNING):
        events = _run_many(readings, FakeLookup(dep=_dep()))
    assert events == [None, None, None]
    msgs = [r.getMessage() for r in caplog.records
            if "device_measurement_name_unusable" in r.getMessage()]
    assert len(msgs) == 1
    assert TENANT in msgs[0] and f"{DM_PREFIX}:airTemperatureMin" in msgs[0]
