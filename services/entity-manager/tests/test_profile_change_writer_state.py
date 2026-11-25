"""A profile change when the IoT Agent may write the measurements: the agent's attributes, the
DeviceModel and the Device are written in one order, and a failure puts the previous attributes
of the device's writer state back."""
import copy
import json
from unittest.mock import patch

import pytest
import requests

from tests.telemetry_fakes import (AUTH, PARCEL, PROFILE, TENANT, FakeState, client, post_json,
                                   seed_datalogger, wired)

import dataloggers  # noqa: E402

DL = "11111111-1111-1111-1111-111111111111"
URN = f"urn:ngsi-ld:Device:{TENANT}:S1"
MACHINE = f"urn:ngsi-ld:ManufacturingMachine:{TENANT}:S1"
MODEL2 = f"urn:ngsi-ld:DeviceModel:{TENANT}:p2"
OLD = PROFILE["mapping"]["measurements"]
# `stemDiameter` has no official value, so changing to it also deletes `controlledProperty`.
NEW = [{"type": "x", "sdmAttribute": "stemDiameter", "unit": "mm"}]

STATES = {"em": ("em", None), "iota-legacy": ("iota", "legacy"), "iota-sdm": ("iota", "sdm")}
each_state = pytest.mark.parametrize("state", STATES.values(), ids=STATES.keys())


def _prop(value):
    return {"type": "Property", "value": value}


def _ref(code):
    return {"type": "Relationship", "object": f"urn:ngsi-ld:DeviceModel:{TENANT}:{code}"}


# Every one is on profile "p"; where there is another code, it is "other".
ON_P = {
    "model-only": {"refDeviceModel": _ref("p")},
    "legacy-only": {"profileCode": _prop("p")},
    "both-profile-code-wins": {"refDeviceModel": _ref("other"), "profileCode": _prop("p")},
}
each_device = pytest.mark.parametrize("attrs", ON_P.values(), ids=ON_P.keys())

MODEL_FAILURES = {
    "model-network": {"upsert": requests.exceptions.ConnectionError("down")},
    "model-refused": {"upsert": [{"entityId": MODEL2, "error": {"status": 400}}]},
    "model-delete": {"orion_attr_delete": 500},
}
POST_FAILURES = {
    "post-500": {"orion_attrs": 500},
    "post-207": {"orion_attrs": 207},
    "post-network": {"orion_attrs": requests.exceptions.ConnectionError("down")},
}
FAILURES = {**MODEL_FAILURES, **POST_FAILURES}
each_failure = pytest.mark.parametrize("fail", FAILURES.values(), ids=FAILURES.keys())


def _state(state, urn=URN):
    st = FakeState()
    seed_datalogger(st, dl_id=DL)
    for code, measurements in (("p", OLD), ("p2", NEW), ("other", NEW)):
        st.profiles[code] = {"tenant_id": TENANT, "name": code,
                             "mapping": {"measurements": measurements}}
    st.devices.append(dict(datalogger_id=DL, tenant_id=TENANT, device_urn=urn, external_id="S1",
                           dm_writer=state[0], dm_shape=state[1]))
    return st


def _entity(attrs, urn=URN):
    return {"id": urn, "type": urn.split(":")[2], "name": _prop("Probe"),
            "controlledProperty": _prop(["temperature"]), **attrs}


def _move_to(st, state):
    for row in st.devices:
        row["dm_writer"], row["dm_shape"] = state


def _change(w, entity):
    w.orion.entities[entity["id"]] = copy.deepcopy(entity)
    return client().put(f"/api/sensors/devices/{entity['id']}/profile", headers=AUTH,
                        content_type="application/json", data=json.dumps({"profile": "p2"}))


def _attributes(state, code, measurements, urn=URN):
    return dataloggers._attributes_for_state(state, TENANT, urn, "S1", code, measurements)


def _writes(st):
    return [e for e in st.log if e[0] in ("iota", "orion")]


@each_state
@each_device
def test_the_agent_then_the_model_then_one_device_write_then_the_delete(attrs, state):
    st = _state(state)
    with wired(st) as w:
        r = _change(w, _entity(attrs))
        sent = [a for _, a in st.iota_attrs]
        assert r.status_code == 200, r.get_json()
        assert sent == [_attributes(state, "p2", NEW)]
    assert _writes(st) == [
        ("iota", "update", "S1"), ("orion", "upsert", MODEL2),
        ("orion", "delete_attr", MODEL2, "controlledProperty"),
        ("orion", "attrs", URN), ("orion", "delete_attr", URN, "controlledProperty")]
    assert w.orion.attr_posts == [(URN, {
        "profileCode": _prop("p2"), "sensorType": _prop("p2"), "refDeviceModel": _ref("p2"),
        "deviceCategory": _prop(["sensor"])})]


