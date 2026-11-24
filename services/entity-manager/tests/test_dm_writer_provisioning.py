"""Per-device writer state: every provisioning path goes through one helper."""
import ast
import json
import pathlib

import pytest

from tests.telemetry_fakes import (AUTH, PROFILE, TENANT, FakeState, client, post_json,  # noqa: I001
                                   seed_datalogger, seed_device, wired)

import dataloggers  # after telemetry_fakes: it stubs the modules dataloggers imports

DL = "11111111-1111-1111-1111-111111111111"
PROFILE_2 = {"mapping": {"measurements": [
    {"type": "x", "sdmAttribute": "soilMoisture", "unit": "%"}]}}
BASE = {"external_id": "S1", "name": "Sensor 1", "profile": "p",
        "location": {"lat": 42.0, "lon": -2.0},
        "controlledAsset": ["urn:ngsi-ld:AgriParcel:p1"], "datalogger_id": DL}


def _state(writer="em", shape=None):
    st = FakeState()
    seed_datalogger(st, dl_id=DL)
    st.profiles["p"] = dict(PROFILE)
    st.profiles["p2"] = dict(PROFILE_2)
    urn = seed_device(st, DL, "S1", dm_writer=writer, dm_shape=shape)
    return st, urn


def _put_profile(urn, code="p2"):
    return client().put(f"/api/sensors/devices/{urn}/profile", headers=AUTH,
                        content_type="application/json", data=json.dumps({"profile": code}))


def _orion_device(w, urn, code="p"):
    w.orion.entities[urn] = {"id": urn, "type": "Device",
                             "profileCode": {"type": "Property", "value": code}}


def _dm(attrs):
    return [a for a in attrs if a.get("entity_type") == "DeviceMeasurement"]


def _device_type_literal(attrs):
    found = {a["expression"].rsplit("? ", 1)[1].removesuffix(" : null")
             for a in _dm(attrs) if a["name"] == "deviceType"}
    assert len(found) == 1
    return found.pop()


# -- device_dm_state ---------------------------------------------------------------------

def test_state_defaults_to_em_without_a_row():
    st = FakeState()
    with wired(st):
        assert dataloggers.device_dm_state(TENANT, "urn:ngsi-ld:Device:tenant-a:none") == ("em", None)


def test_state_reads_the_row():
    st, urn = _state("iota", "sdm")
    with wired(st):
        assert dataloggers.device_dm_state(TENANT, urn) == ("iota", "sdm")


# -- iota_attributes_for -----------------------------------------------------------------

def test_em_device_gets_exactly_todays_attributes():
    st, urn = _state()
    ms = PROFILE["mapping"]["measurements"]
    with wired(st) as w:
        got = dataloggers.iota_attributes_for(TENANT, urn, "S1", "p", ms)
    assert got == w.iota.build_iota_attributes(ms)


@pytest.mark.parametrize("shape", ["legacy", "sdm"])
def test_iota_device_gets_dm_then_device_mappings(shape):
    st, urn = _state("iota", shape)
    ms = PROFILE["mapping"]["measurements"]
    with wired(st):
        got = dataloggers.iota_attributes_for(TENANT, urn, "S1", "p", ms)
    kinds = [a.get("entity_type") for a in got]
    assert kinds[0] == "DeviceMeasurement" and kinds[-1] is None
    assert kinds.count(None) == 3  # two channels + TimeInstant
    assert f"urn:ngsi-ld:DeviceMeasurement:{TENANT}:S1:" in got[0]["entity_name"]
    assert (any(a["name"] == "name" for a in _dm(got))) == (shape == "sdm")


@pytest.mark.parametrize("shape", [None, "other"])
def test_iota_device_without_a_known_shape_fails_loudly(shape):
    st, urn = _state("iota", shape)
    with wired(st):
        with pytest.raises(dataloggers.ApiError) as e:
            dataloggers.iota_attributes_for(TENANT, urn, "S1", "p", PROFILE["mapping"]["measurements"])
    assert e.value.status == 500


def test_state_read_failure_is_an_error_not_em():
    st, urn = _state("iota", "legacy")
    st.fail["dm_state"] = True
    with wired(st):
        with pytest.raises(dataloggers.ApiError) as e:
            dataloggers.iota_attributes_for(TENANT, urn, "S1", "p", PROFILE["mapping"]["measurements"])
    assert e.value.status == 502 and e.value.step == "db"


# -- the paths ---------------------------------------------------------------------------

def test_profile_change_on_an_iota_device_keeps_the_dm_mappings_with_the_new_code():
    st, urn = _state("iota", "legacy")
    with wired(st) as w:
        _orion_device(w, urn)
        r = _put_profile(urn)
    assert r.status_code == 200, r.get_json()
    ((dev, attrs),) = st.iota_attrs
    assert dev == "S1" and _dm(attrs) and attrs[-1]["object_id"] == "TimeInstant"
    assert _device_type_literal(attrs) == "'p2'"


