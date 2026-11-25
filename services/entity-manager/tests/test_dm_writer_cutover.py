"""Cutover command: who writes the DeviceMeasurement entities of a device (everything mocked)."""
import copy
import importlib.util
import logging
import os
import re
import signal
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import psycopg2
import pytest

from tests.telemetry_fakes import PROFILE, TENANT, FakeState, _services_dir  # noqa: F401,I001  (stubs first)

import dataloggers  # noqa: E402
import dm_writer_cutover as cut  # noqa: E402
import writer_move_lock as wml  # noqa: E402

_REAL_EXCLUSIVE_LOCK = cut._exclusive_lock     # the fixtures replace it by a fake

CTX_URL = "http://context.invalid/ctx.json"
DM_TYPE_IRI = "https://smartdatamodels.org/dataModel.Device/DeviceMeasurement"
DEV = "https://smartdatamodels.org/dataModel.Device/"
NKZ = "https://nkz-os.org/ns/"
CONTEXT_DOC = {"@context": [
    "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld",
    {"schema": "https://schema.org/", "nkz": NKZ,
     "DeviceMeasurement": {"@id": DM_TYPE_IRI, "@type": "@id"},
     "refDevice": DEV + "hasDevice", "controlledProperty": DEV + "controlledProperty",
     "measurementType": DEV + "measurementType", "deviceType": DEV + "deviceType",
     "numValue": DEV + "numValue", "textValue": DEV + "textValue",
     "dateObserved": "https://smartdatamodels.org/dateObserved", "timeSource": "nkz:timeSource",
     "name": "schema:name"}]}
DEFAULT_VOCAB = "https://uri.etsi.org/ngsi-ld/default-context/"

MS = PROFILE["mapping"]["measurements"]
ONE_MS = [{"type": "t", "incomingKey": "r1", "sdmAttribute": "airTemperature", "unit": "°C"}]


def urn_of(ext):
    return f"urn:ngsi-ld:Device:{TENANT}:{ext}"


class World:
    """Rows, Orion entities, profiles and the IoT Agent, with an ordered event log."""

    def __init__(self):
        self.rows = {}            # urn -> dict(external_id, dm_writer, dm_shape)
        self.entities = {}        # urn -> profile code
        self.profiles = {}        # code -> measurements
        self.agent = {}           # external_id -> attributes (the agent's own document)
        self.log = []
        self.puts = 0
        self.put_fail = lambda n, ext: False
        self.after_put = None     # callback(n, ext)
        self.update_fail = False      # the UPDATE raises a database error: nothing written
        self.commit_fail = False      # COMMIT fails and the row is NOT written (outcome unknown)
        self.commit_lies = False      # COMMIT fails but the row IS written (outcome unknown)
        self.before_commit = None     # callback(urn) run on the lock connection before the UPDATE
        self.lock_busy = False
        self.dead_after_commit = False
        self.losses = 0               # locks that really died
        self.after_read = None        # callback(urn) after a read through the lock, before it returns
        self.lock_unavailable = False
        self.tenants = {TENANT: None}  # tenant -> deleted_at; absent = no row
        self.tenant_read_fail = False
        self.state_read_fail = False
        self.get_fail = False
        self.orion_fail = False
        self.on_sleep = None
        self.before_put = None        # callback(n, ext) run before the PUT is applied
        self.before_get = None        # callback(ext) run when the agent's document is read
        self.locks = []               # every FakeLock the command created
        self.read_state_fail = False  # reads through the lock fail (the lock stays alive)
        self.retake_refused = False   # after a lock is lost, taking it again is refused
        self.inject = []              # [(kind, index, effect)]: faults at the nth op of a kind
        self.counts = {}              # kind -> number of ops seen

    def tick(self, kind):
        """Count one operation of `kind`; run the injected fault that is due at it."""
        n = self.counts.get(kind, 0)
        self.counts[kind] = n + 1
        for k, i, effect in self.inject:
            if k == kind and i == n:
                if effect == "signal":
                    os.kill(os.getpid(), signal.SIGINT)
                elif effect == "lost":
                    self.lose_lock()
                elif effect == "boom":
                    raise RuntimeError("unexpected")
                elif effect == "pipe":
                    raise BrokenPipeError("closed pipe")
                elif effect == "valerr":
                    raise ValueError("I/O operation on closed file")
                elif effect == "db":
                    raise psycopg2.OperationalError("statement timeout")
                elif effect == "unknown":
                    raise wml.CommitOutcomeUnknown("commit failed (OperationalError)")
                else:
                    raise AssertionError(effect)

    def lose_lock(self):
        """The newest live lock connection dies (the lock is gone with it)."""
        live = [lk for lk in self.locks if lk.held and not lk.dead]
        if live:
            live[-1].dead = True
            self.losses += 1
        self.lock_busy = self.retake_refused

    def add(self, ext, writer="em", shape=None, code="p", ms=None, entity=True):
        urn = urn_of(ext)
        self.rows[urn] = dict(device_urn=urn, external_id=ext, dm_writer=writer, dm_shape=shape)
        if entity:
            self.entities[urn] = code
        if ms is not None or code not in self.profiles:
            self.profiles[code] = copy.deepcopy(MS if ms is None else ms)
        return urn

    def settle_agent(self):
        """The agent holds what the current rows and profiles say (a finished cutover)."""
        for urn, row in self.rows.items():
            code = self.entities.get(urn)
            self.agent[row["external_id"]] = dataloggers._attributes_for_state(
                (row["dm_writer"], row["dm_shape"]), TENANT, urn, row["external_id"], code,
                self.profiles[code])

    def events(self, kind):
        return [e for e in self.log if e[0] == kind]


class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0
        self._rows = []

    def execute(self, sql, params=()):
        w = self.conn.world
        sql = " ".join(sql.split())
        if sql.startswith("SELECT device_urn, external_id, dm_writer, dm_shape FROM iot_datalogger_devices"):
            assert params[0] == TENANT
            w.log.append(("read_rows",))
            rows = [dict(r) for r in w.rows.values()]
            if "device_urn = %s" in sql:
                rows = [r for r in rows if r["device_urn"] == params[1]]
            self._rows = sorted(rows, key=lambda r: r["external_id"])
        elif sql.startswith("SELECT dm_writer, dm_shape FROM iot_datalogger_devices"):
            if w.state_read_fail:
                raise RuntimeError("state read failed")
            r = w.rows.get(params[1])
            self._rows = [dict(dm_writer=r["dm_writer"], dm_shape=r["dm_shape"])] if r else []
        elif sql.startswith("SELECT deleted_at FROM tenants WHERE tenant_id = %s"):
            if w.tenant_read_fail:
                raise RuntimeError("tenant read failed")
            w.log.append(("read_tenant", params[0]))
            self._rows = [dict(deleted_at=w.tenants[params[0]])] if params[0] in w.tenants else []
        elif sql.startswith("UPDATE iot_datalogger_devices"):
            raise AssertionError("a row was written through the pool; it belongs on the lock connection")
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _Conn:
    """The pool connection: reads only."""

    def __init__(self, world):
        self.world = world

    def cursor(self, *a, **kw):
        return _Cur(self)

    def commit(self):
        self.world.log.append(("pool_commit",))

    def rollback(self):
        self.world.log.append(("pool_rollback",))


class FakeLock:
    """Stand-in for writer_move_lock.ExclusiveLock: logs lock, unlock and every committed row."""

    def __init__(self, world, tenant):
        self.world = world
        self.tenant = tenant
        self.held = False
        self.dead = False
        world.locks.append(self)

    def alive(self):
        self.world.tick("alive")
        return self.held and not self.dead

    def _live(self):
        assert self.held, "used a lock that is not held"
        if self.dead:
            raise psycopg2.OperationalError("connection lost")

    def read_state(self, urn):
        w = self.world
        self._live()
        w.log.append(("read_state", urn))
        w.tick("read")
        self._live()
        if w.state_read_fail or w.read_state_fail:
            raise psycopg2.OperationalError("state read failed")
        r = w.rows.get(urn)
        state = (r["dm_writer"], r["dm_shape"]) if r else None
        if w.after_read:
            w.after_read(urn)
        return state

    def __enter__(self):
        w = self.world
        if w.lock_busy:
            raise wml.MoveInProgress(self.tenant)
        if any(lk.held for lk in w.locks if lk is not self):
            raise wml.MoveInProgress(self.tenant)      # another session (even a dead one) holds it
        if w.lock_unavailable:
            raise wml.LockUnavailable("cannot connect (OperationalError)")
        self.held = True
        w.log.append(("lock", self.tenant))
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.held:
            self.world.log.append(("unlock",))
        self.held = False
        return False

    def commit_state(self, urn, previous, target):
        w = self.world
        self._live()
        if w.before_commit:
            w.before_commit(urn)
        w.tick("commit_before")
        self._live()
        if w.update_fail:
            raise psycopg2.OperationalError("update failed")
        row = w.rows.get(urn)
        if row is None or (row["dm_writer"], row["dm_shape"]) != tuple(previous):
            raise wml.RowChanged(urn)
        w.log.append(("update", urn, target[0], target[1]))
        if w.commit_fail:
            raise wml.CommitOutcomeUnknown("commit failed (OperationalError)")
        row["dm_writer"], row["dm_shape"] = target
        w.log.append(("commit",))
        w.tick("commit_after")
        if w.dead_after_commit:
            raise psycopg2.OperationalError("connection lost")
        if w.commit_lies:
            raise wml.CommitOutcomeUnknown("commit failed (OperationalError)")


class _IotaError(Exception):
    def __init__(self, status_code=500, body=""):
        super().__init__(f"agent {status_code}")
        self.status_code = status_code


def _fake_iota(world):
    import sys
    real = sys.modules["common.iot_agent"]

    def update(url, headers, device_id, attributes):
        world.puts += 1
        n = world.puts
        if world.before_put:
            world.before_put(n, device_id)
        world.tick("put_before")
        if world.put_fail(n, device_id):
            world.log.append(("put_failed", device_id))
            raise _IotaError(500)
        world.agent[device_id] = copy.deepcopy(attributes)
        kinds = sorted({a.get("entity_type", "Device") for a in attributes})
        world.log.append(("put", device_id, tuple(kinds)))
        if world.after_put:
            world.after_put(n, device_id)
        world.tick("put_after")

    def get(url, headers, device_id):
        world.tick("get")
        if world.before_get:
            world.before_get(device_id)
        if world.get_fail or device_id not in world.agent:
            raise _IotaError(404)
        return {"device_id": device_id, "attributes": copy.deepcopy(world.agent[device_id])}

    return SimpleNamespace(update_device_attributes=update, get_device=get,
                           build_iota_attributes=real.build_iota_attributes,
                           IotAgentError=_IotaError)


@pytest.fixture
def world(monkeypatch):
    w = World()

    @contextmanager
    def db(tenant):
        yield _Conn(w)

    def orion_devices(tenant, urns):
        w.log.append(("orion",))
        if w.orion_fail:
            raise dataloggers.ApiError(502, "orion down", "orion_read")
        return {u: {"id": u, "profileCode": {"type": "Property", "value": w.entities[u]}}
                for u in urns if u in w.entities and w.entities[u] is not None}

    def profile_measurements(tenant, code):
        ms = w.profiles.get(code)
        return copy.deepcopy(ms) if ms is not None else None

    iota = _fake_iota(w)
    monkeypatch.setattr(dataloggers, "get_db_connection_with_tenant", db)
    monkeypatch.setattr(cut, "get_db_connection_with_tenant", db)
    monkeypatch.setattr(dataloggers, "iot_agent", iota)
    monkeypatch.setattr(cut, "iot_agent", iota)
    monkeypatch.setattr(dataloggers, "_orion_devices", orion_devices)
    monkeypatch.setattr(dataloggers, "_profile_measurements", profile_measurements)
    monkeypatch.setattr(cut, "_exclusive_lock", lambda tenant: FakeLock(w, tenant))
    return w


@pytest.fixture(autouse=True)
def real_tenant_normaliser(monkeypatch):
    """The suite stubs `common.tenant_utils`; the command must judge tenants with the real one."""
    spec = importlib.util.spec_from_file_location(
        "_real_tenant_utils", os.path.join(_services_dir, "common", "tenant_utils.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(cut, "normalize_tenant_id", mod.normalize_tenant_id)


@pytest.fixture(autouse=True)
def move_enabled(monkeypatch):
    """The shipped constant is off; the tests of the move itself switch it on."""
    monkeypatch.setattr(cut, "MOVE_TO_IOTA_ENABLED", True)


@pytest.fixture(autouse=True)
def sleeps(world, monkeypatch):
    """The settle wait never really sleeps; it lands in the event log."""
    seen = []

    def fake_sleep(seconds):
        seen.append(seconds)
        world.log.append(("sleep", seconds))
        world.tick("sleep")
        if world.on_sleep:
            world.on_sleep(seconds)

    monkeypatch.setattr(cut, "_sleep", fake_sleep)
    return seen


def run(*argv):
    return cut.main(list(argv))


def _waited(sleeps):
    """Total of the wait, which is sliced: every slice is at most half a second."""
    assert sleeps and all(0 < x <= 0.5 for x in sleeps)
    return sum(sleeps)


BASE = ("--tenant", TENANT)
SINCE = "2026-06-01T00:00:00Z"
VERIFY = ("--verify", "--since", SINCE)


# -- arguments ----------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["--to", "iota", "--apply"],                      # shape missing
    ["--to", "em", "--shape", "sdm", "--apply"],      # shape makes no sense for em
    ["--to", "iota", "--shape", "sdm"],               # no mode
    ["--to", "iota", "--shape", "sdm", "--apply", "--dry-run"],
    ["--to", "iota", "--shape", "other", "--apply"],
    ["--shape", "sdm", "--apply"],                    # no direction
])
def test_bad_arguments_exit_2(world, argv):
    with pytest.raises(SystemExit) as e:
        run(*BASE, *argv)
    assert e.value.code == 2
    assert world.puts == 0 and not world.events("update")


# -- dry run ------------------------------------------------------------------------------

def test_dry_run_writes_nothing_and_lists_the_plan(world, capsys):
    a, b = world.add("S1"), world.add("S2")
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--dry-run") == 0
    out = capsys.readouterr().out
    assert a in out and b in out and "would" in out
    assert world.puts == 0 and not world.events("update") and not world.events("commit")


def test_dry_run_reports_every_failure_and_exits_1(world, capsys):
    ok = world.add("S1")
    no_entity = world.add("S2", entity=False)
    no_code = world.add("S3")
    world.entities[no_code] = None
    no_profile = world.add("S4", code="ghost")
    del world.profiles["ghost"]
    bad_unit = world.add("S5", code="bad", ms=[{"type": "t", "sdmAttribute": "airTemperature",
                                                "unit": "no-such-unit"}])
    assert run(*BASE, "--to", "iota", "--shape", "sdm", "--dry-run") == 1
    out = capsys.readouterr().out
    for urn in (no_entity, no_code, no_profile, bad_unit):
        assert urn in out
    assert ok not in out.split("FAILED")[-1]
    assert world.puts == 0 and not world.events("update")


def test_apply_refuses_to_start_when_any_device_fails_validation(world, capsys):
    good = world.add("S1")
    world.add("S2", entity=False)
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert world.puts == 0 and not world.events("update")
    assert world.rows[good]["dm_writer"] == "em"
    assert "nothing was written" in capsys.readouterr().out


def test_channels_sharing_a_dm_id_that_differ_fail_validation(world, capsys):
    clash = [{"type": "a", "sdmAttribute": "airTemperature", "unit": "°C"},
             {"type": "b", "incomingKey": "b", "sdmAttribute": "airTemperature", "unit": "%"}]
    urn = world.add("S1", code="clash", ms=clash)
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--dry-run") == 1
    out = capsys.readouterr().out
    assert urn in out and "airTemperature" in out


def test_channels_sharing_a_dm_id_that_agree_pass(world):
    same = [{"type": "a", "sdmAttribute": "airTemperature", "unit": "°C"},
            {"type": "a", "incomingKey": "b", "sdmAttribute": "airTemperature", "unit": "°C"}]
    world.add("S1", code="same", ms=same)
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--dry-run") == 0