@each_failure
@each_state
@each_device
def test_a_failed_model_or_device_write_restores_the_attributes_of_the_old_profile(attrs, state, fail):
    st = _state(state)
    st.fail.update(fail)
    entity = _entity(attrs)
    with wired(st) as w:
        r = _change(w, entity)
        assert [a for _, a in st.iota_attrs] == [_attributes(state, "p2", NEW),
                                                 _attributes(state, "p", OLD)]
    step = "device_model" if fail in MODEL_FAILURES.values() else "orion_profile"
    assert r.status_code == 502 and r.get_json()["step"] == step, r.get_json()
    assert w.orion.entities[URN] == entity
    assert ("orion", "delete_attr", URN, "controlledProperty") not in st.log


def _readable(reads):
    """`device_dm_state` that fails after that many reads."""
    real, seen = dataloggers.device_dm_state, []

    def read(tenant, urn):
        seen.append(urn)
        if len(seen) > reads:
            raise dataloggers.ApiError(502, "Database error", "db")
        return real(tenant, urn)

    return patch.object(dataloggers, "device_dm_state", read)


@each_failure
def test_an_unreadable_state_restores_from_the_state_the_attributes_were_pushed_for(fail, caplog):
    """The device moves to the IoT Agent during the push; for the restore it cannot be read."""
    st = _state(("em", None))
    st.after_update = lambda n: _move_to(st, ("iota", "sdm")) if n == 1 else None
    st.fail.update(fail)
    with wired(st) as w, caplog.at_level("CRITICAL"):
        with _readable(reads=3):  # before the push, after it, after the second one
            r = _change(w, _entity(ON_P["model-only"]))
        assert [a for _, a in st.iota_attrs] == [
            _attributes(("em", None), "p2", NEW), _attributes(("iota", "sdm"), "p2", NEW),
            _attributes(("iota", "sdm"), "p", OLD)]
    assert r.status_code == 502, r.get_json()
    assert any("last known state ('iota', 'sdm')" in x.getMessage() for x in caplog.records)


@each_failure
def test_the_restore_reads_the_state_again(fail):
    """The device moves to the IoT Agent between the push and the failure."""
    st = _state(("em", None))
    st.fail.update(fail)
    with wired(st) as w:
        real = dataloggers.push_iota_attributes
        calls = []

        def push(*a):
            out = real(*a)
            if not calls:
                _move_to(st, ("iota", "legacy"))
            calls.append(a[3])
            return out

        with patch.object(dataloggers, "push_iota_attributes", push):
            r = _change(w, _entity(ON_P["both-profile-code-wins"]))
        assert calls == ["p2", "p"]
        assert [a for _, a in st.iota_attrs] == [_attributes(("em", None), "p2", NEW),
                                                 _attributes(("iota", "legacy"), "p", OLD)]
    assert r.status_code == 502, r.get_json()


@each_failure
def test_a_restore_that_fails_is_logged_with_both_codes_and_keeps_the_error(fail, caplog):
    st = _state(("iota", "sdm"))
    st.fail.update(fail)
    st.after_update = lambda n: st.fail.__setitem__("iota_update", True)
    with wired(st) as w, caplog.at_level("CRITICAL"):
        r = _change(w, _entity(ON_P["model-only"]))
    step = "device_model" if fail in MODEL_FAILURES.values() else "orion_profile"
    assert r.status_code == 502 and r.get_json()["step"] == step, r.get_json()
    assert any("old_profile=p new_profile=p2" in x.getMessage() for x in caplog.records)


@each_state
def test_a_delete_that_fails_alone_is_502_and_restores_nothing(state):
    """Agent and entity are both on the new profile."""
    st = _state(state)
    with wired(st) as w:
        delete = w.orion.delete
        w.orion.delete = lambda url, **kw: (st.fail.update(orion_attr_delete=500)
                                            if f"{URN}/attrs/" in url else None) or delete(url, **kw)
        r = _change(w, _entity(ON_P["model-only"]))
        assert [a for _, a in st.iota_attrs] == [_attributes(state, "p2", NEW)]
    assert r.status_code == 502 and r.get_json()["step"] == "orion_profile_leftover", r.get_json()
    assert w.orion.entities[URN]["refDeviceModel"] == _ref("p2")


