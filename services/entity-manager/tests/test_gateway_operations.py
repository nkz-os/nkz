"""Task 3: user operations start the gateway projection after their commit, in the background;
deleting a datalogger removes its gateway; sensor routes refuse a gateway URN."""
import json
import logging
import os
import threading

import pytest

from tests.telemetry_fakes import _resp  # noqa: E402
from tests.telemetry_fakes import (AUTH, PARCEL, PROFILE, TENANT, FakeState, client,
                                   seed_datalogger, seed_device, wired)

import dataloggers  # noqa: E402
import gateways  # noqa: E402

DL = "11111111-1111-1111-1111-111111111111"
DL2 = "22222222-2222-2222-2222-222222222222"
ROLE = "dl_aaaaaaaaaaaaaaaa"
ROLE2 = "dl_bbbbbbbbbbbbbbbb"
GW = "gw-0123456789ab"
GW_URN = f"urn:ngsi-ld:Device:{TENANT}:{GW}"
S1 = f"urn:ngsi-ld:Device:{TENANT}:S1"
RECORDING = os.path.join(os.path.dirname(__file__), "recordings", "gateway_operations_base.json")
BASE = {"external_id": "S1", "name": "Sensor 1", "profile": "p",
        "location": {"lat": 42.0, "lon": -2.0}, "controlledAsset": [PARCEL], "datalogger_id": DL}


@pytest.fixture(autouse=True)
def flag(monkeypatch):
    monkeypatch.setenv(gateways.FLAG, TENANT)
    yield
    gateways.join_background(10)


def put(c, path, body):
    return c.put(path, headers=AUTH, content_type="application/json", data=json.dumps(body))


def post(c, path, body=None):
    return c.post(path, headers=AUTH, content_type="application/json", data=json.dumps(body or {}))


def base_state(*, second=False, sensors=("S1",), status="active"):
    st = FakeState()
    seed_datalogger(st, dl_id=DL, username=ROLE, status=status)
    if second:
        seed_datalogger(st, dl_id=DL2, username=ROLE2)
    for s in sensors:
        seed_device(st, DL, s)
    st.profiles["p"] = dict(PROFILE)
    return st


# ── the operations: (state, request, committed-state check, expected gateways) ────────────

def _create(st, c):
    return post(c, "/api/sensors/dataloggers", {"name": "L", "kind": "datak"})


def _enable(st, c):
    return post(c, f"/api/sensors/dataloggers/{DL}/enable")


def _revoke(st, c):
    return post(c, f"/api/sensors/dataloggers/{DL}/revoke")


def _rotate(st, c):
    return post(c, f"/api/sensors/dataloggers/{DL}/rotate")


def _register(st, c):
    return post(c, "/api/sensors/register", BASE)


def _move(st, c):
    return put(c, f"/api/sensors/devices/{S1}/datalogger", {"datalogger_id": DL2})


def _delete_sensor(st, c):
    return c.delete(f"/api/sensors/devices/{S1}", headers=AUTH)


# name: (state factory, request, failure injector, committed check on a snapshot, gateways)
OPS = {
    "create": (lambda: FakeState(), _create, lambda st: st.fail.update(commit=True),
               lambda s: len(s["dataloggers"]) == 1, "new"),
    "enable": (lambda: base_state(status="revoked"), _enable, lambda st: st.fail.update(commit=True),
               lambda s: s["dataloggers"][DL]["status"] == "active", [DL]),
    "revoke": (lambda: base_state(), _revoke, lambda st: st.fail.update(commit=True),
               lambda s: s["dataloggers"][DL]["status"] == "revoked", [DL]),
    "rotate": (lambda: base_state(), _rotate,
               lambda st: (st.missing_clients.add(ROLE), st.fail.update(create_account=True)),
               lambda s: s["dataloggers"][DL]["credentials_rotated_at"] == "now", [DL]),
    "register": (lambda: base_state(sensors=()), _register, lambda st: st.fail.update(provision=True),
                 lambda s: [d["device_urn"] for d in s["devices"]] == [S1], [DL]),
    "move": (lambda: base_state(second=True), _move, lambda st: st.fail.update(set_role_topics=True),
             lambda s: s["devices"][0]["datalogger_id"] == DL2, [DL2, DL]),
    "delete_sensor": (lambda: base_state(), _delete_sensor, lambda st: st.fail.update(set_role_topics=True),
                      lambda s: s["devices"] == [], [DL]),
}