def test_orion_read_failure_is_fatal(world):
    world.add("S1")
    world.orion_fail = True
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert world.puts == 0


def test_no_devices_says_so_and_exits_0(world, capsys):
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    assert "no devices" in capsys.readouterr().out.lower()


def test_unlinked_device_exits_1(world, capsys):
    world.add("S1")
    assert run(*BASE, "--device", urn_of("nope"), "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert world.puts == 0


def test_device_selects_only_that_device(world):
    a, b = world.add("S1"), world.add("S2")
    assert run(*BASE, "--device", a, "--to", "iota", "--shape", "legacy", "--apply") == 0
    assert world.rows[a]["dm_writer"] == "iota" and world.rows[b]["dm_writer"] == "em"


# -- apply, to iota -----------------------------------------------------------------------

def test_to_iota_puts_then_updates_row_then_puts_again(world):
    urn = world.add("S1")
    assert run(*BASE, "--to", "iota", "--shape", "sdm", "--apply") == 0
    kinds = [e[0] for e in world.log if e[0] in ("put", "update", "commit")]
    assert kinds == ["put", "update", "commit", "put"]
    assert world.rows[urn]["dm_writer"] == "iota" and world.rows[urn]["dm_shape"] == "sdm"
    assert all("DeviceMeasurement" in e[2] for e in world.events("put"))
    assert world.agent["S1"] == dataloggers._attributes_for_state(
        ("iota", "sdm"), TENANT, urn, "S1", "p", MS)


def test_final_put_rereads_the_profile(world):
    world.add("S1")
    new_ms = ONE_MS

    def change_profile(n, ext):
        if n == 1:
            world.profiles["p"] = copy.deepcopy(new_ms)

    world.after_put = change_profile
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    urn = urn_of("S1")
    assert world.agent["S1"] == dataloggers._attributes_for_state(
        ("iota", "legacy"), TENANT, urn, "S1", "p", new_ms)


def test_first_put_failure_leaves_the_device_as_it_was(world, capsys):
    a, b = world.add("S1"), world.add("S2")
    world.put_fail = lambda n, ext: ext == "S1"
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert world.rows[a]["dm_writer"] == "em" and world.rows[b]["dm_writer"] == "iota"
    out = capsys.readouterr().out
    assert a in out and "FAILED" in out
    assert [e for e in world.events("update") if e[1] == a] == []


def test_db_failure_restores_the_previous_mappings(world, capsys):
    urn = world.add("S1")
    world.settle_agent()
    before = copy.deepcopy(world.agent["S1"])
    world.update_fail = True
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert world.rows[urn]["dm_writer"] == "em"
    assert world.agent["S1"] == before
    assert [e[0] for e in world.log if e[0] in ("put", "put_failed")] == ["put", "put"]
    assert "restored" in capsys.readouterr().out


def test_db_failure_and_failed_restore_is_critical(world, caplog):
    urn = world.add("S1")
    world.update_fail = True
    world.put_fail = lambda n, ext: n == 2
    with caplog.at_level(logging.CRITICAL):
        assert run(*BASE, "--to", "iota", "--shape", "sdm", "--apply") == 1
    crit = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert crit
    text = " ".join(r.getMessage() for r in crit)
    assert urn in text and "'em'" in text and "'iota'" in text and "'sdm'" in text


def test_failed_commit_with_unreadable_state_keeps_the_new_mappings(world, caplog):
    """The commit may have gone through: restoring would leave nobody writing."""
    urn = world.add("S1")
    world.commit_fail = True
    world.state_read_fail = True
    with caplog.at_level(logging.CRITICAL):
        assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert world.puts == 1
    assert any(a.get("entity_type") == "DeviceMeasurement" for a in world.agent["S1"])
    assert any(urn in r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL)


def test_failed_commit_that_did_commit_carries_on(world):
    urn = world.add("S1")
    world.commit_lies = True            # COMMIT raised after the row was written
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    assert world.rows[urn]["dm_writer"] == "iota"
    assert world.puts == 2


def test_final_put_failure_is_reported_and_the_rerun_is_not_a_noop(world, capsys):
    urn = world.add("S1")

    def change_profile(n, ext):
        if n == 1:
            world.profiles["p"] = copy.deepcopy(ONE_MS)

    world.after_put = change_profile
    world.put_fail = lambda n, ext: n == 2
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    out = capsys.readouterr().out
    assert world.rows[urn]["dm_writer"] == "iota"
    assert urn in out and "row already updated" in out and "rerun" in out
    world.put_fail = lambda n, ext: False
    puts_before = world.puts
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    assert world.puts == puts_before + 1
    assert world.agent["S1"] == dataloggers._attributes_for_state(
        ("iota", "legacy"), TENANT, urn, "S1", "p", ONE_MS)


def test_shape_change_on_an_iota_device(world):
    urn = world.add("S1", writer="iota", shape="legacy")
    world.settle_agent()
    assert run(*BASE, "--to", "iota", "--shape", "sdm", "--apply") == 0
    assert world.rows[urn]["dm_shape"] == "sdm"
    assert world.agent["S1"] == dataloggers._attributes_for_state(
        ("iota", "sdm"), TENANT, urn, "S1", "p", MS)


# -- rerun --------------------------------------------------------------------------------

def test_rerun_is_a_noop(world, capsys):
    world.add("S1")
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    puts, updates = world.puts, len(world.events("update"))
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    assert world.puts == puts and len(world.events("update")) == updates
    assert "unchanged" in capsys.readouterr().out


def test_dry_run_of_a_noop_plans_nothing(world, capsys):
    world.add("S1")
    run(*BASE, "--to", "iota", "--shape", "legacy", "--apply")
    capsys.readouterr()
    puts = world.puts
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--dry-run") == 0
    assert "unchanged" in capsys.readouterr().out and world.puts == puts


def test_same_state_but_different_agent_mappings_pushes_again(world):
    urn = world.add("S1", writer="iota", shape="legacy")
    world.settle_agent()
    world.agent["S1"][0] = dict(world.agent["S1"][0], expression="x * 2")
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    assert world.puts == 1 and not world.events("update")
    assert world.agent["S1"] == dataloggers._attributes_for_state(
        ("iota", "legacy"), TENANT, urn, "S1", "p", MS)


def test_same_state_with_unreadable_agent_pushes_again(world):
    world.add("S1", writer="iota", shape="legacy")
    world.settle_agent()
    world.get_fail = True
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    assert world.puts == 1


def test_agent_comparison_ignores_absent_versus_empty_fields(world):
    world.add("S1", writer="iota", shape="legacy")
    world.settle_agent()
    world.agent["S1"] = [dict(a, metadata=a.get("metadata") or {}, expression=a.get("expression"))
                         for a in world.agent["S1"]]
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    assert world.puts == 0


# -- apply, to em -------------------------------------------------------------------------

def test_to_em_updates_the_row_first_then_puts_device_only(world):
    urn = world.add("S1", writer="iota", shape="sdm")
    world.settle_agent()
    assert run(*BASE, "--to", "em", "--apply") == 0
    kinds = [e[0] for e in world.log if e[0] in ("put", "update", "commit")]
    assert kinds == ["update", "commit", "put"]
    assert world.rows[urn]["dm_writer"] == "em" and world.rows[urn]["dm_shape"] is None
    assert world.events("update")[0][2:] == ("em", None)
    assert world.agent["S1"] == dataloggers._attributes_for_state(("em", None), TENANT, urn, "S1", "p", MS)
    assert world.events("put")[0][2] == ("Device",)


def test_to_em_row_failure_puts_nothing(world):
    urn = world.add("S1", writer="iota", shape="legacy")
    world.settle_agent()
    world.update_fail = True
    assert run(*BASE, "--to", "em", "--apply") == 1
    assert world.puts == 0 and world.rows[urn]["dm_writer"] == "iota"


def test_to_em_put_failure_is_listed_and_the_rerun_repairs(world, capsys):
    urn = world.add("S1", writer="iota", shape="legacy")
    world.settle_agent()
    world.put_fail = lambda n, ext: True
    assert run(*BASE, "--to", "em", "--apply") == 1
    out = capsys.readouterr().out
    assert world.rows[urn]["dm_writer"] == "em" and urn in out and "rerun" in out
    world.put_fail = lambda n, ext: False
    assert run(*BASE, "--to", "em", "--apply") == 0
    assert world.agent["S1"] == dataloggers._attributes_for_state(("em", None), TENANT, urn, "S1", "p", MS)


def test_to_em_rerun_is_a_noop(world):
    world.add("S1")
    world.settle_agent()
    assert run(*BASE, "--to", "em", "--apply") == 0
    assert world.puts == 0 and not world.events("update")


# -- verify -------------------------------------------------------------------------------

class Http:
    """Stand-in for `requests` in the command: the context document and the broker."""

    def __init__(self, dms, count=None, page=1000, context=None, context_status=200):
        self.dms = dms
        self.count = len(dms) if count is None else count
        self.page = page
        self.context = CONTEXT_DOC if context is None else context
        self.context_status = context_status
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, dict(headers or {}), dict(params or {})))
        if url == CTX_URL:
            r = MagicMock(status_code=self.context_status)
            r.json.return_value = self.context
            return r
        params = params or {}
        off, lim = int(params.get("offset", 0)), int(params.get("limit", 1000))
        r = MagicMock(status_code=200, headers={"NGSILD-Results-Count": str(self.count)})
        r.json.return_value = self.dms[off:off + min(lim, self.page)]
        return r

    def orion_calls(self):
        return [c for c in self.calls if c[0] != CTX_URL]


OLD = "2026-05-01T00:00:00Z"     # before SINCE
NEW = "2026-06-02T00:00:00Z"     # after SINCE


def dm(ext, name, *, shape="legacy", mtype="t", value="num", drop=(), extra=None, tenant=TENANT,
       modified=NEW, value_modified=None, date_observed=OLD, observed_at="2026-01-01T00:00:00Z"):
    """A DeviceMeasurement as the broker answers with options=sysAttrs (no Link header)."""
    prop = {"type": "Property", "value": 1}
    value_modified = value_modified or {}

    def val(term):
        v = {"type": "Property", "value": 1, "modifiedAt": value_modified.get(term, modified)}
        if observed_at is not None:
            v["observedAt"] = observed_at
        return v

    attrs = {
        DEV + "hasDevice": {"type": "Relationship", "object": urn_of(ext)},
        DEV + "controlledProperty": prop, DEV + "deviceType": prop,
        "https://smartdatamodels.org/dateObserved": {"type": "Property", "value": date_observed},
        NKZ + "timeSource": prop,
    }
    if mtype is not None:
        attrs[DEV + "measurementType"] = prop
    if shape == "sdm":
        attrs["https://schema.org/name"] = prop
    if value in ("num", "both"):
        attrs[DEV + "numValue"] = val("numValue")
    if value in ("text", "both"):
        attrs[DEV + "textValue"] = val("textValue")
    for k in drop:
        attrs.pop(k)
    attrs.update(extra or {})
    out = {"id": f"urn:ngsi-ld:DeviceMeasurement:{tenant}:{ext}:{name}",
           "type": DM_TYPE_IRI, "createdAt": OLD, **attrs}
    if modified is not None:
        out["modifiedAt"] = modified
    return out


@pytest.fixture
def http(monkeypatch, world):
    monkeypatch.setenv("CONTEXT_URL", CTX_URL)
    holder = SimpleNamespace(h=None)

    def install(h):
        world.settle_agent()     # the agent holds what rows and profiles call for
        holder.h = h
        monkeypatch.setattr(cut, "requests", h)
        monkeypatch.setattr(cut, "inject_fiware_headers", _fake_headers)
        return h

    return install


def _fake_headers(headers, tenant=None, **kw):
    headers["NGSILD-Tenant"] = tenant
    headers["Link"] = '<http://context.invalid/ctx.json>; rel="http://www.w3.org/ns/json-ld#context"'
    headers["Content-Type"] = "application/json"
    return headers


def iota_world(world, shape="legacy", ms=None):
    world.add("S1", writer="iota", shape=shape, code="p", ms=ONE_MS if ms is None else ms)
    return world


def test_verify_passes_on_clean_dms(world, http, capsys):
    iota_world(world)
    h = http(Http([dm("S1", "airTemperature")]))
    assert run(*BASE, *VERIFY) == 0
    out = capsys.readouterr().out
    assert "check (a)" in out and "check (b)" in out
    assert world.puts == 0 and not world.events("update")
    (url, headers, params), = h.orion_calls()
    assert "Link" not in headers and headers["NGSILD-Tenant"] == TENANT
    assert params["type"] == DM_TYPE_IRI and params["count"] == "true"
    assert params["limit"] == 1000 and params["offset"] == 0


def test_verify_sdm_shape_requires_name(world, http):
    iota_world(world, shape="sdm")
    http(Http([dm("S1", "airTemperature", shape="sdm", mtype="instant")]))
    assert run(*BASE, *VERIFY) == 0
    http(Http([dm("S1", "airTemperature", shape="legacy", mtype="instant")]))
    assert run(*BASE, *VERIFY) == 1


def test_verify_legacy_forbids_name(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", shape="sdm")]))
    assert run(*BASE, *VERIFY) == 1
    assert "https://schema.org/name" in capsys.readouterr().out


@pytest.mark.parametrize("extra", [
    {DEV + "unexpected": {"type": "Property", "value": 1}},
    {DEFAULT_VOCAB + "something": {"type": "Property", "value": 1}},
    {"something": {"type": "Property", "value": 1}},
])
def test_verify_extra_or_default_vocabulary_attribute_fails_b_only(world, http, capsys, extra):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", extra=extra)]))
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    key = next(iter(extra))
    assert key in out and "airTemperature" in out
    assert "check (a): ok" in out and "check (b): FAILED" in out


@pytest.mark.parametrize("key", ["TimeInstant", DEFAULT_VOCAB + "TimeInstant"])
def test_verify_time_instant_attribute_fails(world, http, capsys, key):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", extra={key: {"type": "Property", "value": 1}})]))
    assert run(*BASE, *VERIFY) == 1
    assert "TimeInstant" in capsys.readouterr().out


def test_verify_missing_attribute_fails(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", drop=(NKZ + "timeSource",))]))
    assert run(*BASE, *VERIFY) == 1
    assert NKZ + "timeSource" in capsys.readouterr().out


def test_verify_missing_measurement_type_only_when_profile_has_none(world, http):
    no_type = [{"incomingKey": "r1", "type": None, "sdmAttribute": "airTemperature", "unit": "°C"}]
    iota_world(world, ms=no_type)
    http(Http([dm("S1", "airTemperature", mtype=None)]))
    assert run(*BASE, *VERIFY) == 0
    http(Http([dm("S1", "airTemperature", mtype="t")]))
    assert run(*BASE, *VERIFY) == 1


def test_verify_dm_without_value_fails_a_for_every_device(world, http, capsys):
    world.add("S1", writer="em")                       # an em device: judged by (a) only
    world.add("S2", writer="iota", shape="legacy", code="p", ms=ONE_MS)
    bad = dm("S1", "airTemperature", value="none")
    http(Http([bad, dm("S2", "airTemperature")]))
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    assert "check (a): FAILED" in out and bad["id"] in out and "check (b): ok" in out


def test_verify_b_does_not_judge_em_devices(world, http):
    world.add("S1", writer="em")
    http(Http([dm("S1", "airTemperature", extra={DEV + "weird": {"type": "Property", "value": 1}})]))
    assert run(*BASE, *VERIFY) == 0


def test_verify_runtime_kind_accepts_one_or_both_values(world, http):
    rt = [{"type": "x", "sdmAttribute": "airTemperature"}]
    iota_world(world, ms=rt)
    for value in ("num", "text", "both"):
        http(Http([dm("S1", "airTemperature", mtype="x", value=value)]))
        assert run(*BASE, *VERIFY) == 0, value


def test_verify_numeric_kind_refuses_text_value(world, http):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", value="both")]))
    assert run(*BASE, *VERIFY) == 1


def test_verify_paginates_and_checks_the_count(world, http):
    iota_world(world)
    dms = [dm("S1", "airTemperature")]
    h = http(Http(dms * 3, page=1))   # three DMs, one per page (same id, fine for the shape)
    assert run(*BASE, *VERIFY) == 0
    assert [c[2]["offset"] for c in h.orion_calls()] == [0, 1, 2]
    http(Http(dms, count=5))
    assert run(*BASE, *VERIFY) == 1


def test_verify_missing_count_header_fails(world, http):
    iota_world(world)
    h = Http([dm("S1", "airTemperature")])
    orig = h.get

    def get(url, **kw):
        r = orig(url, **kw)
        r.headers = {}
        return r

    h.get = get
    http(h)
    assert run(*BASE, *VERIFY) == 1


def test_verify_fails_closed_without_context_url(world, http, monkeypatch):
    iota_world(world)
    h = http(Http([dm("S1", "airTemperature")]))
    monkeypatch.delenv("CONTEXT_URL")
    assert run(*BASE, *VERIFY) == 1
    assert h.orion_calls() == []


def test_verify_fails_closed_when_the_context_cannot_be_read(world, http):
    iota_world(world)
    http(Http([dm("S1", "airTemperature")], context_status=500))
    assert run(*BASE, *VERIFY) == 1


@pytest.mark.parametrize("term", ["numValue", "timeSource", "name", "DeviceMeasurement"])
def test_verify_fails_closed_when_a_term_is_missing(world, http, term):
    iota_world(world)
    ctx = copy.deepcopy(CONTEXT_DOC)
    del ctx["@context"][1][term]
    h = http(Http([dm("S1", "airTemperature")], context=ctx))
    assert run(*BASE, *VERIFY) == 1
    assert h.orion_calls() == []


def test_verify_device_narrows_check_b(world, http):
    world.add("S1", writer="iota", shape="legacy", code="p", ms=ONE_MS)
    world.add("S2", writer="iota", shape="legacy", code="p")
    bad = dm("S2", "airTemperature", extra={DEV + "weird": {"type": "Property", "value": 1}})
    http(Http([dm("S1", "airTemperature"), bad]))
    assert run(*BASE, "--device", urn_of("S1"), *VERIFY) == 0
    assert run(*BASE, *VERIFY) == 1


def test_command_prints_no_secrets(world, capsys):
    world.add("S1")
    world.agent["S1"] = []
    run(*BASE, "--to", "iota", "--shape", "legacy", "--apply")
    out = capsys.readouterr()
    assert "apikey" not in (out.out + out.err).lower()


# -- verify, round 1 ----------------------------------------------------------------------

OFFICIAL_EXTRA = {DEV + "weird": {"type": "Property", "value": 1}}


def test_orphan_dm_with_an_extra_official_key_fails_and_is_listed(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature"),
               dm("S1", "gone", extra=OFFICIAL_EXTRA)]))
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    assert "no channel in the current profile" in out and DEV + "weird" in out