def test_a_writer_state_that_is_not_confirmed_writes_nothing_to_the_broker():
    st = _state(("em", None))
    st.after_update = lambda n: st.fail.update(dm_state=True) if n == 1 else None
    with wired(st) as w:
        r = _change(w, _entity(ON_P["model-only"]))
        assert st.iota_attrs[-1] == ("S1", _attributes(("em", None), "p", OLD))
    assert r.status_code == 502 and r.get_json()["step"] == "dm_state", r.get_json()
    assert not any(e[0] == "orion" for e in st.log)


def test_a_profile_code_that_fits_no_model_id_is_400_before_the_state_is_read():
    st = _state(("iota", "sdm"))
    st.profiles["bad:code"] = st.profiles["p2"]
    st.fail["dm_state"] = True
    with wired(st) as w:
        w.orion.entities[URN] = _entity(ON_P["model-only"])
        with pytest.raises(dataloggers.ApiError) as err:
            dataloggers.change_device_profile(TENANT, URN, "bad:code")
    assert err.value.status == 400 and _writes(st) == []


# ---- machines ----------------------------------------------------------------

MACHINE_ATTRS = {"profileCode": _prop("p"), "refDeviceModel": _ref("other")}


@each_state
def test_a_machine_gets_the_agent_update_and_the_same_patch(state):
    st = _state(state, MACHINE)
    with wired(st) as w:
        patches, original = [], w.orion.patch
        w.orion.patch = lambda url, **kw: patches.append((url, json.dumps(kw["json"]))) or original(url, **kw)
        r = _change(w, _entity({**MACHINE_ATTRS, "sensorType": _prop("p")}, MACHINE))
        assert [a for _, a in st.iota_attrs] == [_attributes(state, "p2", NEW, MACHINE)]
    assert r.status_code == 200, r.get_json()
    assert _writes(st) == [("iota", "update", "S1"), ("orion", "patch")]
    assert patches == [(
        f"{dataloggers.ORION_URL}/ngsi-ld/v1/entities/{MACHINE}/attrs",
        '{"profileCode": {"type": "Property", "value": "p2"}, '
        '"sensorType": {"type": "Property", "value": "p2"}}')]


@each_state
def test_a_machine_whose_patch_fails_gets_its_previous_attributes_back(state):
    st = _state(state, MACHINE)
    st.fail["orion_patch"] = True
    with wired(st) as w:
        r = _change(w, _entity(MACHINE_ATTRS, MACHINE))
        assert [a for _, a in st.iota_attrs] == [_attributes(state, "p2", NEW, MACHINE),
                                                 _attributes(state, "p", OLD, MACHINE)]
    assert r.status_code == 502 and r.get_json()["step"] == "orion_profile", r.get_json()


# ---- the other two paths -----------------------------------------------------

@each_state
@each_device
def test_a_profile_edit_pushes_the_attributes_of_the_state_with_the_code_of_the_profile(attrs, state):
    st = _state(state)
    with wired(st) as w:
        w.orion.entities[URN] = _entity(attrs)
        out = dataloggers.reprovision_profile_devices(TENANT, "p", OLD)
        assert st.iota_attrs == [("S1", _attributes(state, "p", OLD))]
        assert dataloggers.reprovision_profile_devices(TENANT, "other", NEW)["reprovisioned"] == []
    assert out == {"reprovisioned": [URN], "failed": []}


def _register():
    return post_json(client(), "/api/sensors/register", {
        "external_id": "S1", "name": "Sensor 1", "profile": "p",
        "location": {"lat": 42.0, "lon": -2.0}, "controlledAsset": [PARCEL], "datalogger_id": DL,
        "validFrom": "2026-03-01T08:00:00+00:00"})


def test_registration_provisions_the_agent_for_the_profile_code():
    st = _state(("em", None))
    st.devices.clear()
    provisioned = []
    with wired(st), patch.object(dataloggers, "provision_iota_device",
                                 lambda *a: provisioned.append(a)):
        r = _register()
    assert r.status_code == 201, r.get_json()
    assert provisioned == [(TENANT, "KEY123", "S1", URN, "Device", OLD, "p")]


@pytest.mark.parametrize("profile", [
    {"sdm_entity_type": "Sensor"},
    {"mapping": {"measurements": [{"type": "numValue", "sdmAttribute": "soilMoisture", "unit": "%"}]}},
], ids=["type", "mapping"])
def test_a_registration_refused_with_400_writes_no_model(profile):
    st = _state(("em", None))
    st.devices.clear()
    st.profiles["p"].update(profile)
    with wired(st) as w:
        r = _register()
    assert r.status_code == 400, r.get_json()
    assert _writes(st) == [] and w.orion.device_models == {}


