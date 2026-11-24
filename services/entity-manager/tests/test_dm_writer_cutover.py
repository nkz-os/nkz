"""Cutover command: who writes the DeviceMeasurement entities of a device (everything mocked)."""
import copy
import logging
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.telemetry_fakes import PROFILE, TENANT, FakeState  # noqa: F401,I001  (stubs first)

import dataloggers  # noqa: E402
import dm_writer_cutover as cut  # noqa: E402

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
        self.update_fail = False
        self.commit_fail = False
        self.state_read_fail = False
        self.get_fail = False
        self.orion_fail = False

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
            rows = [dict(r) for r in w.rows.values()]
            if "device_urn = %s" in sql:
                rows = [r for r in rows if r["device_urn"] == params[1]]
            self._rows = sorted(rows, key=lambda r: r["external_id"])
        elif sql.startswith("SELECT dm_writer, dm_shape FROM iot_datalogger_devices"):
            if w.state_read_fail:
                raise RuntimeError("state read failed")
            r = w.rows.get(params[1])
            self._rows = [dict(dm_writer=r["dm_writer"], dm_shape=r["dm_shape"])] if r else []
        elif sql.startswith("UPDATE iot_datalogger_devices SET dm_writer"):
            writer, shape, tenant, urn = params
            assert tenant == TENANT
            if w.update_fail:
                raise RuntimeError("update failed")
            self.conn.pending.append((urn, writer, shape))
            self.rowcount = 1 if urn in w.rows else 0
            w.log.append(("update", urn, writer, shape))
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, world):
        self.world = world
        self.pending = []

    def cursor(self, *a, **kw):
        return _Cur(self)

    def commit(self):
        if self.world.commit_fail:
            raise RuntimeError("commit failed")
        for urn, writer, shape in self.pending:
            self.world.rows[urn]["dm_writer"] = writer
            self.world.rows[urn]["dm_shape"] = shape
        self.pending = []
        self.world.log.append(("commit",))

    def rollback(self):
        self.pending = []
        self.world.log.append(("rollback",))


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
        if world.put_fail(n, device_id):
            world.log.append(("put_failed", device_id))
            raise _IotaError(500)
        world.agent[device_id] = copy.deepcopy(attributes)
        kinds = sorted({a.get("entity_type", "Device") for a in attributes})
        world.log.append(("put", device_id, tuple(kinds)))
        if world.after_put:
            world.after_put(n, device_id)

    def get(url, headers, device_id):
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
    return w


def run(*argv):
    return cut.main(list(argv))


BASE = ("--tenant", TENANT)


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

    def lying_commit(self):
        for u, writer, shape in self.pending:
            self.world.rows[u]["dm_writer"], self.world.rows[u]["dm_shape"] = writer, shape
        self.pending = []
        raise RuntimeError("connection lost after commit")

    with patch.object(_Conn, "commit", lying_commit):
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


def dm(ext, name, *, shape="legacy", mtype="t", value="num", drop=(), extra=None, tenant=TENANT):
    prop = {"type": "Property", "value": 1}
    attrs = {
        DEV + "hasDevice": {"type": "Relationship", "object": urn_of(ext)},
        DEV + "controlledProperty": prop, DEV + "deviceType": prop,
        "https://smartdatamodels.org/dateObserved": prop, NKZ + "timeSource": prop,
    }
    if mtype is not None:
        attrs[DEV + "measurementType"] = prop
    if shape == "sdm":
        attrs["https://schema.org/name"] = prop
    if value in ("num", "both"):
        attrs[DEV + "numValue"] = prop
    if value in ("text", "both"):
        attrs[DEV + "textValue"] = prop
    for k in drop:
        attrs.pop(k)
    attrs.update(extra or {})
    return {"id": f"urn:ngsi-ld:DeviceMeasurement:{tenant}:{ext}:{name}",
            "type": DM_TYPE_IRI, "createdAt": "2026-01-01T00:00:00Z",
            "modifiedAt": "2026-01-01T00:00:00Z", **attrs}


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setenv("CONTEXT_URL", CTX_URL)
    holder = SimpleNamespace(h=None)

    def install(h):
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
    assert run(*BASE, "--verify") == 0
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
    assert run(*BASE, "--verify") == 0
    http(Http([dm("S1", "airTemperature", shape="legacy", mtype="instant")]))
    assert run(*BASE, "--verify") == 1