def test_undo_after_an_orion_failure_restores_dm_mappings_with_the_old_code():
    st, urn = _state("iota", "legacy")
    st.fail["orion_patch"] = True
    with wired(st) as w:
        _orion_device(w, urn)
        r = _put_profile(urn)
    assert r.status_code == 502 and r.get_json()["step"] == "orion_profile"
    (_, new), (_, undo) = st.iota_attrs
    assert _device_type_literal(new) == "'p2'" and _device_type_literal(undo) == "'p'"
    assert _dm(undo)


def test_profile_change_on_an_em_device_is_unchanged():
    st, urn = _state()
    with wired(st) as w:
        _orion_device(w, urn)
        r = _put_profile(urn)
    assert r.status_code == 200
    ((_, attrs),) = st.iota_attrs
    assert not _dm(attrs)


def test_profile_change_invalid_mapping_is_400_before_anything_is_touched():
    st, urn = _state("iota", "legacy")
    st.profiles["p2"] = {"mapping": {"measurements": [
        {"type": "numValue", "sdmAttribute": "soilMoisture", "unit": "%"}]}}
    with wired(st) as w:
        _orion_device(w, urn)
        r = _put_profile(urn)
    assert r.status_code == 400
    assert not any(e[0] == "iota" or e[:2] == ("orion", "patch") for e in st.log)


def test_profile_change_state_read_failure_is_502_and_touches_nothing():
    st, urn = _state("iota", "legacy")
    st.fail["dm_state"] = True
    with wired(st) as w:
        _orion_device(w, urn)
        r = _put_profile(urn)
    assert r.status_code == 502
    assert not any(e[0] == "iota" or e[:2] == ("orion", "patch") for e in st.log)


def test_profile_change_iota_device_without_shape_is_500_and_touches_nothing():
    st, urn = _state("iota", None)
    with wired(st) as w:
        _orion_device(w, urn)
        r = _put_profile(urn)
    assert r.status_code == 500
    assert not any(e[0] == "iota" or e[:2] == ("orion", "patch") for e in st.log)


def test_reprovision_pushes_dm_and_device_for_iota_devices_only():
    st, u1 = _state("iota", "sdm")
    u2 = seed_device(st, DL, "S2")
    body = {"code": "p", "name": "P", "sdm_entity_type": "Device",
            "mapping": {"measurements": PROFILE["mapping"]["measurements"]}}
    st.profiles["p"]["tenant_id"] = TENANT
    with wired(st) as w:
        for u in (u1, u2):
            _orion_device(w, u)
        r = client().put("/api/sensors/profiles/p", headers=AUTH,
                         content_type="application/json", data=json.dumps(body))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["reprovisioned"] == [u1, u2]
    by_dev = dict(st.iota_attrs)
    assert _dm(by_dev["S1"]) and not _dm(by_dev["S2"])


def test_register_provisions_an_em_device():
    st = FakeState()
    seed_datalogger(st, dl_id=DL)
    st.profiles["p"] = dict(PROFILE)
    with wired(st):
        r = post_json(client(), "/api/sensors/register", BASE)
    assert r.status_code == 201, r.get_json()
    ((_, attrs),) = st.iota_attrs
    assert not _dm(attrs)
    assert st.devices[0]["dm_writer"] == "em"


# -- write-time validation ---------------------------------------------------------------

@pytest.mark.parametrize("m", [
    {"type": "numValue", "sdmAttribute": "soilMoisture", "unit": "%"},
    {"type": "r-3", "sdmAttribute": "soilMoisture", "unit": "%"},
    {"type": "x", "sdmAttribute": "soilMoisture", "valueType": "Text", "expression": "x * 2"},
])
def test_profile_save_rejects_unsupported_mappings(m):
    st = FakeState()
    body = {"code": "bad", "name": "Bad", "sdm_entity_type": "Device", "mapping": {"measurements": [m]}}
    with wired(st):
        r = post_json(client(), "/api/sensors/profiles", body)
    assert r.status_code == 400, r.get_json()
    assert r.get_json()["error"].isascii() and st.profiles == {}


def test_profile_update_rejects_before_touching_devices():
    st, urn = _state()
    st.profiles["p"]["tenant_id"] = TENANT
    body = {"code": "p", "name": "P", "sdm_entity_type": "Device", "mapping": {"measurements": [
        {"type": "TimeInstant", "sdmAttribute": "soilMoisture", "unit": "%"}]}}
    with wired(st) as w:
        _orion_device(w, urn)
        r = client().put("/api/sensors/profiles/p", headers=AUTH,
                         content_type="application/json", data=json.dumps(body))
    assert r.status_code == 400
    assert not any(e[0] == "iota" for e in st.log)