# ---- the writer move lock (shared): profile changes against a writer move -----------------------
# `move_lock` (conftest) is a working fake lock; here its events, the reads and the writes share
# one log, so the order of the request can be asserted.
import logging  # noqa: E402
from contextlib import ExitStack, contextmanager  # noqa: E402

import dm_writer_cutover  # noqa: E402
import writer_move_lock  # noqa: E402

def _attrs(state, code, measurements, urn=URN):
    """The attributes the agent is given, built with the stand-in agent module."""
    from tests.telemetry_fakes import fake_iota
    with patch.object(dataloggers, "iot_agent", fake_iota(FakeState())):
        return _attributes(state, code, measurements, urn)


READS = ("read_profile", "_orion_devices", "device_dm_state")
LOCK_ALIVE, LOCK_DEAD = ("lock", "alive"), ("lock", "dead")


class _Log(list):
    """`FakeState.log` that tells a test after each entry (where it kills the lock)."""

    after = None

    def append(self, item):
        super().append(item)
        if self.after:
            self.after(len(self), item)


class _Clock:
    now = 1000.0

    def monotonic(self):
        return self.now


@contextmanager
def _traced(st, move_lock, clock=None):
    """Lock events and reads land in `st.log`; yields the locks the request took, in order."""
    st.log = _Log()
    move_lock.events = st.log
    locks, real = [], {n: getattr(dataloggers, n) for n in READS}
    take = dataloggers.profile_change_lock

    def read(name):
        def f(*a, **kw):
            st.log.append(("read", name))
            return real[name](*a, **kw)
        return f

    @contextmanager
    def capture(tenant):
        with take(tenant) as lock:
            locks.append(lock)
            yield lock

    with ExitStack() as stack:
        for name in READS:
            stack.enter_context(patch.object(dataloggers, name, read(name)))
        stack.enter_context(patch.object(dataloggers, "profile_change_lock", capture))
        if clock is not None:
            stack.enter_context(patch.object(dataloggers, "time", clock))
            stack.enter_context(patch.object(writer_move_lock, "time", clock))
        yield locks
    # every lock a request takes (the re-take in a restore included) is the SHARED lock of THIS tenant
    assert move_lock.lock_queries() == [("shared", writer_move_lock.LOCK_CLASS, TENANT)] * len(
        move_lock.conns)


def test_a_change_takes_the_shared_lock_of_its_own_tenant_and_so_does_the_restore(move_lock):
    st, clock = _state(("em", None)), _Clock()
    st.fail.update(MODEL_FAILURES["model-refused"])
    with wired(st) as w:
        with _traced(st, move_lock, clock):
            _after(st, LOCK_ALIVE, 2, lambda: _kill(st, move_lock, clock, 5))
            r = _change(w, _entity(ON_P["model-only"]))
        assert r.status_code == 502
        # the change's own lock, then the one the restore takes again
        assert move_lock.lock_queries() == [("shared", writer_move_lock.LOCK_CLASS, TENANT)] * 2


def test_a_healthy_change_takes_exactly_one_shared_lock_of_its_tenant(move_lock):
    st = _state(("em", None))
    with wired(st) as w, _traced(st, move_lock):
        assert _change(w, _entity(ON_P["model-only"])).status_code == 200
        assert move_lock.lock_queries() == [("shared", writer_move_lock.LOCK_CLASS, TENANT)]


def _calls(log, *kinds):
    return [e for e in log if e[0] in kinds]


def _no_lock_traffic(log):
    return [e for e in log if e != LOCK_ALIVE]


def test_profile_change_during_a_move_is_409_and_touches_nothing(move_lock):
    st = _state(("em", None))
    move_lock.plan = ["busy"]
    with wired(st) as w, _traced(st, move_lock):
        r = _change(w, _entity(ON_P["model-only"]))
    assert r.status_code == 409
    assert r.get_json() == {"error": "Sensors are being moved; try again in a few minutes",
                            "step": "writer_move"}
    assert st.iota_attrs == [] and w.orion.attr_posts == [] and w.orion.device_models == {}
    assert st.log == [("lock", "busy"), ("lock", "released")]  # no read, no write


def test_database_down_is_502_db(move_lock):
    st = _state(("em", None))
    move_lock.plan = ["unavailable"]
    with wired(st) as w, _traced(st, move_lock):
        r = _change(w, _entity(ON_P["model-only"]))
    assert r.status_code == 502 and r.get_json() == {"error": "Database error", "step": "db"}
    assert st.log == [] and st.iota_attrs == []