def test_verify_legacy_forbids_name(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", shape="sdm")]))
    assert run(*BASE, "--verify") == 1
    assert "https://schema.org/name" in capsys.readouterr().out


@pytest.mark.parametrize("extra", [
    {DEV + "unexpected": {"type": "Property", "value": 1}},
    {DEFAULT_VOCAB + "something": {"type": "Property", "value": 1}},
    {"something": {"type": "Property", "value": 1}},
])
def test_verify_extra_or_default_vocabulary_attribute_fails_b_only(world, http, capsys, extra):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", extra=extra)]))
    assert run(*BASE, "--verify") == 1
    out = capsys.readouterr().out
    key = next(iter(extra))
    assert key in out and "airTemperature" in out
    assert "check (a): ok" in out and "check (b): FAILED" in out


@pytest.mark.parametrize("key", ["TimeInstant", DEFAULT_VOCAB + "TimeInstant"])
def test_verify_time_instant_attribute_fails(world, http, capsys, key):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", extra={key: {"type": "Property", "value": 1}})]))
    assert run(*BASE, "--verify") == 1
    assert "TimeInstant" in capsys.readouterr().out


def test_verify_missing_attribute_fails(world, http, capsys):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", drop=(NKZ + "timeSource",))]))
    assert run(*BASE, "--verify") == 1
    assert NKZ + "timeSource" in capsys.readouterr().out


def test_verify_missing_measurement_type_only_when_profile_has_none(world, http):
    no_type = [{"incomingKey": "r1", "type": None, "sdmAttribute": "airTemperature", "unit": "°C"}]
    iota_world(world, ms=no_type)
    http(Http([dm("S1", "airTemperature", mtype=None)]))
    assert run(*BASE, "--verify") == 0
    http(Http([dm("S1", "airTemperature", mtype="t")]))
    assert run(*BASE, "--verify") == 1


def test_verify_dm_without_value_fails_a_for_every_device(world, http, capsys):
    world.add("S1", writer="em")                       # an em device: judged by (a) only
    world.add("S2", writer="iota", shape="legacy", code="p", ms=ONE_MS)
    bad = dm("S1", "airTemperature", value="none")
    http(Http([bad, dm("S2", "airTemperature")]))
    assert run(*BASE, "--verify") == 1
    out = capsys.readouterr().out
    assert "check (a): FAILED" in out and bad["id"] in out and "check (b): ok" in out


def test_verify_b_does_not_judge_em_devices(world, http):
    world.add("S1", writer="em")
    http(Http([dm("S1", "airTemperature", extra={DEV + "weird": {"type": "Property", "value": 1}})]))
    assert run(*BASE, "--verify") == 0


def test_verify_runtime_kind_accepts_one_or_both_values(world, http):
    rt = [{"type": "x", "sdmAttribute": "airTemperature"}]
    iota_world(world, ms=rt)
    for value in ("num", "text", "both"):
        http(Http([dm("S1", "airTemperature", mtype="x", value=value)]))
        assert run(*BASE, "--verify") == 0, value


def test_verify_numeric_kind_refuses_text_value(world, http):
    iota_world(world)
    http(Http([dm("S1", "airTemperature", value="both")]))
    assert run(*BASE, "--verify") == 1


def test_verify_paginates_and_checks_the_count(world, http):
    iota_world(world)
    dms = [dm("S1", "airTemperature")]
    h = http(Http(dms * 3, page=1))   # three DMs, one per page (same id, fine for the shape)
    assert run(*BASE, "--verify") == 0
    assert [c[2]["offset"] for c in h.orion_calls()] == [0, 1, 2]
    http(Http(dms, count=5))
    assert run(*BASE, "--verify") == 1


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
    assert run(*BASE, "--verify") == 1


def test_verify_fails_closed_without_context_url(world, http, monkeypatch):
    iota_world(world)
    h = http(Http([dm("S1", "airTemperature")]))
    monkeypatch.delenv("CONTEXT_URL")
    assert run(*BASE, "--verify") == 1
    assert h.orion_calls() == []


def test_verify_fails_closed_when_the_context_cannot_be_read(world, http):
    iota_world(world)
    http(Http([dm("S1", "airTemperature")], context_status=500))
    assert run(*BASE, "--verify") == 1


@pytest.mark.parametrize("term", ["numValue", "timeSource", "name", "DeviceMeasurement"])
def test_verify_fails_closed_when_a_term_is_missing(world, http, term):
    iota_world(world)
    ctx = copy.deepcopy(CONTEXT_DOC)
    del ctx["@context"][1][term]
    h = http(Http([dm("S1", "airTemperature")], context=ctx))
    assert run(*BASE, "--verify") == 1
    assert h.orion_calls() == []


def test_verify_device_narrows_check_b(world, http):
    world.add("S1", writer="iota", shape="legacy", code="p", ms=ONE_MS)
    world.add("S2", writer="iota", shape="legacy", code="p")
    bad = dm("S2", "airTemperature", extra={DEV + "weird": {"type": "Property", "value": 1}})
    http(Http([dm("S1", "airTemperature"), bad]))
    assert run(*BASE, "--device", urn_of("S1"), "--verify") == 0
    assert run(*BASE, "--verify") == 1


def test_verify_with_apply_runs_after_the_cutover(world, http):
    world.add("S1", code="p", ms=ONE_MS)
    h = http(Http([dm("S1", "airTemperature")]))
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply", "--verify") == 0
    assert len(h.orion_calls()) == 1


def test_verify_exit_1_after_a_failed_apply_even_if_dms_are_fine(world, http):
    world.add("S1", code="p", ms=ONE_MS)
    world.put_fail = lambda n, ext: True
    http(Http([dm("S1", "airTemperature")]))
    assert run(*BASE, "--to", "iota", "--shape", "legacy", "--apply", "--verify") == 1


def test_command_prints_no_secrets(world, capsys):
    world.add("S1")
    world.agent["S1"] = []
    run(*BASE, "--to", "iota", "--shape", "legacy", "--apply")
    out = capsys.readouterr()
    assert "apikey" not in (out.out + out.err).lower()