def test_orphan_dm_missing_a_required_key_fails(world, http):
    iota_world(world)
    http(Http([dm("S1", "airTemperature"),
               dm("S1", "gone", drop=(DEV + "hasDevice",))]))
    assert run(*BASE, *VERIFY) == 1


def test_orphan_dm_without_any_value_fails_b(world, http):
    iota_world(world)
    http(Http([dm("S1", "airTemperature"), dm("S1", "gone", value="none")]))
    assert run(*BASE, *VERIFY) == 1


def test_clean_orphan_passes_and_is_listed(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature"), dm("S1", "gone", mtype=None, value="both")]))
    assert run(*BASE, *VERIFY) == 0
    assert "no channel in the current profile" in capsys.readouterr().out


def test_orphan_name_only_allowed_in_sdm(world, http):
    iota_world(world, shape="sdm")
    http(Http([dm("S1", "airTemperature", shape="sdm", mtype="instant"),
               dm("S1", "gone", shape="sdm")]))
    assert run(*BASE, *VERIFY) == 0
    world.rows[urn_of("S1")]["dm_shape"] = "legacy"
    http(Http([dm("S1", "airTemperature", mtype="instant"), dm("S1", "gone", shape="sdm")]))
    assert run(*BASE, *VERIFY) == 1


def test_no_iota_device_means_check_b_not_run(world, http, capsys):
    world.add("S1", writer="em")
    http(Http([dm("S1", "airTemperature")]))
    assert run(*BASE, *VERIFY) == 0
    assert "check (b): NOT RUN (no iota devices)" in capsys.readouterr().out


def test_no_iota_device_still_fails_on_check_a(world, http):
    world.add("S1", writer="em")
    http(Http([dm("S1", "airTemperature", value="none")]))
    assert run(*BASE, *VERIFY) == 1


def test_iota_device_without_any_dm_fails(world, http, capsys):
    iota_world(world)
    world.add("S2", writer="iota", shape="legacy", code="p", ms=ONE_MS)
    http(Http([dm("S1", "airTemperature")]))
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    assert urn_of("S2") in out and "no DeviceMeasurement" in out


def test_zero_dms_with_iota_devices_fails(world, http, capsys):
    iota_world(world)
    http(Http([]))
    assert run(*BASE, *VERIFY) == 1
    assert urn_of("S1") in capsys.readouterr().out


def test_dm_ids_match_by_prefix_with_colon_in_external_id(world, http):
    world.add("S:1", writer="iota", shape="legacy", code="p", ms=ONE_MS)
    world.add("S", writer="iota", shape="legacy", code="p", ms=ONE_MS)
    http(Http([dm("S:1", "airTemperature", extra=OFFICIAL_EXTRA),
               dm("S", "airTemperature")]))
    assert run(*BASE, *VERIFY) == 1       # the extra key is on the S:1 entity


def test_the_longest_id_prefix_owns_the_entity(world, http):
    """`S:1:airTemperature` belongs to device `S:1`, not to `S`: with the shortest prefix it would be
    an orphan of `S` and `S:1` would have delivered nothing."""
    world.add("S:1", writer="iota", shape="legacy", code="p", ms=ONE_MS)
    world.add("S", writer="iota", shape="legacy", code="p", ms=ONE_MS)
    http(Http([dm("S:1", "airTemperature"), dm("S", "airTemperature")]))
    assert run(*BASE, *VERIFY) == 0


def test_the_agent_comparison_looks_at_the_metadata_of_an_attribute(world):
    """A document that differs from the expected one only in `metadata` is a different document."""
    urn = world.add("S1", writer="iota", shape="legacy")
    world.settle_agent()
    expected = _attrs(urn, "S1", ("iota", "legacy"))
    assert cut._agent_matches(TENANT, "S1", expected)
    world.agent["S1"][0]["metadata"] = {"TimeInstant": {"type": "DateTime"}}
    assert not cut._agent_matches(TENANT, "S1", expected)


def test_a_lost_lock_never_commits_even_when_it_answers_again(world):
    world.add("S1")
    holder = cut._LockHolder(TENANT, FakeLock(world, TENANT).__enter__())
    assert holder.can_commit()
    holder.lost = True
    assert not holder.can_commit()


def test_dm_of_no_linked_device_is_counted_not_judged(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature"),
               dm("ZZ", "airTemperature", extra=OFFICIAL_EXTRA)]))
    assert run(*BASE, *VERIFY) == 0
    assert "1 entities matching no linked device" in capsys.readouterr().out


def test_verify_non_existing_tenant_404_exits_1(world, http):
    iota_world(world)
    h = Http([])
    orig = h.get

    def get(url, **kw):
        r = orig(url, **kw)
        if url != CTX_URL:
            r.status_code = 404
            r.json.return_value = {"type": "https://uri.etsi.org/ngsi-ld/errors/NonExistingTenant"}
        return r

    h.get = get
    http(h)
    assert run(*BASE, *VERIFY) == 1


def test_restore_rebuilds_from_a_fresh_profile_read(world):
    urn = world.add("S1")
    world.settle_agent()

    def change(n, ext):
        if n == 1:
            world.profiles["p"] = copy.deepcopy(ONE_MS)

    world.after_put = change
    world.update_fail = True
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert world.agent["S1"] == dataloggers._attributes_for_state(("em", None), TENANT, urn, "S1", "p", ONE_MS)


def test_restore_falls_back_to_plan_time_mappings_and_says_so(world, caplog):
    urn = world.add("S1")
    world.settle_agent()
    before = copy.deepcopy(world.agent["S1"])

    def break_profiles(n, ext):
        world.orion_fail = True

    world.after_put = break_profiles
    world.update_fail = True
    with caplog.at_level(logging.WARNING):
        assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert world.agent["S1"] == before
    assert "plan-time" in caplog.text


# -- settle (batch) -----------------------------------------------------------------------

def test_the_wait_is_at_least_two_minutes():
    assert cut.DEFAULT_SETTLE == cut.MIN_SETTLE == 120.0 and cut.MAX_SETTLE == 600.0
    assert "120-600, default 120" in cut._parser().format_help().replace("\n", " ").replace("  ", " ")


def test_settle_defaults_to_120_and_waits_once_per_batch(world, sleeps):
    a, b = world.add("S1"), world.add("S2")
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    assert _waited(sleeps) == 120.0
    kinds = [e[0] for e in world.log if e[0] in ("put", "sleep", "update")]
    kinds = [k for i, k in enumerate(kinds) if i == 0 or not (k == "sleep" == kinds[i - 1])]
    assert kinds == ["put", "put", "sleep", "update", "put", "update", "put"]
    assert world.rows[a]["dm_writer"] == world.rows[b]["dm_writer"] == "iota"


def test_settle_seconds_is_used(world, sleeps):
    world.add("S1")
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--settle", "150", "--apply") == 0
    assert _waited(sleeps) == 150.0


@pytest.mark.parametrize("settle", ["120", "600"])
def test_settle_bounds_are_inclusive(world, sleeps, settle):
    world.add("S1")
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--settle", settle, "--apply") == 0
    assert _waited(sleeps) == float(settle)


@pytest.mark.parametrize("settle", ["nan", "inf", "-inf", "-1", "0", "59.9", "60", "119.9", "600.1"])
def test_settle_out_of_range_exits_2(world, sleeps, settle):
    world.add("S1")
    for mode in ("--apply", "--dry-run"):
        with pytest.raises(SystemExit) as e:
            run(*BASE, "--to", "iota", "--shape", "legacy", "--settle", settle, mode)
        assert e.value.code == 2
    assert sleeps == [] and world.puts == 0 and not world.events("lock")


def test_no_settle_flag_is_gone(world, sleeps):
    world.add("S1")
    for extra in ([], ["--settle", "90"]):
        with pytest.raises(SystemExit) as e:
            run(*BASE, "--to", "iota", "--shape", "legacy", "--no-settle", *extra, "--apply")
        assert e.value.code == 2
    assert sleeps == [] and world.puts == 0 and not world.events("lock")


@pytest.mark.parametrize("mode", ["--apply", "--dry-run"])
def test_settle_with_to_em_exits_2(world, mode):
    world.add("S1", writer="iota", shape="legacy")
    world.settle_agent()
    with pytest.raises(SystemExit) as e:
        run(*BASE, "--to", "em", "--settle", "150", mode)
    assert e.value.code == 2
    assert world.puts == 0 and not world.events("lock")


def test_to_em_needs_no_settle(world, sleeps):
    urn = world.add("S1", writer="iota", shape="legacy")
    world.settle_agent()
    assert run(*BASE, "--to", "em", "--apply") == 0
    assert sleeps == [] and world.rows[urn]["dm_writer"] == "em"


def test_to_em_does_not_wait(world, sleeps):
    world.add("S1", writer="iota", shape="legacy")
    world.settle_agent()
    assert run(*BASE, "--to", "em", "--apply") == 0
    assert sleeps == []


def test_dry_run_does_not_wait_and_says_it_would(world, sleeps, capsys):
    world.add("S1")
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--dry-run") == 0
    assert sleeps == [] and "would wait 120 s" in capsys.readouterr().out


def test_device_whose_first_put_failed_is_left_out_and_listed(world, sleeps, capsys):
    a, b = world.add("S1"), world.add("S2")
    world.put_fail = lambda n, ext: ext == "S1"
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert world.rows[a]["dm_writer"] == "em" and world.rows[b]["dm_writer"] == "iota"
    assert _waited(sleeps) == 120.0 and a in capsys.readouterr().out
    assert [e for e in world.events("update") if e[1] == a] == []


def test_no_wait_when_no_first_put_succeeded(world, sleeps):
    world.add("S1")
    world.put_fail = lambda n, ext: True
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert sleeps == []


def test_first_put_failure_tries_to_restore_and_says_state_unknown(world, capsys):
    urn = world.add("S1")
    world.settle_agent()
    before = copy.deepcopy(world.agent["S1"])
    world.put_fail = lambda n, ext: n == 1
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert "state unknown, rerun" in capsys.readouterr().out
    assert world.agent["S1"] == before and world.rows[urn]["dm_writer"] == "em"


def test_profile_change_during_the_settle_is_picked_up_by_the_second_put(world):
    urn = world.add("S1")

    def change(seconds):
        world.profiles["p"] = copy.deepcopy(ONE_MS)

    world.on_sleep = change
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    assert world.agent["S1"] == dataloggers._attributes_for_state(
        ("iota", "legacy"), TENANT, urn, "S1", "p", ONE_MS)


def test_crash_during_the_settle_is_undone_and_the_rerun_moves(world):
    """An unexpected error ends in the final undo; nothing escapes."""
    urn = world.add("S1")
    world.settle_agent()
    before = _snapshot(world)

    def crash(seconds):
        raise RuntimeError("killed")

    world.on_sleep = crash
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert world.rows[urn]["dm_writer"] == "em" and world.agent == before
    world.on_sleep = None
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    assert world.rows[urn]["dm_writer"] == "iota"
    assert world.agent["S1"] == dataloggers._attributes_for_state(
        ("iota", "legacy"), TENANT, urn, "S1", "p", MS)


def test_interrupt_during_the_settle_restores_every_uncommitted_device(world, capsys):
    a, b = world.add("S1"), world.add("S2")
    world.settle_agent()
    before = {k: copy.deepcopy(v) for k, v in world.agent.items()}

    def interrupt(seconds):
        os.kill(os.getpid(), signal.SIGINT)

    world.on_sleep = interrupt
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert world.rows[a]["dm_writer"] == world.rows[b]["dm_writer"] == "em"
    assert world.agent == before
    out = capsys.readouterr().out
    assert a in out and b in out and "interrupted" in out


def test_interrupt_restore_failure_is_critical(world, caplog):
    urn = world.add("S1")

    def interrupt(seconds):
        world.put_fail = lambda n, ext: True
        os.kill(os.getpid(), signal.SIGINT)

    world.on_sleep = interrupt
    with caplog.at_level(logging.CRITICAL):
        assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert any(urn in r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL)


# -- verify, fix wave ---------------------------------------------------------------------