@pytest.mark.parametrize("urn", [URN, MACHINE], ids=["device", "machine"])
def test_a_healthy_change_is_lock_reads_agent_model_device_unlock(urn, move_lock):
    st = _state(("em", None), urn)
    with wired(st) as w, _traced(st, move_lock):
        r = _change(w, _entity(ON_P["model-only"] if urn == URN else {**MACHINE_ATTRS, "sensorType": _prop("p")}, urn))
        assert [a for _, a in st.iota_attrs] == [_attrs(("em", None), "p2", NEW, urn)]
    assert r.status_code == 200, r.get_json()
    log = st.log
    assert log[0] == ("lock", "taken") and log[-1] == ("lock", "released")
    rest = _no_lock_traffic(log)
    writes = [e for e in rest if e[0] in ("iota", "orion")]
    if urn == URN:
        assert writes == [("iota", "update", "S1"), ("orion", "upsert", MODEL2),
                          ("orion", "delete_attr", MODEL2, "controlledProperty"),
                          ("orion", "attrs", URN), ("orion", "delete_attr", URN, "controlledProperty")]
    else:
        assert writes == [("iota", "update", "S1"), ("orion", "patch")]
    first_write = rest.index(writes[0])
    assert [e for e in rest[1:first_write] if e[0] == "read"] == [
        ("read", "read_profile"), ("read", "_orion_devices"), ("read", "read_profile"),
        ("read", "device_dm_state")]
    assert [e for e in rest[first_write:] if e[0] == "read"] == [("read", "device_dm_state")]
    # a check right before the agent PUT, before the model and before the Device write
    for write in writes[:1] + [e for e in writes if e[1] in ("upsert", "attrs", "patch")]:
        assert log[log.index(write) - 1] == LOCK_ALIVE, write


def test_lock_is_held_until_the_undo_ends(move_lock):
    st = _state(("iota", "sdm"))
    st.fail.update(MODEL_FAILURES["model-refused"])
    with wired(st) as w, _traced(st, move_lock):
        r = _change(w, _entity(ON_P["model-only"]))
    assert r.status_code == 502 and r.get_json()["step"] == "device_model"
    updates = [i for i, e in enumerate(st.log) if e == ("iota", "update", "S1")]
    assert len(updates) == 2
    assert st.log[0] == ("lock", "taken")
    assert st.log.index(("lock", "released")) == len(st.log) - 1 > updates[1]
    assert st.log[updates[1] - 1] == LOCK_ALIVE  # the restore is checked as well


def test_lock_error_on_release_does_not_hide_the_result(move_lock, monkeypatch):
    monkeypatch.setattr(type(move_lock.connect()), "close_error", RuntimeError("boom"))
    st = _state(("em", None))
    with wired(st) as w:
        ok = _change(w, _entity(ON_P["model-only"]))
        st.fail.update(MODEL_FAILURES["model-refused"])
        bad = _change(w, _entity(ON_P["model-only"]))
    assert ok.status_code == 200, ok.get_json()
    assert bad.status_code == 502 and bad.get_json()["step"] == "device_model"


def test_entity_type_not_allowed_is_400(move_lock):
    st = _state(("em", None))
    st.profiles["p2"]["sdm_entity_type"] = "Sensor"  # the mapping is valid
    with wired(st) as w, _traced(st, move_lock):
        r = _change(w, _entity(ON_P["model-only"]))
    assert r.status_code == 400 and "not for a Device" in r.get_json()["error"]
    assert st.iota_attrs == [] and w.orion.attr_posts == [] and w.orion.device_models == {}
    assert st.log[-1] == ("lock", "released")


def test_mapping_value_error_is_400(move_lock):
    st = _state(("iota", "sdm"))
    st.profiles["p2"]["mapping"] = {"measurements": [
        {"type": "x", "sdmAttribute": "stemDiameter", "unit": "furlongs"}]}  # a Device profile
    with wired(st) as w, _traced(st, move_lock):
        r = _change(w, _entity(ON_P["model-only"]))
    assert r.status_code == 400, r.get_json()
    assert st.iota_attrs == [] and w.orion.attr_posts == [] and st.log[-1] == ("lock", "released")


# ---- a lost lock ---------------------------------------------------------------------------

def _kill(st, move_lock, clock, since=29, readable=True, mode="ok"):
    """The lock connection dies now; `since` seconds pass; the next connection does `mode`."""
    move_lock.conns[0].dead = True
    move_lock.plan[:] = [mode]
    clock.now += since
    if not readable:
        st.fail["dm_state"] = True


def _after(st, entry, nth, action):
    """Run `action` right after the `nth` time `entry` is logged."""
    seen = []

    def hook(n, item):
        if item == entry:
            seen.append(n)
            if len(seen) == nth:
                action()
    st.log.after = hook