class Recorder:
    """Stands in for `gateways.project_gateway`: records what the world looked like when called."""

    def __init__(self, st):
        import copy
        self._copy, self.st = copy, st
        self.calls, self.threads, self.snaps = [], [], []
        self.entered, self.release = threading.Event(), None
        self.result = "done"

    def __call__(self, tenant, dl_id):
        self.calls.append((tenant, dl_id))
        self.threads.append(threading.current_thread())
        self.snaps.append({"dataloggers": self._copy.deepcopy(self.st.dataloggers),
                           "devices": self._copy.deepcopy(self.st.devices),
                           "log": list(self.st.log)})
        self.entered.set()
        if self.release is not None:
            assert self.release.wait(10), "projection never released"
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def rec(monkeypatch):
    holder = {}

    def make(st):
        holder["r"] = Recorder(st)
        monkeypatch.setattr(gateways, "project_gateway", holder["r"])
        return holder["r"]
    return make


def run_op(name, *, fail=False, rec_factory=None, before_join=None):
    factory, request, injector, _check, _ids = OPS[name]
    st = factory()
    r = rec_factory(st) if rec_factory else None
    if fail:
        injector(st)
    with wired(st):
        resp = request(st, client())
        if before_join:
            before_join(st, r, resp)
        gateways.join_background(10)
    return st, r, resp


def expected_ids(name, resp):
    ids = OPS[name][4]
    return [resp.get_json()["id"]] if ids == "new" else ids


@pytest.mark.parametrize("name", OPS)
def test_each_operation_projects_its_gateways_after_the_commit(name, rec):
    st, r, resp = run_op(name, rec_factory=rec)
    assert resp.status_code in (200, 201, 204), resp.get_json()
    assert [c[1] for c in r.calls] == expected_ids(name, resp)
    assert all(c[0] == TENANT for c in r.calls)
    for snap in r.snaps:  # what was committed when the projection started
        assert OPS[name][3](snap)
        assert ("sql", "commit") in snap["log"]


@pytest.mark.parametrize("name", OPS)
def test_a_failed_operation_starts_no_projection(name, rec):
    st, r, resp = run_op(name, fail=True, rec_factory=rec)
    assert resp.status_code >= 400, resp.get_json()
    assert r.calls == []


def test_a_sensor_deleted_after_its_commit_is_projected_even_when_the_agent_refuses(rec):
    # the link row is gone and committed: the gateway must lose that sensor from controlledAsset
    st = base_state()
    r = rec(st)
    st.fail["iota_delete"] = True
    with wired(st):
        resp = _delete_sensor(st, client())
        gateways.join_background(10)
    assert resp.status_code == 502
    assert [c[1] for c in r.calls] == [DL] and r.snaps[0]["devices"] == []


@pytest.mark.parametrize("name", OPS)
def test_with_the_flag_off_nothing_is_started(name, rec, monkeypatch):
    monkeypatch.delenv(gateways.FLAG)
    started = []
    real = threading.Thread.start
    monkeypatch.setattr(threading.Thread, "start", lambda self: (started.append(self), real(self))[1])
    st, r, resp = run_op(name, rec_factory=rec)
    assert resp.status_code in (200, 201, 204)
    assert r.calls == [] and started == []


def test_a_tenant_outside_the_flag_starts_nothing(rec, monkeypatch):
    monkeypatch.setenv(gateways.FLAG, "someone-else")
    st, r, resp = run_op("revoke", rec_factory=rec)
    assert resp.status_code == 200 and r.calls == []


# ── the response does not change ──────────────────────────────────────────────

_MASKED = {"password", "username", "id"}