def test_verify_fails_when_the_agent_mappings_differ(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature")]))
    world.agent["S1"][0] = dict(world.agent["S1"][0], expression="x * 2")
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    assert "check (c): FAILED" in out and urn_of("S1") in out
    assert "check (b): ok" in out and "check (d): ok" in out


def test_verify_fails_when_the_agent_document_is_unreadable(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature")]))
    world.get_fail = True
    assert run(*BASE, *VERIFY) == 1
    assert "check (c): FAILED" in capsys.readouterr().out


def test_verify_requires_observed_at_on_the_values(world, http, capsys):
    iota_world(world)
    d = dm("S1", "airTemperature")
    d[DEV + "numValue"] = {"type": "Property", "value": 1, "modifiedAt": NEW}
    http(Http([d]))
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    assert "check (d): FAILED" in out and d["id"] in out and "check (c): ok" in out


def test_verify_checks_c_and_d_are_not_run_without_iota_devices(world, http, capsys):
    world.add("S1", writer="em")
    http(Http([dm("S1", "airTemperature")]))
    assert run(*BASE, *VERIFY) == 0
    out = capsys.readouterr().out
    assert "check (c): NOT RUN" in out and "check (d): NOT RUN" in out
    assert out.splitlines()[-1] == "verify: OK (no iota devices: only check (a) ran)"


def test_verify_device_not_on_iota_is_an_explicit_failure(world, http, capsys):
    world.add("S1", writer="em")
    http(Http([dm("S1", "airTemperature")]))
    assert run(*BASE, "--device", urn_of("S1"), *VERIFY) == 1
    out = capsys.readouterr().out
    assert "not on iota" in out and "NOT RUN" not in out


def test_verify_unknown_device_exits_1(world, http, capsys):
    iota_world(world)
    http(Http([]))
    assert run(*BASE, "--device", urn_of("nope"), *VERIFY) == 1
    assert "not linked" in capsys.readouterr().out


# -- verify --since -----------------------------------------------------------------------

def _since_fails(argv):
    with pytest.raises(SystemExit) as e:
        run(*BASE, *argv)
    assert e.value.code == 2


@pytest.fixture
def clock(monkeypatch):
    """The command's clock: a list of instants, each call returns the next (the last repeats)."""
    from datetime import datetime, timezone
    state = SimpleNamespace(times=[datetime(2026, 11, 25, 10, 0, 0, tzinfo=timezone.utc)], calls=0)

    def now():
        i = min(state.calls, len(state.times) - 1)
        state.calls += 1
        return state.times[i]

    monkeypatch.setattr(cut, "_utcnow", now)
    return state


def test_verify_needs_since(world):
    _since_fails(["--verify"])
    assert not world.events("read_rows")


@pytest.mark.parametrize("since", [
    "2026-06-01T00:00:00",          # naive
    "2026-06-01",                   # date only
    "2999-01-01T00:00:00Z",         # future
    "2026-11-25T10:00:06Z",         # 6 s ahead of the clock: beyond the 5 s margin
    "yesterday", "", "2026-13-45T00:00:00Z",
])
def test_bad_since_exits_2(world, clock, since):
    _since_fails(["--verify", "--since", since])
    assert not world.events("read_rows")


def test_since_accepts_an_offset_and_a_few_seconds_of_clock_skew(world, http, clock):
    iota_world(world)
    http(Http([dm("S1", "airTemperature")]))
    assert run(*BASE, "--verify", "--since", "2026-06-01T02:00:00+02:00") == 0
    assert run(*BASE, "--verify", "--since", "2026-11-25T10:00:05Z") == 1   # in the margin: parsed


@pytest.mark.parametrize("argv", [
    ["--verify", "--since", SINCE, "--apply", "--to", "em"],
    ["--verify", "--since", SINCE, "--dry-run", "--to", "em"],
    ["--verify", "--since", SINCE, "--settle", "60"],
    ["--verify", "--since", SINCE, "--shape", "sdm"],
    ["--verify", "--since", SINCE, "--to", "iota"],
    ["--since", SINCE, "--to", "em", "--dry-run"],      # --since without --verify
    ["--since", SINCE, "--to", "em", "--apply"],
])
def test_verify_with_apply_is_refused(world, argv):
    world.add("S1", writer="iota", shape="legacy")
    _since_fails(argv)
    assert world.puts == 0 and not world.events("lock") and not world.events("update")


def _two_devices(world):
    world.add("S1")
    world.add("S2")


def test_apply_prints_the_verify_line(world, capsys, clock):
    from datetime import datetime, timezone
    clock.times = [datetime(2026, 11, 25, 10, 0, 7, 300000, tzinfo=timezone.utc)]
    world.add("S1")
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == (f"verify in a few minutes with: --tenant {TENANT} --verify "
                       "--since 2026-11-25T10:00:08Z")


def test_apply_verify_line_whole_second_is_not_pushed_on(world, capsys, clock):
    from datetime import datetime, timezone
    clock.times = [datetime(2026, 11, 25, 10, 0, 7, tzinfo=timezone.utc)]
    world.add("S1")
    run(*BASE, "--to", "iota", "--shape", "legacy", "--apply")
    assert capsys.readouterr().out.splitlines()[-1].endswith("--since 2026-11-25T10:00:07Z")


def test_apply_verify_line_uses_the_time_of_the_last_moved_outcome(world, capsys, clock):
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 11, 25, 10, 0, 0, tzinfo=timezone.utc)
    clock.times = [t0 + timedelta(seconds=10 * i) for i in range(1, 8)]
    _two_devices(world)
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    assert capsys.readouterr().out.splitlines()[-1].endswith("--since 2026-11-25T10:00:20Z")
    assert clock.calls == 2          # one reading per moved outcome, none afterwards


def test_apply_verify_line_names_the_device_when_given(world, capsys, clock):
    world.add("S1")
    run(*BASE, "--device", urn_of("S1"), "--to", "iota", "--shape", "legacy", "--apply")
    assert capsys.readouterr().out.splitlines()[-1].startswith(
        f"verify in a few minutes with: --tenant {TENANT} --device {urn_of('S1')} --verify --since ")


def test_apply_prints_no_verify_line_when_nothing_moved(world, capsys, clock):
    world.add("S1")
    world.put_fail = lambda n, ext: True
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert "verify in a few minutes" not in capsys.readouterr().out
    world.put_fail = lambda n, ext: False
    world.add("S2", writer="iota", shape="legacy")
    world.settle_agent()
    assert run(*BASE, "--device", urn_of("S2"), "--to", "em", "--apply") == 0
    assert "verify in a few minutes" not in capsys.readouterr().out


def test_apply_prints_the_line_when_only_some_devices_moved(world, capsys, clock):
    _two_devices(world)
    world.put_fail = lambda n, ext: ext == "S2"
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert "verify in a few minutes with:" in capsys.readouterr().out


def test_channel_not_reported_yet_is_listed_not_failed(world, http, capsys):
    iota_world(world, ms=MS)
    old = dm("S1", "relativeHumidity", modified=OLD,
             extra={DEV + "weird": {"type": "Property", "value": 1}})     # would fail (b)
    del old["https://smartdatamodels.org/dateObserved"]                   # and lacks a key
    http(Http([dm("S1", "airTemperature"), old]))
    assert run(*BASE, *VERIFY) == 0
    out = capsys.readouterr().out
    assert f"not reported since {SINCE}" in out and old["id"] in out
    assert "check (b): ok" in out and out.splitlines()[-1] == "verify: OK"


def test_a_channel_with_no_entity_in_the_broker_is_listed_not_failed(world, http, capsys):
    """A channel of the profile that never reported has no entity at all: the verification names it."""
    iota_world(world, ms=MS)
    http(Http([dm("S1", "airTemperature")]))
    assert run(*BASE, *VERIFY) == 0
    out = capsys.readouterr().out
    missing = f"urn:ngsi-ld:DeviceMeasurement:{TENANT}:S1:relativeHumidity"
    assert "1 channel(s) of iota devices with no entity in the broker yet" in out
    assert f"{missing}: no entity yet" in out
    assert f"{TENANT}:S1:airTemperature: no entity yet" not in out
    assert out.splitlines()[-1] == "verify: OK"


def test_no_channel_listing_when_every_channel_has_an_entity(world, http, capsys):
    iota_world(world, ms=MS)
    http(Http([dm("S1", "airTemperature"), dm("S1", "relativeHumidity")]))
    assert run(*BASE, *VERIFY) == 0
    assert "no entity in the broker yet" not in capsys.readouterr().out


def test_not_reported_entities_are_still_judged_by_a(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature"), dm("S1", "gone", modified=OLD, value="none")]))
    assert run(*BASE, *VERIFY) == 1
    assert "check (a): FAILED" in capsys.readouterr().out


def test_device_that_delivered_nothing_fails(world, http, capsys):
    world.add("S1", writer="iota", shape="legacy", code="p", ms=ONE_MS)
    world.add("S2", writer="iota", shape="legacy", code="p", ms=ONE_MS)
    http(Http([dm("S1", "airTemperature"), dm("S2", "airTemperature", modified=OLD)]))
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    assert f"{urn_of('S2')}: delivered NOTHING since {SINCE}" in out
    assert urn_of("S1") + ": delivered" not in out
    assert out.splitlines()[-1].startswith("verify: FAILED")


def test_a_reported_orphan_counts_as_delivered(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", modified=OLD), dm("S1", "gone", mtype=None)]))
    assert run(*BASE, *VERIFY) == 0
    assert "delivered NOTHING" not in capsys.readouterr().out


def test_reported_uses_modified_at_not_date_observed(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", date_observed=OLD, modified=NEW,
                  extra={DEV + "weird": {"type": "Property", "value": 1}})]))
    assert run(*BASE, *VERIFY) == 1                     # reported: judged, and it fails (b)
    assert "not reported" not in capsys.readouterr().out
    http(Http([dm("S1", "airTemperature", date_observed=NEW, modified=OLD)]))
    assert run(*BASE, *VERIFY) == 1                     # a fresh dateObserved does not report it
    out = capsys.readouterr().out
    assert "delivered NOTHING" in out


def test_reported_at_exactly_since_counts(world, http):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", modified=SINCE,
                  extra={DEV + "weird": {"type": "Property", "value": 1}})]))
    assert run(*BASE, *VERIFY) == 1


RT = [{"type": "x", "sdmAttribute": "airTemperature"}]


def test_stale_other_value_is_ignored_and_listed(world, http, capsys):
    iota_world(world, ms=RT)
    http(Http([dm("S1", "airTemperature", mtype="x", value="both",
                  value_modified={"textValue": OLD})]))
    assert run(*BASE, *VERIFY) == 0
    out = capsys.readouterr().out
    assert "stale value" in out and "textValue" in out


def test_stale_value_is_left_out_of_d_but_the_fresh_one_is_judged(world, http, capsys):
    iota_world(world, ms=RT)
    d = dm("S1", "airTemperature", mtype="x", value="both", value_modified={"textValue": OLD})
    del d[DEV + "textValue"]["observedAt"]                       # the stale one: ignored
    http(Http([d]))
    assert run(*BASE, *VERIFY) == 0
    del d[DEV + "numValue"]["observedAt"]                        # the fresh one: judged
    http(Http([d]))
    assert run(*BASE, *VERIFY) == 1
    assert "check (d): FAILED (1 value(s))" in capsys.readouterr().out


def test_both_fresh_values_are_judged_as_today(world, http):
    iota_world(world, ms=RT)
    fresh = dm("S1", "airTemperature", mtype="x", value="both")
    fresh[DEV + "textValue"].pop("observedAt")
    http(Http([fresh]))
    assert run(*BASE, *VERIFY) == 1


def test_both_old_values_mean_value_not_updated(world, http, capsys):
    iota_world(world, ms=RT)
    http(Http([dm("S1", "airTemperature", mtype="x", value="both",
                  value_modified={"numValue": OLD, "textValue": OLD})]))
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    assert "value not updated since" in out and "delivered NOTHING" in out


def test_an_old_text_value_on_a_numeric_channel_is_listed_not_failed(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", value="both", value_modified={"textValue": OLD})]))
    assert run(*BASE, *VERIFY) == 0
    assert "stale value" in capsys.readouterr().out
    http(Http([dm("S1", "airTemperature", value="both")]))        # a NEW one is a failure
    assert run(*BASE, *VERIFY) == 1


def test_a_stale_value_of_an_orphan_is_left_out(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature"),
               dm("S1", "gone", mtype=None, value="both", value_modified={"numValue": OLD})]))
    assert run(*BASE, *VERIFY) == 0
    assert "stale value" in capsys.readouterr().out


def test_a_stale_attribute_of_another_kind_is_still_judged_by_b(world, http):
    iota_world(world)
    stale = {DEV + "weird": {"type": "Property", "value": 1, "modifiedAt": OLD}}
    http(Http([dm("S1", "airTemperature", extra=stale)]))
    assert run(*BASE, *VERIFY) == 1


def test_removed_channel_is_an_orphan_not_a_failure(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature"),
               dm("S1", "gone", modified=OLD, extra={DEV + "weird": {"type": "Property", "value": 1}})]))
    assert run(*BASE, *VERIFY) == 0
    out = capsys.readouterr().out
    assert "orphan" in out and "urn:ngsi-ld:DeviceMeasurement:" + TENANT + ":S1:gone" in out


@pytest.mark.parametrize("value, term", [("num", "numValue"), ("text", "textValue")])
def test_reported_value_without_observed_at_fails_d(world, http, capsys, value, term):
    iota_world(world, ms=RT)
    d = dm("S1", "airTemperature", mtype="x", value=value)
    del d[DEV + term]["observedAt"]
    http(Http([d]))
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    assert "check (d): FAILED" in out and d["id"] in out and term in out


def test_not_reported_value_without_observed_at_is_not_judged(world, http):
    iota_world(world, ms=ONE_MS + [{"type": "u", "incomingKey": "r2", "sdmAttribute": "relativeHumidity",
                                    "unit": "%"}])
    d = dm("S1", "relativeHumidity", mtype="u", modified=OLD)
    del d[DEV + "numValue"]["observedAt"]
    http(Http([dm("S1", "airTemperature"), d]))
    assert run(*BASE, *VERIFY) == 0


@pytest.mark.parametrize("modified", [None, "not a time", "2026-06-02T00:00:00", 5])
def test_entity_without_modified_at_fails(world, http, capsys, modified):
    iota_world(world)
    d = dm("S1", "airTemperature", modified=None)
    if modified is not None:
        d["modifiedAt"] = modified
    http(Http([d]))
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    assert d["id"] in out and "modifiedAt" in out
    assert "not reported" not in out.split("check (a)")[-1]
    assert "delivered NOTHING" not in out


def test_check_a_runs_for_a_device_not_on_iota(world, http, capsys):
    world.add("S1", writer="em")
    http(Http([dm("S1", "airTemperature")]))
    assert run(*BASE, "--device", urn_of("S1"), *VERIFY) == 1
    out = capsys.readouterr().out
    assert "not on iota" in out and "check (a): ok" in out and "NOT RUN" not in out
    http(Http([dm("S1", "airTemperature", value="none")]))
    assert run(*BASE, "--device", urn_of("S1"), *VERIFY) == 1
    assert "check (a): FAILED" in capsys.readouterr().out


def test_verify_reads_with_sys_attrs(world, http):
    iota_world(world)
    h = http(Http([dm("S1", "airTemperature")]))
    assert run(*BASE, *VERIFY) == 0
    assert h.orion_calls()[0][2]["options"] == "sysAttrs"


def test_listed_entities_show_the_three_times(world, http, capsys):
    iota_world(world)
    d = dm("S1", "airTemperature", modified=OLD, date_observed="2026-04-01T00:00:00Z",
           observed_at="2026-04-02T00:00:00Z")
    http(Http([d, dm("S1", "gone")]))
    assert run(*BASE, *VERIFY) == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines() if d["id"] in ln)
    assert "2026-04-01T00:00:00Z" in line and "2026-04-02T00:00:00Z" in line and OLD in line


def test_listings_are_capped_at_50_and_counts_stay_complete(world, http, capsys):
    iota_world(world)
    n = 60
    ok = dm("S1", "airTemperature")
    old = [dm("S1", f"o{i}", modified=OLD) for i in range(n)]       # orphans: not reported
    http(Http([ok] + old))
    assert run(*BASE, *VERIFY) == 0
    out = capsys.readouterr().out
    assert f"{n} orphan entities" in out
    assert sum(1 for ln in out.splitlines() if ":S1:o" in ln) == 50
    assert "\u2026 and 10 more" in out
    bad = [dm("S1", f"b{i}", extra={DEV + "weird": {"type": "Property", "value": 1}})
           for i in range(n)]
    http(Http([ok] + bad))
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    assert f"check (b): FAILED ({n} problem(s)" in out
    assert sum(1 for ln in out.splitlines() if "unexpected keys" in ln) == 50
    assert out.count("\u2026 and 10 more") == 2        # the orphan listing and the problems


def test_not_reported_listing_is_capped_too(world, http, capsys):
    ms = [{"type": f"t{i}", "incomingKey": f"r{i}", "sdmAttribute": "airTemperature", "unit": "°C"}
          for i in range(1)]
    iota_world(world, ms=ms)
    old = [dm("S1", "airTemperature", modified=OLD)] * 55         # same id: a listing of 55
    http(Http([dm("S1", "airTemperature")] + old))
    assert run(*BASE, *VERIFY) == 0
    out = capsys.readouterr().out
    assert "55 entities not reported since" in out and "\u2026 and 5 more" in out