def test_register_rejects_a_stored_profile_with_a_reserved_key_before_writing():
    st = FakeState()
    seed_datalogger(st, dl_id=DL)
    st.profiles["p"] = {"mapping": {"measurements": [
        {"type": "numValue", "sdmAttribute": "soilMoisture", "unit": "%"}]}}
    with wired(st):
        r = post_json(client(), "/api/sensors/register", BASE)
    assert r.status_code == 400
    assert not any(e[0] in ("orion", "iota", "db") for e in st.log)


# -- the one place ------------------------------------------------------------------------

# -- the writer changes while attributes are being pushed -------------------------------

def _flip(st, to_writer="iota", on=(1,)):
    def cb(n):
        if n in on:
            for r in st.devices:
                r["dm_writer"], r["dm_shape"] = to_writer, ("legacy" if to_writer == "iota" else None)
    st.after_update = cb


def _toggle(st):
    def cb(n):
        for r in st.devices:
            on = r["dm_writer"] == "iota"
            r["dm_writer"], r["dm_shape"] = ("em", None) if on else ("iota", "legacy")
    st.after_update = cb


def test_change_repushes_with_dm_mappings_when_the_device_moves_to_iota_meanwhile():
    st, urn = _state()
    _flip(st, on=(1,))
    with wired(st) as w:
        _orion_device(w, urn)
        r = _put_profile(urn)
    assert r.status_code == 200, r.get_json()
    (_, first), (_, second) = st.iota_attrs
    assert not _dm(first) and _dm(second)


def test_undo_repushes_with_dm_mappings_when_the_device_moves_to_iota_meanwhile():
    st, urn = _state()
    st.fail["orion_patch"] = True
    _flip(st, on=(2,))  # the undo push is the second one
    with wired(st) as w:
        _orion_device(w, urn)
        r = _put_profile(urn)
    assert r.status_code == 502 and r.get_json()["step"] == "orion_profile"
    (_, new), (_, undo1), (_, undo2) = st.iota_attrs
    assert not _dm(undo1) and _dm(undo2) and _device_type_literal(undo2) == "'p'"


def test_reprovision_repushes_when_the_device_moves_to_iota_meanwhile():
    st, u1 = _state()
    st.profiles["p"]["tenant_id"] = TENANT
    _flip(st, on=(1,))
    body = {"code": "p", "name": "P", "sdm_entity_type": "Device",
            "mapping": {"measurements": PROFILE["mapping"]["measurements"]}}
    with wired(st) as w:
        _orion_device(w, u1)
        r = client().put("/api/sensors/profiles/p", headers=AUTH,
                         content_type="application/json", data=json.dumps(body))
    assert r.get_json()["reprovisioned"] == [u1]
    assert not _dm(st.iota_attrs[0][1]) and _dm(st.iota_attrs[1][1])


def test_change_with_a_state_that_never_settles_is_502_and_restores():
    st, urn = _state()
    _toggle(st)
    with wired(st) as w:
        _orion_device(w, urn)
        r = _put_profile(urn)
    assert r.status_code == 502 and r.get_json()["step"] == "dm_state"
    assert ("orion", "patch") not in st.log
    assert len(st.iota_attrs) > 3  # 3 pushes, then the restore pushes


def test_change_with_a_failing_re_read_is_502_and_restores():
    st, urn = _state()
    st.after_update = lambda n: st.fail.__setitem__("dm_state", True) if n == 1 else None
    with wired(st) as w:
        _orion_device(w, urn)
        r = _put_profile(urn)
    assert r.status_code == 502 and r.get_json()["step"] == "dm_state"
    assert ("orion", "patch") not in st.log


@pytest.mark.parametrize("mode", ["toggle", "reread"])
def test_reprovision_lists_the_device_as_failed(mode):
    st, u1 = _state()
    st.profiles["p"]["tenant_id"] = TENANT
    if mode == "toggle":
        _toggle(st)
    else:
        st.after_update = lambda n: st.fail.__setitem__("dm_state", True)
    body = {"code": "p", "name": "P", "sdm_entity_type": "Device",
            "mapping": {"measurements": PROFILE["mapping"]["measurements"]}}
    with wired(st) as w:
        _orion_device(w, u1)
        r = client().put("/api/sensors/profiles/p", headers=AUTH,
                         content_type="application/json", data=json.dumps(body))
    assert r.status_code == 200 and r.get_json()["failed"] == [u1]