def test_no_put_after_the_lock_is_lost_first_push(move_lock):
    st, clock = _state(("em", None)), _Clock()
    with wired(st) as w, _traced(st, move_lock, clock):
        _after(st, ("read", "device_dm_state"), 1, lambda: _kill(st, move_lock, clock, 29))
        r = _change(w, _entity(ON_P["model-only"]))
    assert r.status_code == 502 and r.get_json() == {"error": "Database error", "step": "db"}
    assert st.iota_attrs == [] and w.orion.attr_posts == [] and w.orion.device_models == {}


def test_no_put_after_the_lock_is_lost_later_push(move_lock):
    """The device moves to the IoT Agent during the first push: a second push is due, and the lock is gone."""
    st, clock = _state(("em", None)), _Clock()
    st.after_update = lambda n: _move_to(st, ("iota", "sdm")) if n == 1 else None
    with wired(st) as w, _traced(st, move_lock, clock):
        _after(st, ("read", "device_dm_state"), 2, lambda: _kill(st, move_lock, clock, 31))
        r = _change(w, _entity(ON_P["model-only"]))
    assert r.status_code == 502 and r.get_json()["step"] == "dm_state", r.get_json()
    # the first push, then the restore under the lock taken again; never the second push
    assert [a for _, a in st.iota_attrs] == [_attrs(("em", None), "p2", NEW),
                                             _attrs(("iota", "sdm"), "p", OLD)]
    for i, e in enumerate(st.log):
        if e == ("iota", "update", "S1"):
            assert st.log[i - 1] == LOCK_ALIVE


def _restore_runs_with_a_dying_lock(move_lock, since):
    """The model write fails so the restore runs; the lock dies when the restore first checks it."""
    st, clock = _state(("em", None)), _Clock()
    st.fail.update(MODEL_FAILURES["model-refused"])
    with wired(st) as w, _traced(st, move_lock, clock):
        # 1st check: before the first PUT; 2nd: before the model; then the restore looks
        _after(st, LOCK_ALIVE, 2, lambda: _kill(st, move_lock, clock, since, mode="unavailable"))
        r = _change(w, _entity(ON_P["model-only"]))
    return st, r


def test_no_put_after_the_lock_is_lost_restore(move_lock, caplog):
    with caplog.at_level(logging.CRITICAL):
        st, r = _restore_runs_with_a_dying_lock(move_lock, since=31)
    assert r.status_code == 502 and r.get_json()["step"] == "device_model"
    assert [a for _, a in st.iota_attrs] == [_attrs(("em", None), "p2", NEW)]  # no restore


def test_fallback_put_within_30_s_of_a_live_lock(move_lock, caplog):
    """The lock cannot be taken again and the state cannot be read, but it answered 29 s ago."""
    st, clock = _state(("em", None)), _Clock()
    st.fail.update(MODEL_FAILURES["model-refused"])
    with wired(st) as w, _traced(st, move_lock, clock), caplog.at_level(logging.CRITICAL):
        _after(st, LOCK_ALIVE, 2, lambda: _kill(st, move_lock, clock, 29, False, "unavailable"))
        r = _change(w, _entity(ON_P["model-only"]))
    assert r.status_code == 502 and r.get_json()["step"] == "device_model"
    assert [a for _, a in st.iota_attrs] == [_attrs(("em", None), "p2", NEW),
                                             _attrs(("em", None), "p", OLD)]
    assert any(x.getMessage().endswith("save the profile again once the database answers")
               for x in caplog.records if x.levelno == logging.CRITICAL)


def test_fallback_put_when_the_state_is_unreadable_under_a_live_lock(move_lock):
    st = _state(("em", None))
    st.fail.update(MODEL_FAILURES["model-refused"])
    st.after_update = lambda n: st.fail.update(dm_state=True)
    with wired(st) as w, _traced(st, move_lock):
        r = _change(w, _entity(ON_P["model-only"]))
    assert r.status_code == 502, r.get_json()
    i = st.log.index(("iota", "update", "S1"), st.log.index(("iota", "update", "S1")) + 1)
    assert st.log[i - 1] == LOCK_ALIVE


def test_fallback_refused_after_30_s_is_critical(move_lock, caplog):
    st, clock = _state(("em", None)), _Clock()
    st.fail.update(MODEL_FAILURES["model-refused"])
    with wired(st) as w, _traced(st, move_lock, clock), caplog.at_level(logging.CRITICAL):
        _after(st, LOCK_ALIVE, 2, lambda: _kill(st, move_lock, clock, 31, False, "unavailable"))
        r = _change(w, _entity(ON_P["model-only"]))
    assert r.status_code == 502 and r.get_json()["step"] == "device_model"
    assert [a for _, a in st.iota_attrs] == [_attrs(("em", None), "p2", NEW)]
    critical = [x.getMessage() for x in caplog.records if x.levelno == logging.CRITICAL]
    assert any(m.endswith("save the profile again once the database answers") for m in critical)