def test_verify_last_line_names_what_failed(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", value="none")]))
    assert run(*BASE, *VERIFY) == 1
    last = capsys.readouterr().out.splitlines()[-1]
    assert last.startswith("verify: FAILED") and "(a)" in last


def test_verify_never_takes_the_lock_or_writes(world, http):
    iota_world(world)
    http(Http([dm("S1", "airTemperature")]))
    world.lock_busy = True
    assert run(*BASE, *VERIFY) == 0
    assert not world.events("lock") and not world.events("update") and world.puts == 0


# -- verify --since: only a new VALUE counts ---------------------------------

TWO = ONE_MS + [{"type": "u", "incomingKey": "r2", "sdmAttribute": "relativeHumidity", "unit": "%"}]
TEXT_MS = [{"type": "x", "sdmAttribute": "airTemperature", "valueType": "Text"}]
SECOND = "relativeHumidity"


def _fresh_and_not_updated(world, http, **old):
    """S1 has two channels: one delivered, one whose entity changed but whose value did not."""
    iota_world(world, ms=TWO)
    stale = dm("S1", SECOND, mtype="u", **old)
    http(Http([dm("S1", "airTemperature"), stale]))
    return stale


def test_old_num_value_with_a_new_entity_time_is_not_reported(world, http, capsys):
    stale = _fresh_and_not_updated(world, http, value_modified={"numValue": OLD})
    assert run(*BASE, *VERIFY) == 0                       # another channel delivered
    out = capsys.readouterr().out
    assert "value not updated since" in out and stale["id"] in out
    assert "delivered NOTHING" not in out and out.splitlines()[-1] == "verify: OK"


def test_every_channel_with_an_old_value_means_delivered_nothing(world, http, capsys):
    iota_world(world, ms=TWO)
    http(Http([dm("S1", "airTemperature", value_modified={"numValue": OLD}),
               dm("S1", SECOND, mtype="u", value_modified={"numValue": OLD})]))
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    assert f"{urn_of('S1')}: delivered NOTHING since {SINCE}" in out


def test_old_text_value_on_a_text_channel_is_not_reported(world, http, capsys):
    iota_world(world, ms=TEXT_MS)
    http(Http([dm("S1", "airTemperature", mtype="x", value="text", value_modified={"textValue": OLD})]))
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    assert "value not updated since" in out and "delivered NOTHING" in out


def test_old_value_of_an_orphan_with_a_new_entity_time_is_listed(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature"),
               dm("S1", "gone", mtype=None, value_modified={"numValue": OLD},
                  extra={DEV + "weird": {"type": "Property", "value": 1}})]))
    assert run(*BASE, *VERIFY) == 0
    out = capsys.readouterr().out
    assert "orphan" in out and "value not updated" in out


def test_number_channel_with_old_num_value_and_new_text_value_fails_b(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", value="both", value_modified={"numValue": OLD})]))
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    assert "check (b): FAILED" in out and DEV + "numValue" in out and "stale value numValue" in out


@pytest.mark.parametrize("term", ["numValue", "textValue"])
@pytest.mark.parametrize("bad", [None, "not a time", "2026-06-02T00:00:00", 5])
def test_unreadable_value_attribute_time_fails(world, http, capsys, term, bad):
    iota_world(world, ms=RT)
    d = dm("S1", "airTemperature", mtype="x", value="both")
    if bad is None:
        del d[DEV + term]["modifiedAt"]
    else:
        d[DEV + term]["modifiedAt"] = bad
    http(Http([d]))
    assert run(*BASE, *VERIFY) == 1
    out = capsys.readouterr().out
    assert d["id"] in out and f"{term} modifiedAt missing or unreadable" in out
    assert "delivered NOTHING" not in out


@pytest.mark.parametrize("key", [DEV + "controlledProperty", "https://smartdatamodels.org/dateObserved"])
def test_a_new_fixed_attribute_or_date_observed_alone_is_not_delivery(world, http, capsys, key):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", value_modified={"numValue": OLD},
                  extra={key: {"type": "Property", "value": 1, "modifiedAt": NEW}})]))
    assert run(*BASE, *VERIFY) == 1
    assert "delivered NOTHING" in capsys.readouterr().out


def test_a_new_value_counts_even_if_the_entity_time_is_old(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", modified=OLD, value_modified={"numValue": NEW},
                  extra={DEV + "weird": {"type": "Property", "value": 1}})]))
    assert run(*BASE, *VERIFY) == 1                       # reported, so judged (extra key fails)
    out = capsys.readouterr().out
    assert "unexpected keys" in out and "delivered NOTHING" not in out


@pytest.mark.parametrize("stamp, reported", [
    ("2026-06-01T00:00:00Z", True), ("2026-06-01T00:00:00.000Z", True),
    ("2026-06-01T00:00:00+00:00", True), ("2026-06-01T02:00:00+02:00", True),
    ("2026-06-01T00:00:00.000001Z", True), ("2026-05-31T23:59:59.999Z", False),
    ("2026-05-31T23:59:59Z", False)])
def test_value_time_equal_to_since_counts_in_every_format(world, http, capsys, stamp, reported):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", value_modified={"numValue": stamp})]))
    assert run(*BASE, *VERIFY) == (0 if reported else 1)
    assert ("delivered NOTHING" in capsys.readouterr().out) is (not reported)


# -- the move to iota is disabled ---------------------------------------------------------

def test_apply_to_iota_is_refused_by_default(world, monkeypatch, capsys):
    monkeypatch.setattr(cut, "MOVE_TO_IOTA_ENABLED", False)
    world.add("S1")
    with pytest.raises(SystemExit) as e:
        run(*BASE, "--to", "iota", "--shape", "legacy", "--apply")
    assert e.value.code == 2
    assert world.puts == 0 and not world.events("update")
    assert "disabled" in capsys.readouterr().err


def test_the_constant_is_off_in_the_source():
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(cut.__file__).read_text())
    values = [n.value.value for n in tree.body if isinstance(n, ast.Assign)
              and any(getattr(t, "id", None) == "MOVE_TO_IOTA_ENABLED" for t in n.targets)]
    assert values == [False]
    assert "disabled" in cut.__doc__


def test_dry_run_verify_and_to_em_keep_working_while_disabled(world, http, monkeypatch):
    monkeypatch.setattr(cut, "MOVE_TO_IOTA_ENABLED", False)
    world.add("S1")
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--dry-run") == 0
    world.add("S2", writer="iota", shape="legacy", code="p", ms=ONE_MS)
    world.settle_agent()
    assert run(*BASE, "--device", urn_of("S2"), "--to", "em", "--apply") == 0
    http(Http([]))
    assert run(*BASE, *VERIFY) == 0


# -- the lock -----------------------------------------------------------------------------

def test_the_command_locks_the_exclusive_lock_of_its_own_tenant(move_lock):
    """The seam the tests replace builds the real lock; the lock asks the server for the EXCLUSIVE
    lock on (class, tenant) and for nothing else."""
    lock = _REAL_EXCLUSIVE_LOCK("acme")
    assert isinstance(lock, wml.ExclusiveLock) and lock.tenant == "acme"
    with lock:
        pass
    assert move_lock.lock_queries() == [("exclusive", wml.LOCK_CLASS, "acme")]


def _idx(world, kind, nth=0):
    return [i for i, e in enumerate(world.log) if e[0] == kind][nth]


def test_the_plan_is_read_under_the_lock(world):
    world.add("S1")
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    assert world.events("lock") == [("lock", TENANT)]
    assert _idx(world, "lock") < _idx(world, "read_tenant") < _idx(world, "read_rows") < _idx(world, "orion")
    assert _idx(world, "orion") < _idx(world, "put")
    assert world.log[-1] == ("unlock",) and len(world.events("unlock")) == 1


def test_the_plan_of_a_move_to_em_is_read_under_the_lock(world):
    world.add("S1", writer="iota", shape="legacy")
    world.settle_agent()
    assert run(*BASE, "--to", "em", "--apply") == 0
    assert _idx(world, "lock") < _idx(world, "read_rows") < _idx(world, "orion")
    assert world.log[-1] == ("unlock",)


@pytest.mark.parametrize("direction", ["iota", "em"])
def test_rows_are_committed_on_the_lock_connection(world, direction):
    """The pool seam raises on any UPDATE: a row can only change through the lock."""
    if direction == "iota":
        urns = [world.add("S1"), world.add("S2")]
        argv = ("--to", "iota", "--shape", "sdm", "--apply")
    else:
        urns = [world.add("S1", writer="iota", shape="sdm"), world.add("S2", writer="iota", shape="sdm")]
        world.settle_agent()
        argv = ("--to", "em", "--apply")
    assert run(*BASE, *argv) == 0
    assert all(world.rows[u]["dm_writer"] == direction for u in urns)
    updates = [i for i, e in enumerate(world.log) if e[0] == "update"]
    assert len(updates) == 2
    assert _idx(world, "lock") < updates[0] and updates[-1] < _idx(world, "unlock")


@pytest.mark.parametrize("mode", ["busy", "unavailable"])
@pytest.mark.parametrize("argv", [("--to", "iota", "--shape", "legacy"), ("--to", "em")])
def test_busy_lock_writes_nothing_and_exits_1(world, capsys, mode, argv):
    world.add("S1", writer="iota" if argv[1] == "em" else "em", shape="legacy" if argv[1] == "em" else None)
    world.settle_agent()
    before = {u: dict(r) for u, r in world.rows.items()}
    setattr(world, "lock_busy" if mode == "busy" else "lock_unavailable", True)
    assert run(*BASE, *argv, "--apply") == 1
    out = capsys.readouterr().out
    assert "nothing was written" in out
    if mode == "busy":
        assert "profile change or another move is running" in out
    assert world.rows == before and world.puts == 0
    assert not world.events("update") and not world.events("read_rows") and not world.events("orion")


def test_dry_run_and_verify_take_no_lock(world, http):
    world.add("S1")
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--dry-run") == 0
    assert run(*BASE, "--to", "em", "--dry-run") == 0
    http(Http([]))
    assert run(*BASE, *VERIFY) == 0
    assert not world.events("lock") and not world.events("unlock")
    world.lock_busy = True            # a held lock must not stop the read-only modes either
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--dry-run") == 0
    assert run(*BASE, *VERIFY) == 0


def _interrupt_in_settle(world):
    def interrupt(seconds):
        os.kill(os.getpid(), signal.SIGINT)
    world.on_sleep = interrupt


def _crash_in_settle(world):
    def crash(seconds):
        raise RuntimeError("killed")
    world.on_sleep = crash


def _interrupt_in_planning(world, monkeypatch):
    def interrupt(tenant, urns):
        raise KeyboardInterrupt
    monkeypatch.setattr(dataloggers, "_orion_devices", interrupt)


@pytest.mark.parametrize("scenario", [
    "success", "validation-failure", "device-failure", "interrupt-in-settle",
    "crash-in-settle", "interrupt-in-planning", "to-em-success", "to-em-failure"])
def test_lock_is_released_after_success_failure_and_interrupt(world, monkeypatch, scenario, capsys):
    iota = ("--to", "iota", "--shape", "legacy", "--apply")
    world.add("S1")
    expect = None
    if scenario == "validation-failure":
        world.add("S2", entity=False)
    elif scenario == "device-failure":
        world.update_fail = True
    elif scenario == "interrupt-in-settle":
        _interrupt_in_settle(world)
    elif scenario == "crash-in-settle":
        _crash_in_settle(world)
    elif scenario == "interrupt-in-planning":
        _interrupt_in_planning(world, monkeypatch)
    elif scenario.startswith("to-em"):
        world.rows[urn_of("S1")].update(dm_writer="iota", dm_shape="legacy")
        world.settle_agent()
        world.update_fail = scenario == "to-em-failure"
        iota = ("--to", "em", "--apply")
    if expect:
        with pytest.raises(expect):
            run(*BASE, *iota)
    else:
        rc = run(*BASE, *iota)
        if scenario == "interrupt-in-planning":      # exit 1, not a traceback
            assert rc == 1 and "interrupted; nothing was written" in capsys.readouterr().out
            assert world.puts == 0
    assert len(world.events("lock")) == 1 and len(world.events("unlock")) == 1
    assert world.log[-1] == ("unlock",)


def test_lock_is_released_when_the_pool_read_of_the_rows_fails(world, monkeypatch):
    world.add("S1")

    def boom(tenant, urn=None):
        raise RuntimeError("pool down")

    monkeypatch.setattr(cut, "list_rows", boom)
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert world.log[-1] == ("unlock",) and world.puts == 0


def test_suspended_tenant_is_refused_only_for_iota(world, capsys):
    urn = world.add("S1")
    world.tenants[TENANT] = "2026-11-01T00:00:00Z"
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert "suspended" in capsys.readouterr().out
    assert world.puts == 0 and not world.events("update") and world.rows[urn]["dm_writer"] == "em"
    assert not world.events("read_rows") and world.log[-1] == ("unlock",)
    # the way back stays open
    world.rows[urn].update(dm_writer="iota", dm_shape="legacy")
    world.settle_agent()
    assert run(*BASE, "--to", "em", "--apply") == 0
    assert world.rows[urn]["dm_writer"] == "em"
    assert not world.events("read_tenant")[1:]       # only the iota run read it


def test_a_tenant_without_a_row_is_refused_for_iota(world, capsys):
    world.add("S1")
    world.tenants.clear()
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert "suspended" in capsys.readouterr().out
    assert world.puts == 0 and not world.events("update")


def test_an_unreadable_tenant_is_exit_1_with_nothing_written(world, capsys):
    world.add("S1")
    world.tenant_read_fail = True
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    assert "nothing was written" in capsys.readouterr().out
    assert world.puts == 0 and not world.events("update") and world.log[-1] == ("unlock",)


@pytest.mark.parametrize("tenant", ["Tenant-A", " tenant-a", "tenant_a", "tenant-a-", "", "!!!", "t"])
def test_non_canonical_tenant_exits_2(world, tenant):
    world.add("S1")
    for argv in (("--to", "iota", "--shape", "legacy", "--dry-run"), ("--to", "em", "--apply"),
                 ("--verify", "--since", SINCE)):
        with pytest.raises(SystemExit) as e:
            run("--tenant", tenant, *argv)
        assert e.value.code == 2
    assert world.puts == 0 and not world.events("lock") and not world.events("read_rows")


def test_to_em_is_allowed_while_the_constant_is_off(world, monkeypatch):
    monkeypatch.setattr(cut, "MOVE_TO_IOTA_ENABLED", False)
    urn = world.add("S1", writer="iota", shape="sdm")
    world.settle_agent()
    assert run(*BASE, "--to", "em", "--apply") == 0
    assert world.rows[urn]["dm_writer"] == "em"
    assert len(world.events("lock")) == 1 and world.log[-1] == ("unlock",)


def test_row_changed_since_the_plan_is_not_overwritten_to_iota(world, capsys):
    urn = world.add("S1")

    def someone_else(seconds):
        world.rows[urn].update(dm_writer="iota", dm_shape="sdm")

    world.on_sleep = someone_else
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 1
    out = capsys.readouterr().out
    assert "changed since the plan" in out and "nothing was committed" in out and urn in out
    assert world.rows[urn]["dm_writer"] == "iota" and world.rows[urn]["dm_shape"] == "sdm"
    assert not world.events("update")
    assert world.puts == 1            # the first PUT only: restoring would risk a gap


def test_row_changed_since_the_plan_is_not_overwritten_to_em(world, capsys):
    urn = world.add("S1", writer="iota", shape="legacy")
    world.settle_agent()

    def someone_else(u):
        world.rows[u].update(dm_writer="iota", dm_shape="sdm")

    world.before_commit = someone_else
    assert run(*BASE, "--to", "em", "--apply") == 1
    out = capsys.readouterr().out
    assert "changed since the plan" in out and "nothing was committed" in out
    assert world.rows[urn]["dm_shape"] == "sdm" and world.puts == 0 and not world.events("update")


def test_outcomes_are_printed_as_they_happen(world, monkeypatch):
    a, b = world.add("S1"), world.add("S2")
    seen = []
    real_say = cut._say

    def say(text):
        world.log.append(("say", text))
        seen.append(text)
        real_say(text)

    monkeypatch.setattr(cut, "_say", say)
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply") == 0
    lines = [i for i, e in enumerate(world.log) if e[0] == "say" and "moved" in e[1]]
    updates = [i for i, e in enumerate(world.log) if e[0] == "update"]
    assert len(lines) == 2 and len(updates) == 2
    assert updates[0] < lines[0] < updates[1] < lines[1]      # S1 is printed before S2 is committed
    assert seen[-2] == "done: 2 ok, 0 failed"      # the verify line follows it
    assert seen[-1].startswith("verify in a few minutes with: --tenant ")
    assert sum(1 for t in seen if t.startswith((a, b)) and "moved" in t) == 2    # once each, not repeated at the end