def _mask(value):
    if isinstance(value, dict):
        return {k: ("<x>" if k in _MASKED else _mask(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return value


def _recording_cases():
    return [(n, f) for n in OPS for f in (False, True)]


@pytest.mark.parametrize("name,fail", _recording_cases())
@pytest.mark.parametrize("flag_on", [False, True])
def test_the_response_is_the_one_recorded_on_base(name, fail, flag_on, rec, monkeypatch):
    if not flag_on:
        monkeypatch.delenv(gateways.FLAG)
    st, r, resp = run_op(name, fail=fail, rec_factory=rec)
    got = {"status": resp.status_code,
           "body": resp.get_data(as_text=True) if resp.status_code == 204 else _mask(resp.get_json()),
           "type": resp.content_type}
    key = f"{name}:{'fail' if fail else 'ok'}"
    if os.environ.get("RECORD_BASE"):
        data = json.load(open(RECORDING)) if os.path.exists(RECORDING) else {}
        data[key] = got
        json.dump(data, open(RECORDING, "w"), indent=1, sort_keys=True)
        return
    assert got == json.load(open(RECORDING))[key]


# ── background, at most one ──────────────────────────────────────────────────

def test_the_request_returns_while_a_projection_is_still_blocked(rec):
    st = base_state()
    r = rec(st)
    r.release = threading.Event()
    with wired(st):
        resp = _revoke(st, client())
        assert resp.status_code == 200          # answered although the projection has not finished
        assert r.entered.wait(5)
        assert r.threads[0] is not threading.current_thread()
        assert r.threads[0].daemon
        r.release.set()
        gateways.join_background(10)


def test_at_most_one_projection_per_process_and_the_second_is_logged(rec, caplog):
    st = base_state(second=True)
    r = rec(st)
    r.release = threading.Event()
    with wired(st), caplog.at_level(logging.INFO):
        c = client()
        assert post(c, f"/api/sensors/dataloggers/{DL}/rotate").status_code == 200
        assert r.entered.wait(5)
        assert post(c, f"/api/sensors/dataloggers/{DL2}/rotate").status_code == 200  # skipped
        r.release.set()
        gateways.join_background(10)
        assert [c_[1] for c_ in r.calls] == [DL]
        assert "skipped" in caplog.text
        # the slot is free again
        assert post(c, f"/api/sensors/dataloggers/{DL2}/rotate").status_code == 200
        gateways.join_background(10)
    assert [c_[1] for c_ in r.calls] == [DL, DL2]


@pytest.mark.parametrize("result", ["failed:orion_entity", RuntimeError("boom")])
def test_a_failed_projection_is_a_warning_and_frees_the_slot(rec, caplog, result):
    st = base_state()
    r = rec(st)
    r.result = result
    with wired(st), caplog.at_level(logging.INFO):
        assert _revoke(st, client()).status_code == 200
        gateways.join_background(10)
        assert any(x.levelno == logging.WARNING for x in caplog.records)
        assert any(x.levelno == logging.WARNING and "projection" in x.getMessage() for x in caplog.records)
        r.result = "done"
        assert _enable(st, client()).status_code == 200
        gateways.join_background(10)
    assert len(r.calls) == 2


def test_a_done_or_off_projection_is_not_a_warning(rec, caplog):
    st = base_state()
    rec(st)
    with wired(st), caplog.at_level(logging.INFO):
        _revoke(st, client())
        gateways.join_background(10)
    assert not [x for x in caplog.records if x.levelno >= logging.WARNING]


def test_start_projection_never_raises_when_a_thread_cannot_start(monkeypatch, caplog):
    def boom(self):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(threading.Thread, "start", boom)
    assert gateways.start_projection(TENANT, [DL]) is False
    # the slot was given back
    monkeypatch.undo()
    monkeypatch.setenv(gateways.FLAG, TENANT)
    monkeypatch.setattr(gateways, "project_gateway", lambda t, d: "done")
    assert gateways.start_projection(TENANT, [DL]) is True
    gateways.join_background(10)


# ── deletion of a datalogger takes its gateway with it ────────────────────────

DMS = [f"urn:ngsi-ld:DeviceMeasurement:{TENANT}:{GW}:{n}" for n in ("uptime", "batteryLevel", "rssi")]


def gateway_state():
    st = base_state(sensors=())
    st.dataloggers[DL].update(gateway_external_id=GW, gateway_provisioned_at="now", created_at=None)
    st.iota_devices[GW] = {"device_id": GW, "entity_name": GW_URN}
    return st


def delete_dl(st, *, setup=None):
    with wired(st) as w:
        w.orion.entities[GW_URN] = {"id": GW_URN}
        for d in DMS:
            w.orion.entities[d] = {"id": d}
        if setup:
            setup(w)
        resp = client().delete(f"/api/sensors/dataloggers/{DL}", headers=AUTH)
        gateways.join_background(10)
    return w, resp


def _pos(log, entry):
    return next(i for i, e in enumerate(log) if e[:len(entry)] == entry)


def test_deleting_a_datalogger_removes_agent_device_then_measurements_then_entity_under_the_lock():
    st = gateway_state()
    w, resp = delete_dl(st)
    assert resp.status_code == 204 and DL not in st.dataloggers
    log = st.log
    commit = max(i for i, e in enumerate(log) if e == ("sql", "commit") and i < _pos(log, ("iota", "delete")))
    lock = _pos(log, ("sql", "lock", f"gateway:{DL}"))
    order = [lock, _pos(log, ("iota", "delete", GW))] + [_pos(log, ("orion", "delete", d)) for d in DMS] \
        + [_pos(log, ("orion", "delete", GW_URN))]
    assert order == sorted(order) and commit < lock
    assert not any(e[:3] in {("orion", "delete", d) for d in DMS + [GW_URN]} for e in log[:lock])
    assert GW_URN not in w.orion.entities and not any(d in w.orion.entities for d in DMS)
    assert GW not in st.iota_devices


def test_the_delete_response_is_the_same_and_the_job_waits_for_no_request():
    st = gateway_state()
    ev = threading.Event()
    with wired(st) as w:
        orig = w.iota.delete_device

        def slow(*a):
            assert ev.wait(10)
            return orig(*a)
        w.iota.delete_device = slow
        resp = client().delete(f"/api/sensors/dataloggers/{DL}", headers=AUTH)
        assert resp.status_code == 204 and resp.get_data() == b""
        assert GW in st.iota_devices          # still running in the background
        ev.set()
        gateways.join_background(10)
    assert GW not in st.iota_devices


def _removals(st):
    return [e[-1] for e in st.log if e[:2] in (("iota", "delete"), ("orion", "delete"))]


def test_a_failed_agent_deletion_stops_the_removal_and_keeps_the_entity(caplog):
    st = gateway_state()
    st.fail["iota_delete"] = True
    with caplog.at_level(logging.INFO):
        w, resp = delete_dl(st)
    assert resp.status_code == 204
    assert _removals(st) == [GW]
    assert GW_URN in w.orion.entities and all(d in w.orion.entities for d in DMS)
    warnings = [x for x in caplog.records if x.levelno == logging.WARNING]
    assert len(warnings) >= 1
    assert not any(DL in x.getMessage() and ROLE in x.getMessage() for x in warnings)


def _fail_one_measurement(w):
    orig = w.orion.delete

    def delete(url, **kw):
        if url.rsplit("/entities/", 1)[-1] == DMS[1]:
            w.orion.state.log.append(("orion", "delete", DMS[1]))
            return _resp(500)
        return orig(url, **kw)
    w.orion.delete = delete


def test_a_failed_measurement_deletion_keeps_the_entity_and_the_next_run_finishes(caplog):
    st = gateway_state()
    with caplog.at_level(logging.INFO):
        w, resp = delete_dl(st, setup=_fail_one_measurement)
    assert resp.status_code == 204
    assert _removals(st) == [GW, DMS[0], DMS[1]]       # stopped at the failed step
    assert GW_URN in w.orion.entities                    # the anchor the repair finds
    assert [x for x in caplog.records if x.levelno == logging.WARNING]
    st.log.clear()
    with wired(st) as w2:
        w2.orion.entities.update({GW_URN: {"id": GW_URN}, DMS[1]: {"id": DMS[1]}, DMS[2]: {"id": DMS[2]}})
        gateways.delete_gateway(TENANT, DL, GW)
        assert GW_URN not in w2.orion.entities and not any(d in w2.orion.entities for d in DMS)


def test_the_gateway_lock_not_given_in_time_deletes_nothing_and_warns(caplog):
    st = gateway_state()
    st.fail["lock_timeout"] = True
    with caplog.at_level(logging.INFO):
        w, resp = delete_dl(st)
    assert resp.status_code == 204
    assert not [e for e in st.log if e[:2] in (("iota", "delete"), ("orion", "delete"))]
    assert [x for x in caplog.records if x.levelno == logging.WARNING]


def test_a_gateway_that_was_never_assigned_an_id_starts_nothing(rec):
    st = base_state(sensors=())
    st.dataloggers[DL].update(gateway_external_id=None)
    w, resp = delete_dl(st)
    assert resp.status_code == 204
    assert not [e for e in st.log if e[:2] in (("iota", "delete"), ("orion", "delete"))]


def test_a_failed_datalogger_delete_removes_no_gateway():
    st = gateway_state()
    st.fail["commit"] = True
    w, resp = delete_dl(st)
    assert resp.status_code == 502
    assert not [e for e in st.log if e[:2] in (("iota", "delete"), ("orion", "delete"))]


def test_deleting_with_the_flag_off_reads_no_gateway_column_and_starts_nothing(monkeypatch):
    monkeypatch.delenv(gateways.FLAG)
    st = gateway_state()
    w, resp = delete_dl(st)
    assert resp.status_code == 204
    assert ("db", "gateway_read") not in st.log
    assert not [e for e in st.log if e[:2] in (("iota", "delete"), ("orion", "delete"))]


def test_the_deletion_only_touches_ids_of_gateway_shape():
    st = gateway_state()
    st.dataloggers[DL]["gateway_external_id"] = "S1"   # never a sensor id
    w, resp = delete_dl(st)
    assert resp.status_code == 204
    assert not [e for e in st.log if e[:2] in (("iota", "delete"), ("orion", "delete"))]


# ── R14 ─────────────────────────────────────────────────────────────────────

def test_a_second_409_clears_the_marker_and_recomputes_the_topics():
    st = base_state()
    st.dataloggers[DL].update(gateway_external_id=GW, gateway_provisioned_at="now", created_at=None)
    import datetime
    st.dataloggers[DL]["created_at"] = datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc)
    st.role_topics[ROLE] = {"/KEY123/S1/attrs", f"/KEY123/{GW}/attrs"}
    with wired(st) as w:
        def conflict(url, headers, **kw):
            raise w.iota.IotAgentError(409, "duplicate")
        w.iota.provision_device = conflict   # GET says 404 every time, POST says 409 every time
        assert gateways.project_gateway(TENANT, DL) == "failed:agent_device"
    assert st.dataloggers[DL]["gateway_provisioned_at"] is None
    assert ("db", "gateway_marker_clear") in st.log
    assert st.role_topics[ROLE] == {"/KEY123/S1/attrs"}


# ── the guard ───────────────────────────────────────────────────────────────

GUARDED = [
    ("put", f"/api/sensors/devices/{GW_URN}/datalogger", {"datalogger_id": DL}),
    ("put", f"/api/sensors/devices/{GW_URN}/profile", {"profile": "p"}),
    ("delete", f"/api/sensors/devices/{GW_URN}", None),
    ("get", f"/api/sensors/devices/{GW_URN}/deployments", None),
    ("post", f"/api/sensors/devices/{GW_URN}/deployments", {"reason": "moved"}),
    ("put", "/api/sensors/deployments/dep-1", {"observesFeature": None, "reason": "fix"}),
]


def _call(c, method, path, body):
    kw = {"headers": AUTH}
    if body is not None:
        kw.update(content_type="application/json", data=json.dumps(body))
    return getattr(c, method)(path, **kw)


@pytest.mark.parametrize("method,path,body", GUARDED)
def test_a_gateway_urn_on_a_sensor_route_is_409_and_nothing_is_read_or_written(
        method, path, body, monkeypatch):
    import deployments
    st = base_state(second=True)
    with wired(st) as w:
        monkeypatch.setattr(deployments, "get_deployment", lambda t, i: {"id": i, "device_urn": GW_URN})
        r = _call(client(), method, path, body)
    assert r.status_code == 409
    assert r.get_json() == {"error": "This is a gateway, not a sensor", "step": "gateway"}
    assert st.log == []
    assert w.orion.posted_urls == [] and w.orion.attr_posts == []


def test_the_registry_delete_of_a_gateway_entity_is_409_not_a_raw_orion_delete(monkeypatch):
    from flask import Flask, g
    import blueprints.entities as entities
    view = entities.delete_instance
    while hasattr(view, "__wrapped__"):   # the auth and ownership decorators read Orion themselves
        view = view.__wrapped__
    st = base_state()
    app = Flask(__name__)
    with wired(st) as w, app.test_request_context():
        g.tenant, g.farmer_id, g.roles = TENANT, "u1", ["TenantAdmin"]
        g.current_user = {"tenant_id": TENANT, "user_id": "u1"}
        w.orion.entities[GW_URN] = {"id": GW_URN}
        monkeypatch.setattr(entities, "requests", w.orion)   # a raw delete would land here
        r = view("Device", GW_URN)
    resp, status = r if isinstance(r, tuple) else (r, r.status_code)
    assert status == 409 and resp.get_json()["step"] == "gateway"
    assert GW_URN in w.orion.entities
    assert not [e for e in st.log if e[:1] == ("orion",)]


def test_parse_device_urn_refuses_a_gateway_but_other_tenants_stay_404():
    with pytest.raises(dataloggers.ApiError) as exc:
        dataloggers.parse_device_urn(TENANT, GW_URN)
    assert (exc.value.status, exc.value.message, exc.value.step) == (409, "This is a gateway, not a sensor", "gateway")
    with pytest.raises(dataloggers.ApiError) as exc:
        dataloggers.parse_device_urn(TENANT, f"urn:ngsi-ld:Device:tenant-b:{GW}")
    assert exc.value.status == 404
    assert dataloggers.parse_device_urn(TENANT, S1) == "S1"
    assert dataloggers.is_registered_device_urn(TENANT, GW_URN) is True  # so the delete route reaches the guard


@pytest.mark.parametrize("ext", ["gw-0123456789ab", "GW-0123456789AB", "gw-x", "Gw-"])
def test_registering_a_gw_external_id_is_400_and_nothing_is_read(ext):
    st = base_state(sensors=())
    with wired(st):
        r = post(client(), "/api/sensors/register", dict(BASE, external_id=ext))
    assert r.status_code == 400
    assert st.log == []


def test_a_sensor_id_that_only_contains_gw_still_registers():
    st = base_state(sensors=())
    with wired(st):
        r = post(client(), "/api/sensors/register", dict(BASE, external_id="s-gw-1"))
    assert r.status_code == 201


# ── fix round 1: a deletion is never dropped ────────────────────────────────

GW2 = "gw-ba9876543210"
GW2_URN = f"urn:ngsi-ld:Device:{TENANT}:{GW2}"


def test_a_deletion_runs_even_while_a_projection_holds_the_slot(rec):
    st = gateway_state()
    seed_datalogger(st, dl_id=DL2, username=ROLE2)
    r = rec(st)
    r.release = threading.Event()
    with wired(st) as w:
        w.orion.entities[GW_URN] = {"id": GW_URN}
        c = client()
        assert post(c, f"/api/sensors/dataloggers/{DL2}/revoke").status_code == 200
        assert r.entered.wait(5)                       # the projection holds the slot
        assert c.delete(f"/api/sensors/dataloggers/{DL}", headers=AUTH).status_code == 204
        deadline = threading.Event()
        for _ in range(100):                            # the deletion finishes while it is held
            if GW not in st.iota_devices and GW_URN not in w.orion.entities:
                break
            deadline.wait(0.05)
        assert GW not in st.iota_devices and GW_URN not in w.orion.entities
        r.release.set()
        gateways.join_background(10)
    assert [c_[1] for c_ in r.calls] == [DL2]


def test_two_deletions_at_once_both_complete():
    st = gateway_state()
    seed_datalogger(st, dl_id=DL2, username=ROLE2)
    st.dataloggers[DL2].update(gateway_external_id=GW2, gateway_provisioned_at="now", created_at=None)
    st.iota_devices[GW2] = {"device_id": GW2, "entity_name": GW2_URN}
    gate = threading.Event()
    with wired(st) as w:
        for u in (GW_URN, GW2_URN):
            w.orion.entities[u] = {"id": u}
        orig = w.iota.delete_device

        def slow(url, headers, device_id):
            assert gate.wait(10)
            return orig(url, headers, device_id)
        w.iota.delete_device = slow
        c = client()
        assert c.delete(f"/api/sensors/dataloggers/{DL}", headers=AUTH).status_code == 204
        assert c.delete(f"/api/sensors/dataloggers/{DL2}", headers=AUTH).status_code == 204
        gate.set()
        gateways.join_background(10)
    assert st.iota_devices == {} and GW_URN not in w.orion.entities and GW2_URN not in w.orion.entities


def test_an_unreadable_gateway_column_does_not_fail_the_delete(monkeypatch, caplog):
    st = gateway_state()

    def boom(*a, **kw):
        raise RuntimeError("read failed")
    monkeypatch.setattr(dataloggers, "gateway_columns", boom)
    with caplog.at_level(logging.INFO):
        w, resp = delete_dl(st)
    assert resp.status_code == 204 and DL not in st.dataloggers
    assert not [e for e in st.log if e[:2] in (("iota", "delete"), ("orion", "delete"))]
    assert any(x.levelno == logging.WARNING and "RuntimeError" in x.getMessage() for x in caplog.records)
    assert ("sql", "rollback") not in st.log          # the transaction was kept, not abandoned


# ── fix round 1: with the flag off, the side effects are the base's ─────────

SIDE = os.path.join(os.path.dirname(__file__), "recordings", "gateway_operations_side_effects_base.json")
_ID = __import__("re").compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_USER = __import__("re").compile(r"dl_[0-9a-f]{16}")


def _mask_log(value):
    if isinstance(value, (list, tuple)):
        return [_mask_log(v) for v in value]
    if isinstance(value, str):
        return _USER.sub("<user>", _ID.sub("<id>", value))
    return value


def _side_cases():
    cases = [(n, f) for n in OPS for f in (False, True)]
    return cases + [("delete_datalogger", False), ("delete_datalogger_gateway", False)]


@pytest.mark.parametrize("name,fail", _side_cases())
def test_with_the_flag_off_the_side_effects_are_the_ones_recorded_on_base(name, fail, monkeypatch):
    monkeypatch.delenv(gateways.FLAG)
    if name.startswith("delete_datalogger"):
        st = gateway_state() if name.endswith("gateway") else base_state(sensors=())
        with wired(st):
            client().delete(f"/api/sensors/dataloggers/{DL}", headers=AUTH)
            gateways.join_background(10)
    else:
        st, _r, _resp = run_op(name, fail=fail)
    got = _mask_log(list(st.log))
    key = f"{name}:{'fail' if fail else 'ok'}"
    if os.environ.get("RECORD_BASE"):
        data = json.load(open(SIDE)) if os.path.exists(SIDE) else {}
        data[key] = got
        json.dump(data, open(SIDE, "w"), indent=1, sort_keys=True)
        return
    assert got == json.load(open(SIDE))[key]