def test_restore_retakes_the_lock_and_rereads_the_state(move_lock):
    st, clock = _state(("em", None)), _Clock()
    st.fail.update(MODEL_FAILURES["model-refused"])

    def lose():
        _kill(st, move_lock, clock, 5)
        _move_to(st, ("iota", "legacy"))  # while nobody held the lock

    with wired(st) as w, _traced(st, move_lock, clock):
        _after(st, LOCK_ALIVE, 2, lose)
        r = _change(w, _entity(ON_P["model-only"]))
    assert r.status_code == 502 and r.get_json()["step"] == "device_model"
    assert [a for _, a in st.iota_attrs] == [_attrs(("em", None), "p2", NEW),
                                             _attrs(("iota", "legacy"), "p", OLD)]
    assert [e for e in st.log if e[0] == "lock" and e[1] in ("taken", "released")] == [
        ("lock", "taken"), ("lock", "taken"), ("lock", "released"), ("lock", "released")]
    i = [k for k, e in enumerate(st.log) if e == ("iota", "update", "S1")][1]
    assert st.log[i - 1] == LOCK_ALIVE


def test_restore_leaves_it_to_a_running_move_and_says_what_may_be_left(move_lock, caplog):
    """A move of ANOTHER device (or one interrupted before this device) holds the lock: nothing is
    put back, and the log says in plain words that the device may keep the new mappings."""
    st, clock = _state(("em", None)), _Clock()
    st.fail.update(MODEL_FAILURES["model-refused"])
    with wired(st) as w, _traced(st, move_lock, clock), caplog.at_level(logging.WARNING):
        _after(st, LOCK_ALIVE, 2, lambda: _kill(st, move_lock, clock, 5, mode="busy"))
        r = _change(w, _entity(ON_P["model-only"]))
    assert r.status_code == 502 and r.get_json()["step"] == "device_model"
    assert [a for _, a in st.iota_attrs] == [_attrs(("em", None), "p2", NEW)]    # no restore PUT
    assert clock.now == 1005.0                                                    # and no waiting
    critical = [x.getMessage() for x in caplog.records if x.levelno == logging.CRITICAL]
    assert any(m.startswith("INCONSISTENCY: a move holds the lock; the IoT Agent attributes of "
                            "device=S1 urn=" + URN + " were not put back (profile p -> p2); "
                            "if the move does not include this device it keeps the new profile's "
                            "mappings; save the profile again once the move ends")
               for m in critical), critical
    assert not [x for x in caplog.records if x.levelno == logging.WARNING and "left to it" in x.getMessage()]


def test_fallback_only_when_the_step_is_db(move_lock):
    """A state that is incomplete (not unreadable) is not guessed at."""
    st = _state(("em", None))
    st.fail.update(MODEL_FAILURES["model-refused"])
    real, calls = dataloggers.push_iota_attributes, []

    def push(*a):
        calls.append(a[3])
        if len(calls) == 2:
            raise dataloggers.ApiError(500, "incomplete", "dm_state")
        return real(*a)

    with wired(st) as w, _traced(st, move_lock), patch.object(dataloggers, "push_iota_attributes", push):
        r = _change(w, _entity(ON_P["model-only"]))
    assert calls == ["p2", "p"] and r.status_code == 502
    assert [a for _, a in st.iota_attrs] == [_attrs(("em", None), "p2", NEW)]


def test_the_last_state_of_a_failed_re_read_is_the_state_pushed_for(move_lock):
    st = _state(("em", None))
    with wired(st):
        with _readable(reads=1), pytest.raises(dataloggers.WriterStateError) as first:
            dataloggers.push_iota_attributes(TENANT, URN, "S1", "p2", NEW)  # read, build, push, re-read fails
        assert first.value.last_state == ("em", None)
        st.after_update = lambda n: _move_to(st, ("iota", "sdm")) if n == 2 else None  # 2nd update overall
        with _readable(reads=2), pytest.raises(dataloggers.WriterStateError) as later:
            dataloggers.push_iota_attributes(TENANT, URN, "S1", "p2", NEW)
        assert later.value.last_state == ("iota", "sdm")  # the second push was for the new state