def test_to_em_outcomes_are_printed_as_they_happen(world, monkeypatch):
    world.add("S1", writer="iota", shape="legacy")
    world.add("S2", writer="iota", shape="legacy")
    world.settle_agent()
    real_say = cut._say
    monkeypatch.setattr(cut, "_say", lambda t: (world.log.append(("say", t)), real_say(t)))
    assert run(*BASE, "--to", "em", "--apply") == 0
    lines = [i for i, e in enumerate(world.log) if e[0] == "say" and "moved" in e[1]]
    updates = [i for i, e in enumerate(world.log) if e[0] == "update"]
    assert updates[0] < lines[0] < updates[1] < lines[1]


def test_apply_item_refuses_a_move_to_iota(world):
    world.add("S1")
    rows = cut.list_rows(TENANT)
    items = cut.build_plan(TENANT, rows, "iota", "legacy")
    with FakeLock(world, TENANT) as lock:
        with pytest.raises(AssertionError):
            cut.apply_item(lock, TENANT, items[0], "iota", "legacy")
    assert world.puts == 0 and not world.events("update")


def test_set_state_is_gone():
    assert not hasattr(cut, "set_state")


# -- undo and interruptions ----------------------------------------------------------

def _attrs(urn, ext, state, ms=None):
    return dataloggers._attributes_for_state(state, TENANT, urn, ext, "p", MS if ms is None else ms)


def _dm_only(attrs):
    return [a for a in attrs if a.get("entity_type") == "DeviceMeasurement"]


def _snapshot(world):
    return {k: copy.deepcopy(v) for k, v in world.agent.items()}


def _ki_on_put(world, n, after=False):
    def hook(k, ext):
        if k == n:
            os.kill(os.getpid(), signal.SIGINT)
    if after:
        world.after_put = hook
    else:
        world.before_put = hook


IOTA_SDM = ("--to", "iota", "--shape", "sdm", "--apply")
IOTA_LEGACY = ("--to", "iota", "--shape", "legacy", "--apply")


def test_interrupted_shape_move_puts_the_old_iota_shape_back_not_device_only(world, capsys):
    """A move between two iota shapes, stopped by a signal after the first PUTs and
    before the commits, ends with the OLD iota shape in the agent; and the same when the commit
    of the device failed with its outcome unknown and the row was not written."""
    a = world.add("S1", writer="iota", shape="legacy")
    b = world.add("S2", writer="iota", shape="legacy")
    world.settle_agent()
    old = {"S1": _attrs(a, "S1", ("iota", "legacy")), "S2": _attrs(b, "S2", ("iota", "legacy"))}
    assert _dm_only(old["S2"])
    world.on_sleep = lambda seconds: os.kill(os.getpid(), signal.SIGINT)
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.agent == old                                  # the exact old iota mappings
    assert all(world.rows[u]["dm_shape"] == "legacy" for u in (a, b))
    assert len(old["S2"]) > len(_dm_only(old["S2"]))            # not Device-only, not DM-only
    world.on_sleep = None
    world.inject = [("commit_before", 1, "signal"), ("commit_before", 1, "unknown")]
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.rows[a]["dm_shape"] == "sdm" and world.rows[b]["dm_shape"] == "legacy"
    assert world.agent["S2"] == old["S2"]
    assert world.agent["S1"] == _attrs(a, "S1", ("iota", "sdm"))
    assert b in capsys.readouterr().out


@pytest.mark.parametrize("after", [False, True], ids=["put-not-applied", "put-applied"])
def test_interrupt_in_the_first_put_loop_undoes_the_devices_already_put(world, capsys, after):
    urns = [world.add(f"S{i}") for i in (1, 2, 3)]
    world.settle_agent()
    before = _snapshot(world)
    _ki_on_put(world, 3, after=after)           # the third device is in flight
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.agent == before                # in-flight one included
    assert all(world.rows[u]["dm_writer"] == "em" for u in urns)
    assert not world.events("update") and world.puts == 6
    out = capsys.readouterr().out
    assert all(u in out for u in urns) and "interrupted" in out


def test_devices_never_touched_are_listed_not_started(world, capsys):
    urns = [world.add(f"S{i}") for i in (1, 2, 3)]
    world.settle_agent()
    before = _snapshot(world)
    _ki_on_put(world, 2, after=True)
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.agent == before and world.puts == 4      # 2 first PUTs + 2 restores
    lines = capsys.readouterr().out.splitlines()
    assert [ln for ln in lines if urns[2] in ln and "not started" in ln]
    assert sum(1 for ln in lines if "FAILED" in ln) == 3


def test_interrupt_while_committing_reads_the_row_committed(world, capsys):
    urn = world.add("S1")
    world.settle_agent()
    world.inject = [("commit_after", 0, "signal")]          # the row is written, then the interrupt
    assert run(*BASE, *IOTA_SDM) == 0
    assert world.rows[urn]["dm_writer"] == "iota"
    assert world.agent["S1"] == _attrs(urn, "S1", ("iota", "sdm"))
    assert "moved" in capsys.readouterr().out and world.puts == 2


def test_interrupt_while_committing_reads_the_row_not_committed(world, capsys):
    urn = world.add("S1")
    world.settle_agent()
    before = _snapshot(world)
    world.inject = [("commit_before", 0, "signal"), ("commit_before", 0, "unknown")]
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.rows[urn]["dm_writer"] == "em" and world.agent == before
    assert "previous mappings restored" in capsys.readouterr().out
    assert ("read_state", urn) in world.log


def test_interrupt_while_committing_reads_the_row_unreadable(world, capsys, caplog):
    urn = world.add("S1")
    world.settle_agent()
    world.inject = [("commit_before", 0, "signal"), ("commit_before", 0, "unknown")]
    world.read_state_fail = True
    with caplog.at_level(logging.CRITICAL):
        assert run(*BASE, *IOTA_SDM) == 1
    assert world.puts == 1                                  # nothing put back
    assert _dm_only(world.agent["S1"])                      # the new mappings stay: no gap
    assert any(urn in r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL)
    assert "rerun" in capsys.readouterr().out


@pytest.fixture(autouse=True)
def sentinels():
    """Stand-ins for SIGINT/SIGTERM/SIGHUP: a missing handler records a hit instead of killing pytest."""
    hits = []

    def sentinel(signum, frame):
        hits.append(signum)

    old = {s: signal.signal(s, sentinel) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    yield SimpleNamespace(hits=hits, handler=sentinel)
    for s, h in old.items():
        signal.signal(s, h)


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_sigterm_behaves_like_ctrl_c(world, sentinels, capsys, sig):
    urns = [world.add("S1"), world.add("S2")]
    world.settle_agent()
    before = _snapshot(world)
    world.on_sleep = lambda seconds: os.kill(os.getpid(), sig)
    assert run(*BASE, *IOTA_SDM) == 1
    assert sentinels.hits == []                       # our handler took it, not the stand-in
    assert world.agent == before and all(world.rows[u]["dm_writer"] == "em" for u in urns)
    assert "interrupted" in capsys.readouterr().out
    assert world.log[-1] == ("unlock",)


class _FragileStdout:
    """Not re-entrant, like a real buffered stdout: a write inside a write raises. Can fire a
    signal in the middle of the first write that carries every string of `needle`."""

    def __init__(self, needle=None, sig=signal.SIGINT):
        self.needle, self.sig, self.busy, self.fired, self.text = needle, sig, False, False, []

    def write(self, text):
        if self.busy:
            raise RuntimeError("reentrant call to stdout")
        self.busy = True
        try:
            self.text.append(text)
            if self.needle and all(n in text for n in self.needle) and not self.fired:
                self.fired = True
                os.kill(os.getpid(), self.sig)
        finally:
            self.busy = False
        return len(text)

    def flush(self):
        pass


def test_second_signal_does_not_stop_the_undo(world, sentinels, capfd, monkeypatch):
    urns = [world.add(f"S{i}") for i in (1, 2, 3)]
    world.settle_agent()
    before = _snapshot(world)
    out = _FragileStdout(needle=("S2", "FAILED"))     # a signal while the outcome of S2 is printed
    monkeypatch.setattr(sys, "stdout", out)
    world.on_sleep = lambda seconds: os.kill(os.getpid(), signal.SIGINT)      # the first one

    def second(n, ext):
        if n == 5:                                                              # the 2nd restore PUT
            os.kill(os.getpid(), signal.SIGTERM)

    world.before_put = second
    assert run(*BASE, *IOTA_SDM) == 1
    assert sentinels.hits == [] and out.fired
    assert world.agent == before                         # all three restored
    assert world.puts == 6
    err = capfd.readouterr().err
    assert err.count("stopping after the current step; please wait") == 3
    assert not any("stopping after" in t for t in out.text)     # stdout is not used by the handler
    assert all(world.rows[u]["dm_writer"] == "em" for u in urns)


def test_a_signal_in_a_restore_put_lets_it_finish_and_stops_the_batch(world, sentinels, capfd):
    world.add("S1")
    world.add("S2")
    world.settle_agent()
    before = _snapshot(world)
    world.update_fail = True              # the commit of S1 fails: an ordinary undo of that device

    def kill_in_restore(n, ext):
        if n == 3:                         # S1's restore PUT (S1, S2 first PUTs are 1 and 2)
            os.kill(os.getpid(), signal.SIGINT)

    world.before_put = kill_in_restore
    assert run(*BASE, *IOTA_SDM) == 1
    assert sentinels.hits == []
    assert "stopping after the current step; please wait" in capfd.readouterr().err
    assert world.agent == before
    assert world.puts == 4                 # exactly one restore PUT per device
    assert world.counts.get("commit_before", 0) == 1       # S2 was never committed


def test_the_handler_never_raises_and_only_sets_a_flag(sentinels, capfd):
    with cut._Signals() as sg:
        assert not sg.stop_requested()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGINT):
            os.kill(os.getpid(), sig)                  # no exception, whatever the number
        assert sg.stop_requested()
    assert sentinels.hits == []
    assert capfd.readouterr().err.count("stopping after the current step; please wait") == 3


def test_the_handler_writes_nothing_to_stdout(sentinels, capfd, monkeypatch):
    out = _FragileStdout(needle=("x",))
    monkeypatch.setattr(sys, "stdout", out)
    with cut._Signals():
        os.kill(os.getpid(), signal.SIGTERM)
    assert out.text == []


def test_a_signal_before_the_handler_is_installed_and_after_it_is_restored_is_the_previous_one(sentinels):
    os.kill(os.getpid(), signal.SIGINT)
    with cut._Signals():
        pass
    os.kill(os.getpid(), signal.SIGINT)
    assert sentinels.hits == [signal.SIGINT, signal.SIGINT]


def test_handlers_are_restored_on_exit(world, sentinels):
    world.add("S1")
    world.settle_agent()
    world.on_sleep = lambda seconds: os.kill(os.getpid(), signal.SIGINT)
    for argv in (IOTA_SDM, IOTA_SDM):
        assert run(*BASE, *argv) == 1
        assert signal.getsignal(signal.SIGINT) is sentinels.handler
        assert signal.getsignal(signal.SIGTERM) is sentinels.handler
        assert signal.getsignal(signal.SIGHUP) is sentinels.handler
    world.on_sleep = None
    assert run(*BASE, *IOTA_SDM) == 0
    assert signal.getsignal(signal.SIGINT) is sentinels.handler
    assert signal.getsignal(signal.SIGTERM) is sentinels.handler
    assert signal.getsignal(signal.SIGHUP) is sentinels.handler


def test_a_signal_during_the_plan_exits_1_and_releases_the_lock(world, sentinels, monkeypatch, capsys):
    """Before the apply phase a signal is the default one: the `__main__` handler raises."""
    world.add("S1")

    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)

    def kill(tenant, urns):
        os.kill(os.getpid(), signal.SIGTERM)
        return {}

    monkeypatch.setattr(dataloggers, "_orion_devices", kill)
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.puts == 0
    assert "interrupted; nothing was written" in capsys.readouterr().out
    assert world.log[-1] == ("unlock",)


def test_connection_lost_between_devices_stops_the_batch(world, capsys):
    """S1 commits; then the lock connection dies (whether the commit of S2 raised
    or the loss showed after S1). No later row is committed; the new lock only puts back."""
    urns = [world.add(f"S{i}") for i in (1, 2, 3)]
    world.settle_agent()
    before = _snapshot(world)
    world.inject = [("commit_after", 0, "lost")]
    assert run(*BASE, *IOTA_SDM) == 1
    assert [e[1] for e in world.events("update")] == [urns[0]]
    assert world.rows[urns[0]]["dm_writer"] == "iota"
    assert world.rows[urns[1]]["dm_writer"] == world.rows[urns[2]]["dm_writer"] == "em"
    assert world.agent["S2"] == before["S2"] and world.agent["S3"] == before["S3"]
    assert world.agent["S1"] == _attrs(urns[0], "S1", ("iota", "sdm"))     # finished under the new lock
    assert len(world.locks) == 2 and not any(lk.held for lk in world.locks)


def test_a_connection_error_in_the_commit_stops_the_batch(world):
    urns = [world.add(f"S{i}") for i in (1, 2, 3)]
    world.settle_agent()
    before = _snapshot(world)
    world.inject = [("commit_before", 1, "lost")]       # S2's commit finds a dead connection
    assert run(*BASE, *IOTA_SDM) == 1
    assert [e[1] for e in world.events("update")] == [urns[0]]      # S3 is never committed
    assert world.rows[urns[2]]["dm_writer"] == "em"
    assert world.agent["S2"] == before["S2"] and world.agent["S3"] == before["S3"]
    assert len(world.locks) == 2 and not any(lk.held for lk in world.locks)


def test_a_database_error_with_the_lock_alive_only_fails_that_device(world, capsys):
    """A statement timeout is an ordinary failure of that device; the batch goes on."""
    a, b = world.add("S1"), world.add("S2")
    world.settle_agent()
    before = _snapshot(world)
    world.inject = [("commit_before", 0, "db")]
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.rows[a]["dm_writer"] == "em" and world.agent["S1"] == before["S1"]
    assert world.rows[b]["dm_writer"] == "iota" and len(world.locks) == 1


def test_lock_lost_and_retaken_commits_nothing_more(world):
    a, b = world.add("S1"), world.add("S2")
    world.settle_agent()
    world.on_sleep = lambda seconds: world.lose_lock()
    assert run(*BASE, *IOTA_SDM) == 1
    assert not world.events("update")                   # the plan is no longer protected
    assert world.rows[a]["dm_writer"] == world.rows[b]["dm_writer"] == "em"
    assert len(world.locks) == 2


def test_the_lock_is_retaken_once_per_run(world, capsys):
    urns = [world.add(f"S{i}") for i in (1, 2)]
    world.settle_agent()

    def lose(seconds):
        world.lose_lock()

    world.on_sleep = lose
    state = {"restores": 0}

    def lose_again(n, ext):                 # the first restore PUT is done; the new lock dies too
        state["restores"] += 1
        if state["restores"] == 3:          # 2 first PUTs + the first restore
            world.lose_lock()

    world.after_put = lose_again
    assert run(*BASE, *IOTA_SDM) == 1
    assert len(world.locks) == 2            # never a third lock
    assert world.puts == 3                  # the second device got nothing put back
    out = capsys.readouterr().out
    assert "lock lost; the new mappings were left in the agent" in out and urns


def test_nothing_is_put_back_without_the_lock(world, capsys, caplog):
    urns = [world.add("S1"), world.add("S2")]
    world.settle_agent()
    world.retake_refused = True
    seen = {}

    def lose(seconds):
        seen["puts"] = world.puts
        world.lose_lock()

    world.on_sleep = lose
    with caplog.at_level(logging.CRITICAL):
        assert run(*BASE, *IOTA_SDM) == 1
    assert world.puts == seen["puts"] == 2               # zero PUTs after the loss
    out = capsys.readouterr().out
    assert out.count("lock lost; the new mappings were left in the agent (no gap, duplicates "
                     "possible); rerun") == 2
    assert all(u in out for u in urns)
    assert len([r for r in caplog.records if r.levelno == logging.CRITICAL]) >= 2
    assert all(_dm_only(world.agent[e]) for e in ("S1", "S2"))
    assert not any(lk.held for lk in world.locks)