def test_undo_failure_is_logged_with_both_profile_codes(caplog):
    st, urn = _state()
    st.fail["orion_patch"] = True
    st.after_update = lambda n: st.fail.__setitem__("iota_update", True) if n == 1 else None
    with wired(st) as w, caplog.at_level("CRITICAL"):
        _orion_device(w, urn)
        r = _put_profile(urn)
    assert r.status_code == 502
    msgs = [x.getMessage() for x in caplog.records if "could not restore" in x.getMessage()]
    assert msgs and "S1" in msgs[0] and urn in msgs[0] and "old_profile=p " in msgs[0] \
        and "new_profile=p2" in msgs[0]


def test_provision_with_an_unsupported_mapping_is_400():
    st, urn = _state("iota", "legacy")
    bad = [{"type": "numValue", "sdmAttribute": "soilMoisture", "unit": "%"}]
    with wired(st):
        with pytest.raises(dataloggers.ApiError) as e:
            dataloggers.provision_iota_device(TENANT, "k", "S1", urn, "Device", bad, "p")
    assert e.value.status == 400 and not any(x[0] == "iota" for x in st.log)


def _late_second_push_fails(st):
    def cb(n):
        if n == 1:
            for r in st.devices:
                r["dm_writer"], r["dm_shape"] = "iota", "legacy"
            st.fail["iota_update"] = True
    st.after_update = cb


_BAD_P2 = {"mapping": {"measurements": [
    {"type": "numValue", "sdmAttribute": "soilMoisture", "unit": "%"}]}}  # fine for em only


def test_change_second_push_failure_restores_and_is_502_dm_state():
    st, urn = _state()
    _late_second_push_fails(st)
    with wired(st) as w:
        _orion_device(w, urn)
        r = _put_profile(urn)
    assert r.status_code == 502 and r.get_json()["step"] == "dm_state"
    assert ("orion", "patch") not in st.log
    assert len(st.iota_attrs) == 3  # first push, failed second push, restore attempt


def test_change_rebuild_error_after_the_state_moved_restores_and_is_not_a_400():
    st, urn = _state()
    st.profiles["p2"] = dict(_BAD_P2)
    _flip(st, on=(1,))
    with wired(st) as w:
        _orion_device(w, urn)
        r = _put_profile(urn)
    assert r.status_code == 502 and r.get_json()["step"] == "dm_state"
    assert ("orion", "patch") not in st.log
    (_, first), (_, restore) = st.iota_attrs
    assert not _dm(first) and _dm(restore) and _device_type_literal(restore) == "'p'"


@pytest.mark.parametrize("mode", ["push", "rebuild"])
def test_reprovision_late_failure_lists_the_device_as_failed(mode):
    st, u1 = _state()
    if mode == "push":
        _late_second_push_fails(st)
    else:
        _flip(st, on=(1,))
    # Called directly: profile save would reject the unsupported mapping with a 400.
    ms = PROFILE["mapping"]["measurements"] if mode == "push" else _BAD_P2["mapping"]["measurements"]
    with wired(st) as w:
        _orion_device(w, u1)
        out = dataloggers.reprovision_profile_devices(TENANT, "p", ms)
    assert out["failed"] == [u1] and out["reprovisioned"] == []


_GUARDED = ("build_iota_attributes", "device_attributes", "provision_device",
            "update_device_attributes")


class _Calls(ast.NodeVisitor):
    def __init__(self):
        self.scope, self.found = [], []

    def _fn(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _fn

    def visit_ImportFrom(self, node):
        for a in node.names:
            if a.name in _GUARDED:
                self.found.append((a.name, "import"))

    visit_Import = visit_ImportFrom

    def visit_Call(self, node):
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name in _GUARDED:
            self.found.append((name, self.scope[0] if self.scope else None))
        self.generic_visit(node)


def test_attribute_builders_and_pushes_are_called_only_in_the_helpers():
    root = pathlib.Path(dataloggers.__file__).parent
    seen = []
    for path in sorted(root.rglob("*.py")):
        if "tests" in path.parts:
            continue
        v = _Calls()
        v.visit(ast.parse(path.read_text()))
        seen += [(path.name, *f) for f in v.found]
    d = "dataloggers.py"
    assert sorted(seen) == [
        (d, "build_iota_attributes", "_attributes_for_state"),
        (d, "device_attributes", "_attributes_for_state"),
        (d, "device_attributes", "import"),
        (d, "device_attributes", "validate_profile_mappings"),
        (d, "provision_device", "provision_iota_device"),
        (d, "update_device_attributes", "push_iota_attributes"),
        (d, "update_device_attributes", "push_iota_attributes"),
        # the cutover command: first push of a move to iota, and the restore (other half of
        # push_iota_attributes, see its module docstring)
        ("dm_writer_cutover.py", "update_device_attributes", "_put"),
    ], seen