def test_a_restore_without_a_last_state_pushes_nothing(move_lock, caplog):
    st = _state(("em", None))
    st.fail.update(MODEL_FAILURES["model-refused"])
    real, calls = dataloggers.push_iota_attributes, []

    def push(*a):
        calls.append(a[3])
        if len(calls) == 2:
            raise dataloggers.WriterStateError("unknown", None)
        return real(*a)

    with wired(st) as w, _traced(st, move_lock), patch.object(dataloggers, "push_iota_attributes", push), \
            caplog.at_level(logging.CRITICAL):
        r = _change(w, _entity(ON_P["model-only"]))
    assert r.status_code == 502 and calls == ["p2", "p"]
    assert [a for _, a in st.iota_attrs] == [_attrs(("em", None), "p2", NEW)]
    assert any("INCONSISTENCY" in x.getMessage() for x in caplog.records)


# ---- the lock dies at every point of a change ------------------------------------------------

COMBINATIONS = [(state, mode, readable, since)
                for state in (("em", None), ("iota", "sdm"))
                for mode in ("ok", "busy", "unavailable")
                for readable in (True, False)
                for since in (29, 31)]


def _scenario(state, move_lock, kill_at=None, mode="ok", readable=True, since=29):
    st, clock = _state(state), _Clock()
    move_lock.plan.clear()
    with wired(st) as w, _traced(st, move_lock, clock):
        if kill_at is not None:
            def hook(n, item):
                if n == kill_at:
                    _kill(st, move_lock, clock, since, readable, mode)
            st.log.after = hook
        r = _change(w, _entity(ON_P["model-only"]))
    return st, r


@pytest.mark.parametrize("state,mode,readable,since", COMBINATIONS)
def test_a_lock_lost_at_any_point_never_puts_blind_and_never_answers_200_unfinished(
        state, mode, readable, since, move_lock):
    healthy, ok = _scenario(state, move_lock)
    assert ok.status_code == 200
    done = [e for e in healthy.log if e[0] in ("iota", "orion")]
    last_check = max(i for i, e in enumerate(healthy.log, 1) if e == LOCK_ALIVE)
    old = _attrs(state, "p", OLD)
    for k in range(1, len(healthy.log)):
        move_lock.conns.clear()
        st, r = _scenario(state, move_lock, k, mode, readable, since)
        where = f"killed after entry {k}: {healthy.log[k - 1]}"
        puts = [i for i, e in enumerate(st.log) if e == ("iota", "update", "S1")]
        # no PUT without a check just before it, except the blind one within 30 s
        for i in puts:
            if st.log[i - 1] != LOCK_ALIVE:
                assert st.log[i - 1] == LOCK_DEAD and since == 29, where
        # a visible error whenever the change did not finish; 200 only if it did
        if r.status_code == 200:
            assert k >= last_check and [e for e in st.log if e[0] in ("iota", "orion")] == done, where
        else:
            assert k < last_check and r.status_code == 502 and r.get_json().get("step"), where
        held = [e[1] for e in st.log if e[0] == "lock" and e[1] in ("taken", "busy", "released")]
        assert held.count("released") == held.count("taken") + held.count("busy"), where
        # the previous mappings come back whenever the lock allows it
        if r.status_code != 200 and len(puts) >= 1:
            allowed = mode == "ok" or (mode == "unavailable" and since == 29)
            if allowed:
                assert len(puts) >= 2 and st.iota_attrs[-1][1] == old, where
            else:
                assert len(puts) == 1, where
        if r.status_code != 200 and not puts:
            assert r.get_json()["step"] in ("db", "dm_state", "iota_device"), where


# ---- the cutover command's fresh push ---------------------------------------------------------

def test_the_fresh_push_of_the_cutover_checks_its_lock_before_every_put():
    """A second push is due (the device moved to the agent during the first) and the lock is gone."""
    st = _state(("em", None))
    st.after_update = lambda n: _move_to(st, ("iota", "sdm")) if n == 1 else None

    class Lock:
        def alive(self):
            return st.iota_updates == 0

        def __exit__(self, *a):
            return False

    row = dm_writer_cutover.Row(URN, "S1", ("em", None))
    holder = dm_writer_cutover._LockHolder(TENANT, Lock())
    with wired(st) as w, patch.object(dm_writer_cutover, "_exclusive_lock",
                                      side_effect=writer_move_lock.LockUnavailable("down")):
        w.orion.entities[URN] = _entity(ON_P["model-only"])
        with pytest.raises(dataloggers.WriterStateError) as err:
            dm_writer_cutover._push_fresh(holder, TENANT, row)
    assert isinstance(err.value.__cause__, dm_writer_cutover._LockLost)
    assert st.iota_updates == 1  # the second PUT was never sent