def test_the_final_put_is_not_sent_without_the_lock(world, capsys):
    urn = world.add("S1")
    world.settle_agent()
    world.retake_refused = True
    world.inject = [("commit_after", 0, "lost")]        # committed, then the connection dies
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.rows[urn]["dm_writer"] == "iota" and world.puts == 1
    assert "row updated" in (out := capsys.readouterr().out)
    assert "final PUT not sent (lock lost)" in out and "rerun" in out


def test_the_lock_is_checked_right_before_the_restore_put(world):
    """The lock dies between the row read and the restore PUT: the row is read again through the
    new lock before anything is put back."""
    urn = world.add("S1")
    world.settle_agent()
    before = _snapshot(world)
    world.update_fail = True
    state = {"reads": 0}

    def die_after_first_read(u):
        state["reads"] += 1
        if state["reads"] == 1:
            world.lose_lock()

    world.after_read = die_after_first_read
    assert run(*BASE, *IOTA_SDM) == 1
    assert state["reads"] == 2 and world.agent["S1"] == before["S1"]
    assert world.puts == 2 and len(world.locks) == 2 and urn


def test_a_restore_put_is_not_sent_when_the_lock_dies_before_it_and_cannot_be_taken(world):
    world.add("S1")
    world.settle_agent()
    world.update_fail = True
    world.retake_refused = True
    world.after_read = lambda u: world.lose_lock()
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.puts == 1                         # the first PUT only
    assert _dm_only(world.agent["S1"])


def test_row_moved_by_someone_else_is_left_alone(world, capsys, caplog):
    urn = world.add("S1")
    world.settle_agent()

    def someone_else(seconds):
        world.rows[urn].update(dm_writer="iota", dm_shape="legacy")
        os.kill(os.getpid(), signal.SIGINT)

    world.on_sleep = someone_else
    with caplog.at_level(logging.CRITICAL):
        assert run(*BASE, *IOTA_SDM) == 1
    assert world.puts == 1                          # nothing restored, nothing finished
    assert world.rows[urn]["dm_shape"] == "legacy"
    assert "check the row" in capsys.readouterr().out
    assert any(urn in r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL)


def test_a_vanished_row_is_left_alone(world, capsys):
    urn = world.add("S1")
    world.settle_agent()

    def vanish(seconds):
        del world.rows[urn]
        os.kill(os.getpid(), signal.SIGINT)

    world.on_sleep = vanish
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.puts == 1 and "does not exist" in capsys.readouterr().out


def test_commit_that_went_through_is_finished_not_undone(world, capsys):
    urn = world.add("S1")
    world.settle_agent()
    world.commit_lies = True                          # COMMIT raised after the row was written
    assert run(*BASE, *IOTA_SDM) == 0
    assert world.rows[urn]["dm_writer"] == "iota" and world.puts == 2
    assert world.agent["S1"] == _attrs(urn, "S1", ("iota", "sdm"))
    assert "moved" in capsys.readouterr().out


def test_commit_followed_by_a_dead_connection_is_finished_under_the_new_lock(world):
    urn = world.add("S1")
    world.settle_agent()
    world.dead_after_commit = True
    world.inject = [("commit_after", 0, "lost")]
    assert run(*BASE, *IOTA_SDM) == 0
    assert world.rows[urn]["dm_writer"] == "iota" and world.puts == 2
    assert world.agent["S1"] == _attrs(urn, "S1", ("iota", "sdm"))


def test_restore_with_plan_time_mappings_says_so(world, capsys, caplog):
    world.add("S1")
    world.settle_agent()
    before = _snapshot(world)
    world.after_put = lambda n, ext: setattr(world, "orion_fail", True)
    world.update_fail = True
    with caplog.at_level(logging.WARNING):
        assert run(*BASE, *IOTA_SDM) == 1
    assert world.agent == before
    assert ("previous mappings restored from the plan (the profile could not be read again)"
            in capsys.readouterr().out)


def test_restore_with_a_fresh_profile_does_not_say_plan(world, capsys):
    world.add("S1")
    world.settle_agent()
    world.update_fail = True
    assert run(*BASE, *IOTA_SDM) == 1
    out = capsys.readouterr().out
    assert "previous mappings restored" in out and "from the plan" not in out


def test_first_put_that_raised_is_undone_under_the_lock(world, capsys):
    """The PUT applied and then raised (a timeout): the row says what to do, not the exception."""
    urn = world.add("S1")
    world.settle_agent()
    before = _snapshot(world)

    def applied_then_timeout(n, ext):
        if n == 1:
            raise _IotaError(504)

    world.after_put = applied_then_timeout
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.agent == before and ("read_state", urn) in world.log
    assert "state unknown, rerun" in capsys.readouterr().out


def test_first_put_that_raised_with_the_lock_lost_puts_nothing_back(world):
    world.add("S1")
    world.settle_agent()
    world.retake_refused = True

    def applied_then_timeout(n, ext):
        world.lose_lock()
        raise _IotaError(504)

    world.after_put = applied_then_timeout
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.puts == 1 and _dm_only(world.agent["S1"])


def test_an_unreadable_row_is_read_once_more_with_the_lock(world):
    world.add("S1")
    world.settle_agent()
    before = _snapshot(world)
    world.update_fail = True
    world.inject = [("read", 0, "db")]
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.agent == before and world.counts["read"] == 2


def test_a_row_that_stays_unreadable_is_left_alone(world, capsys):
    world.add("S1")
    world.settle_agent()
    world.update_fail = True
    world.read_state_fail = True
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.puts == 1 and "rerun" in capsys.readouterr().out


def test_apply_iota_refuses_a_settle_outside_the_range(world):
    world.add("S1")
    items = cut.build_plan(TENANT, cut.list_rows(TENANT), "iota", "sdm")
    for bad in (0, 59.9, 60, 119.9, 600.1, float("nan"), float("inf"), True, "60", None):
        with FakeLock(world, TENANT) as lock:
            with pytest.raises(ValueError):
                cut.apply_iota(lock, TENANT, items, "sdm", bad)
    assert world.puts == 0


def test_the_docstring_describes_the_undo_and_not_the_old_paragraph():
    doc = cut.__doc__
    assert "Undo (`_undo`)" in doc and "SIGINT, SIGTERM and SIGHUP" in doc
    assert "puts the Device-only mappings back on every device" not in doc
    assert doc.count("Order of the writes") == 1


# -- to em: nothing to undo -------------------------------------------------------------------

def test_to_em_interrupt_after_the_row_was_written_sends_the_put(world, capsys):
    a = world.add("S1", writer="iota", shape="legacy")
    b = world.add("S2", writer="iota", shape="legacy")
    world.settle_agent()
    before = _snapshot(world)
    world.inject = [("commit_after", 0, "signal")]
    assert run(*BASE, "--to", "em", "--apply") == 1
    assert world.rows[a]["dm_writer"] == "em"
    assert world.agent["S1"] == _attrs(a, "S1", ("em", None))
    assert world.rows[b]["dm_writer"] == "iota" and world.agent["S2"] == before["S2"]
    out = capsys.readouterr().out
    assert [ln for ln in out.splitlines() if b in ln and "not started" in ln]


def test_to_em_interrupt_before_the_row_is_written_leaves_the_agent_alone(world, capsys):
    a = world.add("S1", writer="iota", shape="legacy")
    world.add("S2", writer="iota", shape="legacy")
    world.settle_agent()
    before = _snapshot(world)
    world.inject = [("commit_before", 0, "signal"), ("commit_before", 0, "unknown")]
    assert run(*BASE, "--to", "em", "--apply") == 1
    assert world.agent == before and world.puts == 0 and world.rows[a]["dm_writer"] == "iota"
    out = capsys.readouterr().out
    assert "the agent was not touched" in out and "not started" in out


def test_to_em_put_is_not_sent_without_the_lock(world, capsys):
    a = world.add("S1", writer="iota", shape="legacy")
    world.settle_agent()
    world.retake_refused = True
    world.inject = [("commit_after", 0, "lost")]
    assert run(*BASE, "--to", "em", "--apply") == 1
    assert world.rows[a]["dm_writer"] == "em" and world.puts == 0
    assert "lock lost" in capsys.readouterr().out


def test_to_em_stops_after_a_lost_lock(world):
    a = world.add("S1", writer="iota", shape="legacy")
    b = world.add("S2", writer="iota", shape="legacy")
    world.settle_agent()
    world.inject = [("commit_after", 0, "lost")]
    assert run(*BASE, "--to", "em", "--apply") == 1
    assert world.rows[a]["dm_writer"] == "em" and world.rows[b]["dm_writer"] == "iota"
    assert [e[1] for e in world.events("update")] == [a]


def test_a_repair_put_is_not_sent_without_the_lock(world, capsys):
    urn = world.add("S1", writer="iota", shape="sdm")
    world.agent["S1"] = _attrs(urn, "S1", ("em", None))          # the agent differs: a repair
    items = cut.build_plan(TENANT, cut.list_rows(TENANT), "iota", "sdm")
    assert items[0].action == "repair"
    world.retake_refused = True
    with FakeLock(world, TENANT) as lock:
        world.lose_lock()
        out = cut.apply_item(lock, TENANT, items[0], "iota", "sdm")
    assert out.status == "failed" and "lock lost" in out.text and world.puts == 0


# -- the agent is checked before a move commits ------------------------------------------------

def _replace_the_first_mappings_during_the_wait(world, urn, device_registered_again=False):
    """What a profile change that lost its lock does during the wait: it writes the Device-only
    mappings of ITS profile into the agent. Or the device is deleted and registered again under the
    same external id: a new row in the same state, a Device-only agent device."""
    done = []

    def late(seconds):
        if done:
            return
        done.append(1)
        world.agent["S1"] = _attrs(urn, "S1", ("em", None), ONE_MS)
        if device_registered_again:
            world.rows[urn] = dict(device_urn=urn, external_id="S1", dm_writer="em", dm_shape=None)

    world.on_sleep = late


@pytest.mark.parametrize("again", [False, True], ids=["late-put", "device-registered-again"])
def test_first_mappings_replaced_during_the_wait_commit_nothing_and_are_put_back(world, capsys, again):
    urn = world.add("S1")
    world.settle_agent()
    _replace_the_first_mappings_during_the_wait(world, urn, again)
    assert run(*BASE, *IOTA_LEGACY) == 1
    out = capsys.readouterr().out
    assert world.rows[urn]["dm_writer"] == "em" and not world.events("update")
    assert "the agent no longer holds the first mappings" in out
    assert world.agent["S1"] == _attrs(urn, "S1", ("em", None))      # the previous mappings are back


def test_the_agent_is_read_after_the_wait_and_before_the_commit(world):
    urn = world.add("S1")
    world.settle_agent()
    reads = []
    world.before_get = lambda ext: reads.append(len(world.log))
    assert run(*BASE, *IOTA_LEGACY) == 0
    assert reads and world.log.index(("sleep", 0.5)) < reads[-1] <= world.log.index(("update", urn, "iota", "legacy"))


def test_a_slow_agent_does_not_stop_the_move(world):
    urn = world.add("S1")
    world.settle_agent()
    world.before_get = lambda ext: world.log.append(("slow_get", ext))
    assert run(*BASE, *IOTA_LEGACY) == 0
    assert world.rows[urn]["dm_writer"] == "iota"


def test_an_agent_document_that_cannot_be_read_commits_nothing(world, capsys):
    """404 (`get_fail`) and a read that raises are the same: the first mappings are not known to be there."""
    urn = world.add("S1")
    world.settle_agent()
    world.after_put = lambda n, ext: setattr(world, "get_fail", True)
    assert run(*BASE, *IOTA_LEGACY) == 1
    assert world.rows[urn]["dm_writer"] == "em" and not world.events("update")
    assert "the agent no longer holds the first mappings" in capsys.readouterr().out
    assert world.agent["S1"] == _attrs(urn, "S1", ("em", None))


def test_an_agent_read_that_raises_commits_nothing(world, capsys):
    urn = world.add("S1")
    world.settle_agent()
    world.inject = [("get", 0, "boom")]
    world.counts.clear()
    assert run(*BASE, *IOTA_LEGACY) == 1
    assert world.rows[urn]["dm_writer"] == "em" and not world.events("update")
    assert "the agent no longer holds the first mappings" in capsys.readouterr().out


def test_the_profile_changed_during_the_wait_is_still_a_match(world):
    """What the agent holds is compared with what THIS move sent, not with a fresh build."""
    urn = world.add("S1")
    world.settle_agent()
    world.on_sleep = lambda seconds: world.profiles.__setitem__("p", copy.deepcopy(ONE_MS))
    assert run(*BASE, *IOTA_LEGACY) == 0
    assert world.agent["S1"] == _attrs(urn, "S1", ("iota", "legacy"), ONE_MS)


def test_a_committed_row_whose_final_put_fails_says_the_agent_may_not_hold_the_mappings(world, capsys):
    urn = world.add("S1")
    world.settle_agent()
    world.put_fail = lambda n, ext: n == 2
    assert run(*BASE, *IOTA_LEGACY) == 1
    out = capsys.readouterr().out
    assert world.rows[urn]["dm_writer"] == "iota"
    assert "the agent may not hold the new mappings; rerun now" in out
    assert "first mappings stay" not in out


def test_a_committed_row_whose_final_put_is_not_sent_says_the_same(world, capsys):
    urn = world.add("S1")
    world.settle_agent()
    world.retake_refused = True
    world.inject = [("commit_after", 0, "lost")]
    assert run(*BASE, *IOTA_LEGACY) == 1
    out = capsys.readouterr().out
    assert "final PUT not sent (lock lost)" in out
    assert "the agent may not hold the new mappings; rerun now" in out


def test_a_committed_row_whose_profile_cannot_be_read_says_the_same(world, capsys):
    urn = world.add("S1")
    world.settle_agent()

    def profile_gone(n, ext):
        if ext == "S1" and n == 1:
            world.on_sleep = lambda seconds: world.profiles.pop("p", None)

    world.after_put = profile_gone
    assert run(*BASE, *IOTA_LEGACY) == 1
    out = capsys.readouterr().out
    assert "the agent may not hold the new mappings; rerun now" in out


def test_a_move_to_em_keeps_its_own_text_for_a_failed_put(world, capsys):
    world.add("S1", writer="iota", shape="legacy")
    world.settle_agent()
    world.put_fail = lambda n, ext: True
    assert run(*BASE, "--to", "em", "--apply") == 1
    out = capsys.readouterr().out
    assert "duplicate writes, no gap" in out and "may not hold the new mappings" not in out


# -- the tenant is checked again before the first commit ----------------------------------------

@pytest.mark.parametrize("how", ["suspended", "unreadable"])
def test_a_tenant_suspended_during_the_wait_commits_nothing(world, capsys, how):
    urn = world.add("S1")
    world.settle_agent()
    before = _snapshot(world)

    def during_the_wait(seconds):
        if how == "suspended":
            world.tenants[TENANT] = "2026-11-01T00:00:00Z"
        else:
            world.tenant_read_fail = True

    world.on_sleep = during_the_wait
    assert run(*BASE, *IOTA_LEGACY) == 1
    out = capsys.readouterr().out
    assert "tenant is suspended" in out
    assert world.rows[urn]["dm_writer"] == "em" and not world.events("update")
    assert world.agent == before                       # the first mappings were put back
    assert world.log[-1] == ("unlock",)


def test_the_tenant_is_read_once_before_the_plan_and_once_after_the_wait(world):
    world.add("S1")
    world.settle_agent()
    assert run(*BASE, *IOTA_LEGACY) == 0
    reads = [i for i, e in enumerate(world.log) if e[0] == "read_tenant"]
    waits = [i for i, e in enumerate(world.log) if e[0] == "sleep"]
    commits = [i for i, e in enumerate(world.log) if e[0] == "update"]
    assert len(reads) == 2 and reads[0] < waits[0] and waits[-1] < reads[1] < commits[0]


