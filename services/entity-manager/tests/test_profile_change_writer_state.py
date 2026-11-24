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
    assert r.status_code == 502 and r.get_json()["step"] == "orion_profile", r.get_json()
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