# -- every injection point (property) ---------------------------------------------------------

def assert_no_silent_device(world, items, outcomes, strict=True):
    """One outcome per device; no `iota` row whose agent lacks the DeviceMeasurement mappings of
    the row's shape; no `em` row with an empty agent; every lock closed.

    With `strict` False (a second loss, or a refused re-take) the design leaves the NEW mappings
    in the agent on purpose: an `iota` row may then hold the other shape's DeviceMeasurement
    mappings, but only when its outcome lists it as failed. A restore PUT that itself failed is
    reported as such ("could NOT be restored") and is accepted in both modes."""
    assert [o.urn for o in outcomes] == [it.row.urn for it in items]
    assert len({o.urn for o in outcomes}) == len(items)
    for it, out in zip(items, outcomes):
        row = world.rows[it.row.urn]
        state = (row["dm_writer"], row["dm_shape"])
        agent = world.agent[it.row.external_id]
        if state[0] == "iota":
            want = _dm_only(_attrs(it.row.urn, it.row.external_id, state, world.profiles["p"]))
            missing = [a for a in want if a not in agent]
            if missing and out.status == "failed" and "could NOT be restored" in out.text:
                # the restore PUT itself failed (a one-shot error): CRITICAL and listed, the agent
                # keeps the other shape's mappings, never Device-only
                assert _dm_only(agent), (it.row.external_id, state, out)
            elif missing:
                assert not strict, (it.row.external_id, state, out)
                assert _dm_only(agent) and out.status == "failed", (it.row.external_id, state, out)
                assert "rerun" in out.text, out
        else:
            assert any(a.get("entity_type", "Device") != "DeviceMeasurement" for a in agent), \
                (it.row.external_id, "agent has no Device mappings", out)
    assert not any(lk.held for lk in world.locks)


EFFECTS = {
    "put_before": ("signal", "lost", "boom"), "put_after": ("signal", "lost", "boom"),
    "sleep": ("signal", "lost", "boom"),
    "commit_before": ("signal", "lost", "db", "unknown", "boom"),
    "commit_after": ("signal", "lost", "unknown", "boom"),
    "read": ("signal", "lost", "db", "boom"),
    "print": ("signal", "lost", "pipe", "valerr", "boom"),
    "alive": ("signal", "boom"), "orion": ("signal", "boom"),
    "get": ("signal", "lost", "boom"),
}
CELLS = {"strict": 0, "weak": 0}


def _batch_run(world, monkeypatch, injects, refused=False):
    """Three devices: S1 em -> iota, S2 iota legacy -> iota sdm, S3 already iota sdm."""
    world.add("S1")
    world.add("S2", writer="iota", shape="legacy")
    world.add("S3", writer="iota", shape="sdm")
    world.settle_agent()
    items = cut.build_plan(TENANT, cut.list_rows(TENANT), "iota", "sdm")
    assert [it.action for it in items] == ["move", "move", "unchanged"]
    real_say = cut._say

    def say(text):
        world.tick("print")
        real_say(text)

    monkeypatch.setattr(cut, "_say", say)
    world.retake_refused = refused
    world.inject = list(injects)
    world.counts.clear()
    with FakeLock(world, TENANT) as lock:
        outcomes = cut.apply_iota(lock, TENANT, items, "sdm", 120.0)
    return items, outcomes


def _points(world, monkeypatch, base):
    _batch_run(world, monkeypatch, base)
    out = []
    for k, n in world.counts.items():
        if k not in EFFECTS:
            continue
        # the wait has one tick per half second: first, second, middle and last slice
        indices = sorted({0, 1, n // 2, n - 1}) if k == "sleep" else range(n)
        out += [(k, i) for i in indices if 0 <= i < n]
    return out


def _fresh(monkeypatch):
    """A new world wired like the `world` fixture (the property test builds many)."""
    w = World()

    @contextmanager
    def db(tenant):
        yield _Conn(w)

    def orion_devices(tenant, urns):
        return {u: {"id": u, "profileCode": {"type": "Property", "value": w.entities[u]}}
                for u in urns if u in w.entities and w.entities[u] is not None}

    iota = _fake_iota(w)
    monkeypatch.setattr(dataloggers, "get_db_connection_with_tenant", db)
    monkeypatch.setattr(cut, "get_db_connection_with_tenant", db)
    monkeypatch.setattr(dataloggers, "iot_agent", iota)
    monkeypatch.setattr(cut, "iot_agent", iota)
    def ticking_orion(tenant, urns):
        w.tick("orion")
        return orion_devices(tenant, urns)

    monkeypatch.setattr(dataloggers, "_orion_devices", ticking_orion)
    monkeypatch.setattr(dataloggers, "_profile_measurements",
                        lambda tenant, code: copy.deepcopy(w.profiles.get(code)))
    monkeypatch.setattr(cut, "_exclusive_lock", lambda tenant: FakeLock(w, tenant))
    monkeypatch.setattr(cut, "_sleep", lambda seconds: w.tick("sleep"))
    monkeypatch.setattr(cut, "_say", lambda text: None)
    return w


BASES = {
    "clean": [],
    "db-error-in-the-first-commit": [("commit_before", 0, "db")],
    "signal-in-the-wait": [("sleep", 3, "signal")],
    "signal-in-the-first-put": [("put_after", 0, "signal")],
    "lock-lost-in-the-wait": [("sleep", 3, "lost")],
    "print-fails": [("print", 0, "pipe")],
}


@pytest.mark.parametrize("base_name", list(BASES))
@pytest.mark.parametrize("refused", [False, True], ids=["retake-ok", "retake-refused"])
def test_no_injection_point_leaves_a_silent_device(monkeypatch, base_name, refused, sentinels):
    base = BASES[base_name]
    probe = _fresh(monkeypatch)
    points = _points(probe, monkeypatch, base)
    assert points, "the batch has no injection point"
    kinds = {k for k, _ in points}
    if base_name == "clean":
        assert {"put_before", "put_after", "sleep", "commit_before", "commit_after", "print",
                "alive", "orion", "get"} <= kinds
    cases = 0
    for kind, index in points:
        for effect in EFFECTS[kind]:
            injects = base + [(kind, index, effect)]
            world = _fresh(monkeypatch)
            items, outcomes = _batch_run(world, monkeypatch, injects, refused=refused)
            strict = world.losses == 0           # weak only where a loss really happened
            CELLS["strict" if strict else "weak"] += 1
            try:
                assert_no_silent_device(world, items, outcomes, strict=strict)
            except AssertionError as exc:
                raise AssertionError(f"{(base_name, refused, kind, index, effect)}: {exc}") from None
            assert sentinels.hits == []
            cases += 1
    assert cases >= 20
    print(f"property cells so far: {CELLS}")


# -- cooperative stopping -------------------------------------------------------

def test_the_wait_sums_exactly_the_settle_when_undisturbed(world, sleeps):
    world.add("S1")
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--settle", "135.25", "--apply") == 0
    assert _waited(sleeps) == 135.25 and len(sleeps) == 271 and sleeps[-1] == 0.25


@pytest.mark.parametrize("k", [0, 1, 7])
def test_the_wait_stops_within_one_slice_of_a_signal(world, sleeps, k):
    urn = world.add("S1")
    world.settle_agent()
    before = _snapshot(world)

    def maybe(seconds):
        if len(sleeps) == k + 1:
            os.kill(os.getpid(), signal.SIGTERM)

    world.on_sleep = maybe
    assert run(*BASE, *IOTA_SDM) == 1
    assert len(sleeps) == k + 1                       # the next slice is not slept
    assert world.agent == before and world.rows[urn]["dm_writer"] == "em"


def test_a_row_committed_while_a_signal_arrives_gets_its_final_put(world, capsys):
    a, b = world.add("S1"), world.add("S2")
    world.settle_agent()
    before = _snapshot(world)
    world.inject = [("commit_before", 0, "signal")]          # the signal lands inside S1's commit
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.rows[a]["dm_writer"] == "iota"
    assert world.agent["S1"] == _attrs(a, "S1", ("iota", "sdm"))      # the final PUT was sent
    assert world.rows[b]["dm_writer"] == "em" and world.agent["S2"] == before["S2"]
    assert len(world.events("update")) == 1


def test_a_signal_between_the_loop_and_the_final_undo_on_the_lost_lock_path(world, monkeypatch, capsys):
    """The window the raising handler could not close: the batch loop is over, the lock is lost."""
    urns = [world.add(f"S{i}") for i in (1, 2)]
    world.settle_agent()
    before = _snapshot(world)
    world.on_sleep = lambda seconds: world.lose_lock()
    real = cut._stop_reason

    def reason(signals, failure):
        os.kill(os.getpid(), signal.SIGINT)
        os.kill(os.getpid(), signal.SIGTERM)
        return real(signals, failure)

    monkeypatch.setattr(cut, "_stop_reason", reason)
    assert run(*BASE, *IOTA_SDM) == 1
    assert world.agent == before and all(world.rows[u]["dm_writer"] == "em" for u in urns)
    assert not world.events("update")


@pytest.mark.parametrize("exc", [BrokenPipeError("pipe"), ValueError("closed file"), RuntimeError("x")])
def test_printing_never_stops_the_batch(world, monkeypatch, exc):
    for i in (1, 2, 3):
        world.add(f"S{i}")
    world.settle_agent()
    items = cut.build_plan(TENANT, cut.list_rows(TENANT), "iota", "sdm")

    def broken(text):
        raise exc

    monkeypatch.setattr(cut, "_say", broken)
    with FakeLock(world, TENANT) as lock:
        outcomes = cut.apply_iota(lock, TENANT, items, "sdm", 120.0)
    assert [o.status for o in outcomes] == ["moved"] * 3
    assert all(world.rows[u]["dm_shape"] == "sdm" for u in world.rows)


@pytest.mark.parametrize("exc", [BrokenPipeError("pipe"), ValueError("closed file")])
def test_printing_never_stops_a_move_to_em(world, monkeypatch, exc):
    for i in (1, 2):
        world.add(f"S{i}", writer="iota", shape="legacy")
    world.settle_agent()
    items = cut.build_plan(TENANT, cut.list_rows(TENANT), "em", None)
    monkeypatch.setattr(cut, "_say", lambda text: (_ for _ in ()).throw(exc))
    with FakeLock(world, TENANT) as lock:
        outcomes = cut.apply_em(lock, TENANT, items)
    assert [o.status for o in outcomes] == ["moved", "moved"]


def test_the_lost_lock_is_closed_before_the_re_take(world):
    """The old session still holds the lock until it is closed: a re-take that comes first waits
    against itself (the fake refuses it while any session is open)."""
    urn = world.add("S1")
    world.settle_agent()
    before = _snapshot(world)
    world.on_sleep = lambda seconds: world.lose_lock()
    assert run(*BASE, *IOTA_SDM) == 1
    assert len(world.locks) == 2 and world.agent == before        # the re-take worked
    kinds = [e[0] for e in world.log if e[0] in ("lock", "unlock")]
    assert kinds[:3] == ["lock", "unlock", "lock"] and urn


def test_an_unexpected_error_in_the_loop_is_logged_and_undone(world, caplog):
    world.add("S1")
    world.add("S2")
    world.settle_agent()
    before = _snapshot(world)
    world.inject = [("sleep", 5, "boom")]
    with caplog.at_level(logging.CRITICAL):
        assert run(*BASE, *IOTA_SDM) == 1
    assert world.agent == before
    assert "RuntimeError" in caplog.text


def test_the_stop_flag_is_looked_at_between_devices_of_a_move_to_em(world, capsys):
    a = world.add("S1", writer="iota", shape="legacy")
    b = world.add("S2", writer="iota", shape="legacy")
    world.settle_agent()
    world.inject = [("put_before", 0, "signal")]
    assert run(*BASE, "--to", "em", "--apply") == 1
    assert world.rows[a]["dm_writer"] == "em" and world.agent["S1"] == _attrs(a, "S1", ("em", None))
    assert world.rows[b]["dm_writer"] == "iota"
    assert [ln for ln in capsys.readouterr().out.splitlines() if b in ln and "not started" in ln]


# -- the profile is read after the lock is sure ---------------------------------

def _profile_changes_in_the_gap(world, monkeypatch):
    """The first profile read of the apply phase returns the OLD profile; then the lock dies and a
    profile change lands before the lock is taken again."""
    real = dataloggers._profile_measurements
    state = {"calls": 0}

    def reading(tenant, code):
        state["calls"] += 1
        got = real(tenant, code)
        if state["calls"] == 1:
            world.lose_lock()
            world.profiles["p"] = copy.deepcopy(ONE_MS)
        return got

    monkeypatch.setattr(dataloggers, "_profile_measurements", reading)
    return state


def test_the_final_put_carries_the_profile_read_after_the_lock_was_retaken(world, monkeypatch):
    urn = world.add("S1")
    world.settle_agent()
    items = cut.build_plan(TENANT, cut.list_rows(TENANT), "iota", "sdm")
    # the first profile read of the apply phase is the one of the final PUT
    state = _profile_changes_in_the_gap(world, monkeypatch)
    with FakeLock(world, TENANT) as lock:
        outcomes = cut.apply_iota(lock, TENANT, items, "sdm", 120.0)
    assert [o.status for o in outcomes] == ["moved"] and state["calls"] == 2
    assert world.agent["S1"] == _attrs(urn, "S1", ("iota", "sdm"), ONE_MS)


def test_the_undo_of_a_committed_row_carries_the_profile_read_after_the_re_take(world, monkeypatch):
    urn = world.add("S1")
    world.settle_agent()
    items = cut.build_plan(TENANT, cut.list_rows(TENANT), "iota", "sdm")
    world.inject = [("commit_after", 0, "unknown")]        # the row is written; the outcome unknown
    state = _profile_changes_in_the_gap(world, monkeypatch)
    with FakeLock(world, TENANT) as lock:
        outcomes = cut.apply_iota(lock, TENANT, items, "sdm", 120.0)
    assert outcomes[0].status == "moved" and state["calls"] == 2
    assert world.agent["S1"] == _attrs(urn, "S1", ("iota", "sdm"), ONE_MS)


def test_a_repair_carries_the_profile_read_after_the_re_take(world, monkeypatch):
    urn = world.add("S1", writer="iota", shape="sdm")
    world.agent["S1"] = _attrs(urn, "S1", ("em", None))
    items = cut.build_plan(TENANT, cut.list_rows(TENANT), "iota", "sdm")
    assert items[0].action == "repair"
    state = _profile_changes_in_the_gap(world, monkeypatch)
    with FakeLock(world, TENANT) as lock:
        out = cut.apply_item(lock, TENANT, items[0], "iota", "sdm")
    assert out.status == "repaired" and state["calls"] == 2
    assert world.agent["S1"] == _attrs(urn, "S1", ("iota", "sdm"), ONE_MS)


def test_a_move_to_em_carries_the_profile_read_after_the_re_take(world, monkeypatch):
    urn = world.add("S1", writer="iota", shape="sdm")
    world.settle_agent()
    items = cut.build_plan(TENANT, cut.list_rows(TENANT), "em", None)
    state = _profile_changes_in_the_gap(world, monkeypatch)
    with FakeLock(world, TENANT) as lock:
        outcomes = cut.apply_em(lock, TENANT, items)
    assert outcomes[0].status == "moved" and state["calls"] == 2
    assert world.agent["S1"] == _attrs(urn, "S1", ("em", None), ONE_MS)


def test_the_catch_all_log_names_the_failing_frame_and_nothing_it_said(world, caplog):
    world.add("S1")
    world.settle_agent()

    def crash(seconds):
        raise RuntimeError("SECRET-TEXT-IN-A-MESSAGE")

    world.on_sleep = crash
    with caplog.at_level(logging.DEBUG):
        assert run(*BASE, *IOTA_SDM) == 1
    line = [r for r in caplog.records if "unexpected RuntimeError at" in r.getMessage()]
    assert len(line) == 1 and line[0].levelno == logging.CRITICAL
    assert re.search(r"at test_dm_writer_cutover\.py:\d+ ", line[0].getMessage())
    assert "SECRET-TEXT-IN-A-MESSAGE" not in caplog.text
    assert not any(r.exc_info for r in line)
