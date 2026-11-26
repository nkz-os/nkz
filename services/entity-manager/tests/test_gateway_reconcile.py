"""The gateway repair job: projection of every gateway, then the removal of orphans.

The job deletes things in the broker and the IoT Agent, so every safety rule has its own test:
an id must match `gw-<12 lowercase hex>` exactly (and be a Device), nothing is removed when the
table could not be read, at most 50 removals per run, and an id that is in the table by the time
of the removal is left alone. IO is behind seams (`read_table`, `list_*`, `orphan_guard`) that these
tests replace; the real locks are in test_gateway_reconcile_pg.py.
"""
import logging
import threading
import time
from contextlib import contextmanager

import pytest

from tests.telemetry_fakes import TENANT  # noqa: F401  (puts the service dirs on sys.path)

import dataloggers  # noqa: E402
import gateway_reconcile as gr  # noqa: E402
import gateways  # noqa: E402

T = "acme"
GW = "gw-0123456789ab"
GW2 = "gw-ba9876543210"


def urn(tenant, ext):
    return f"urn:ngsi-ld:Device:{tenant}:{ext}"


def device(tenant, ext, category=("network",), etype="Device"):
    entity = {"id": urn(tenant, ext), "type": etype}
    if category is not None:
        entity["deviceCategory"] = {"type": "Property", "value": list(category)}
    return entity


def agent_doc(ext, name=None, etype="Device"):
    return {"device_id": ext, "entity_type": etype, "entity_name": name or urn(T, ext)}


class World:
    """What the seams answer, and a record of everything the job did."""

    def __init__(self, monkeypatch):
        self.tenants = [T]
        self.tables = {T: []}          # tenant -> [(dl_id, external_id or None)]
        self.table_errors = {}         # tenant -> number of reads that succeed before one raises
        self.orion = {T: []}
        self.agent = {T: []}
        self.orion_error = set()
        self.agent_error = set()
        self.guard = {}                # (tenant, ext) -> None (free) | reason | Exception
        self.projected = []
        self.removed = []              # (tenant, ext, entity)
        self.guarded = []
        self.outcomes = {}             # dl_id -> outcome
        self.lock_free = True
        self.reads = {}
        self.fail_reads = {}           # tenant -> read numbers (0-based) that raise
        self.table_after_projection = None
        self.removal_failures = set()
        self.sensor_ids = {T: set()}   # tenant -> external ids in iot_datalogger_devices
        self.sensor_errors = set()
        self.agent_removed = []
        monkeypatch.setattr(gr, "list_active_tenants", lambda: list(self.tenants))
        monkeypatch.setattr(gr, "read_table", self.read_table)
        monkeypatch.setattr(gr, "list_orion_devices", self.list_orion)
        monkeypatch.setattr(gr, "list_agent_devices", self.list_agent)
        monkeypatch.setattr(gr, "read_sensor_ids", self.read_sensor_ids)
        monkeypatch.setattr(gr, "orphan_guard", self.orphan_guard)
        monkeypatch.setattr(gr, "run_removal", self.run_removal)
        monkeypatch.setattr(gr, "job_lock", self.job_lock)
        monkeypatch.setattr(gateways, "project_gateway_detailed", self.project)
        monkeypatch.setenv(gateways.FLAG, "*")

    def read_table(self, tenant):
        n = self.reads.get(tenant, 0)
        self.reads[tenant] = n + 1
        if n in self.fail_reads.get(tenant, ()):
            raise RuntimeError("table unreadable")
        allowed = self.table_errors.get(tenant)
        if allowed is not None and n >= allowed:
            raise RuntimeError("table unreadable")
        if n >= 1 and self.table_after_projection is not None:
            return list(self.table_after_projection.get(tenant, self.tables[tenant]))
        return list(self.tables[tenant])

    def list_orion(self, tenant):
        if tenant in self.orion_error:
            raise RuntimeError("orion down")
        return list(self.orion[tenant])

    def list_agent(self, tenant):
        if tenant in self.agent_error:
            raise RuntimeError("agent down")
        return list(self.agent[tenant])

    def read_sensor_ids(self, tenant):
        if tenant in self.sensor_errors:
            raise RuntimeError("sensor table unreadable")
        return set(self.sensor_ids.get(tenant, ()))

    @contextmanager
    def orphan_guard(self, tenant, ext, agent):
        self.guarded.append((tenant, ext))
        verdict = self.guard.get((tenant, ext))
        if isinstance(verdict, Exception):
            raise verdict
        yield verdict

    def run_removal(self, tenant, ext, entity, agent):
        self.removed.append((tenant, ext, entity))
        self.agent_removed.append((tenant, ext, agent))
        return ["agent_device"] if (tenant, ext) in self.removal_failures else []

    @contextmanager
    def job_lock(self):
        yield self.lock_free

    def project(self, tenant, dl_id):
        self.projected.append((tenant, dl_id))
        return self.outcomes.get(dl_id, ("done", "unchanged"))


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


def run(*argv):
    return gr.main(list(argv))


def removed_ids(world):
    return [ext for _, ext, _ in world.removed]


# ── projection ───────────────────────────────────────────────────────────────────────────

def test_every_gateway_is_projected_including_one_without_an_id(world):
    world.tables[T] = [("dl-1", GW), ("dl-2", None)]
    assert run() == 0
    assert world.projected == [(T, "dl-1"), (T, "dl-2")]


def test_projections_run_one_at_a_time_in_the_calling_thread(world):
    world.tables[T] = [(f"dl-{i}", None) for i in range(4)]
    seen = {"now": 0, "max": 0, "threads": set()}
    lock = threading.Lock()

    def slow(tenant, dl_id):
        with lock:
            seen["now"] += 1
            seen["max"] = max(seen["max"], seen["now"])
            seen["threads"].add(threading.current_thread())
        time.sleep(0.05)
        with lock:
            seen["now"] -= 1
        return ("done", None)

    gateways.project_gateway_detailed = slow  # World's monkeypatch restores the original
    assert run() == 0
    assert seen["max"] == 1
    assert seen["threads"] == {threading.current_thread()}


def test_a_failed_projection_fails_the_run_but_the_rest_goes_on(world):
    world.tables[T] = [("dl-1", GW), ("dl-2", GW2)]
    world.outcomes["dl-1"] = ("failed:orion_entity", None)
    assert run() == 1
    assert world.projected == [(T, "dl-1"), (T, "dl-2")]


def test_an_off_projection_is_not_a_failure(world):
    world.tables[T] = [("dl-1", GW)]
    world.outcomes["dl-1"] = ("off", None)
    assert run() == 0


def test_a_projection_that_raises_fails_only_its_gateway(world, monkeypatch):
    world.tables[T] = [("dl-1", GW), ("dl-2", GW2)]

    def boom(tenant, dl_id):
        world.projected.append((tenant, dl_id))
        if dl_id == "dl-1":
            raise RuntimeError("boom")
        return ("done", None)

    monkeypatch.setattr(gateways, "project_gateway_detailed", boom)
    assert run() == 1
    assert world.projected == [(T, "dl-1"), (T, "dl-2")]


# ── which ids may be removed ─────────────────────────────────────────────────────────────

UNTOUCHABLE_IDS = [
    "gw-XYZ", "GW-0123456789AB", "gw-0123456789AB", "gw-0123456789a", "gw-0123456789abc",
    "gw-0123456789ag", "xgw-0123456789ab", "gw-0123456789abx", "gw-0123456789ab\n",
    "\ngw-0123456789ab", " gw-0123456789ab", "gw-0123456789ab ", "gw_0123456789ab", "gw-",
    "vpn-gw-0123456789ab", "gw-0123456789ab-vpn", "S1", "sensor-gw-1", "headscale-gw",
]


@pytest.mark.parametrize("ext", UNTOUCHABLE_IDS)
def test_an_id_that_is_not_exactly_a_gateway_id_is_never_removed(world, ext):
    world.orion[T] = [{"id": f"urn:ngsi-ld:Device:{T}:{ext}", "type": "Device",
                       "deviceCategory": {"type": "Property", "value": ["network"]}}]
    world.agent[T] = [agent_doc(ext)]
    assert run() == 0
    assert world.removed == [] and world.guarded == []


def test_a_sensor_is_never_removed(world):
    world.orion[T] = [device(T, "S1", category=("sensor",)), device(T, "S2", category=None)]
    world.agent[T] = [agent_doc("S1")]
    assert run() == 0 and world.removed == []


def test_a_manufacturing_machine_with_a_gateway_id_is_never_removed(world):
    world.orion[T] = [device(T, GW, etype="ManufacturingMachine")]
    world.agent[T] = [agent_doc(GW, etype="ManufacturingMachine")]
    assert run() == 0 and world.removed == []


def test_a_vpn_gateway_device_is_never_removed(world):
    """Another id shape, with `deviceCategory` network too."""
    world.orion[T] = [device(T, "vpn-1"), device(T, "headscale-node-7"),
                      {"id": "urn:ngsi-ld:Device:vpn:gateway", "type": "Device",
                       "deviceCategory": {"type": "Property", "value": ["network"]}}]
    assert run() == 0 and world.removed == []


def test_a_gateway_id_whose_device_is_not_a_network_one_is_left_alone(world):
    world.orion[T] = [device(T, GW, category=("sensor",)), device(T, GW2, category=None)]
    assert run() == 0 and world.removed == []


def test_another_tenants_urn_is_never_removed(world):
    world.orion[T] = [device("other", GW)]
    assert run() == 0 and world.removed == []


def test_an_id_that_is_in_the_table_is_never_removed(world):
    world.tables[T] = [("dl-1", GW)]
    world.orion[T] = [device(T, GW)]
    world.agent[T] = [agent_doc(GW)]
    assert run() == 0 and world.removed == []


def test_an_orphan_in_orion_and_one_in_the_agent_are_removed(world):
    world.tables[T] = [("dl-1", GW)]
    world.orion[T] = [device(T, GW), device(T, GW2)]
    world.agent[T] = [agent_doc(GW),
                      agent_doc("gw-aaaaaaaaaaaa")]
    assert run() == 0
    assert world.removed == [(T, "gw-aaaaaaaaaaaa", False), (T, GW2, True)]


def test_the_entity_goes_only_when_it_was_seen_as_a_network_device(world):
    world.agent[T] = [agent_doc(GW)]
    world.orion[T] = [device(T, GW, category=("sensor",))]
    assert run() == 0
    assert world.removed == [(T, GW, False)]


def test_the_removal_order_is_the_agent_device_then_the_measurements_then_the_entity(monkeypatch):
    calls = []
    monkeypatch.setattr(dataloggers, "delete_iota_device", lambda t, e: calls.append(("agent", e)))
    monkeypatch.setattr(dataloggers, "delete_orion_entity", lambda t, u: calls.append(("orion", u)))
    assert gr.run_removal(T, GW, True, True) == []
    assert [c[0] for c in calls] == ["agent", "orion", "orion", "orion", "orion"]
    assert calls[0] == ("agent", GW)
    assert calls[-1] == ("orion", urn(T, GW))
    assert all(u.startswith(f"urn:ngsi-ld:DeviceMeasurement:{T}:{GW}:") for _, u in calls[1:-1])
    calls.clear()
    assert gr.run_removal(T, GW, False, True) == []
    assert [c[0] for c in calls] == ["agent", "orion", "orion", "orion"]
    calls.clear()
    assert gr.run_removal(T, GW, True, False) == []  # no agent device of this gateway: not touched
    assert [c[0] for c in calls] == ["orion", "orion", "orion", "orion"]


def test_a_failing_removal_step_stops_the_removal_before_the_entity(monkeypatch):
    calls = []

    def agent(t, e):
        raise RuntimeError("agent down")

    monkeypatch.setattr(dataloggers, "delete_iota_device", agent)
    monkeypatch.setattr(dataloggers, "delete_orion_entity", lambda t, u: calls.append(u))
    assert gr.run_removal(T, GW, True, True) == ["agent_device"]
    assert calls == []


def test_a_failed_measurement_keeps_the_entity_as_the_anchor_and_the_next_run_finishes(monkeypatch):
    calls = []
    broken = {"on": True}

    def orion(t, u):
        if broken["on"] and u.endswith(":batteryLevel"):
            raise RuntimeError("orion down")
        calls.append(u)

    monkeypatch.setattr(dataloggers, "delete_iota_device", lambda t, e: None)
    monkeypatch.setattr(dataloggers, "delete_orion_entity", orion)
    assert gr.run_removal(T, GW, True, True) == ["measurement"]
    assert urn(T, GW) not in calls and len(calls) == 1
    broken["on"] = False
    calls.clear()
    assert gr.run_removal(T, GW, True, True) == []
    assert calls[-1] == urn(T, GW) and len(calls) == 4


# ── an agent device that is not this gateway's ─────────────────────────────

def test_an_agent_device_with_a_gateway_id_that_points_at_a_sensor_is_never_removed(world):
    world.agent[T] = [agent_doc(GW, name=urn(T, "S1"))]
    assert run() == 0
    assert world.removed == [] and world.guarded == []


def test_an_agent_device_that_points_at_another_tenants_gateway_is_never_removed(world):
    world.agent[T] = [agent_doc(GW, name=urn("other", GW))]
    assert run() == 0 and world.removed == []


def test_an_agent_device_without_an_entity_name_is_never_removed(world):
    world.agent[T] = [{"device_id": GW, "entity_type": "Device"}]
    assert run() == 0 and world.removed == []


@pytest.mark.parametrize("etype", [None, "ManufacturingMachine", "Thing"])
def test_an_agent_device_that_is_not_a_device_is_never_removed(world, etype):
    doc = agent_doc(GW)
    if etype is None:
        del doc["entity_type"]
    else:
        doc["entity_type"] = etype
    world.agent[T] = [doc]
    assert run() == 0 and world.removed == []


def test_an_id_that_is_an_external_id_of_a_sensor_is_never_removed(world):
    world.sensor_ids[T] = {GW}
    world.agent[T] = [agent_doc(GW)]
    world.orion[T] = [device(T, GW)]
    assert run() == 0 and world.removed == []


def test_a_sensor_table_that_cannot_be_read_removes_no_agent_device_but_fails_the_run(world):
    world.sensor_errors.add(T)
    world.agent[T] = [agent_doc(GW)]
    assert run() == 1 and world.removed == []


def test_the_agent_device_is_removed_only_when_it_passed_the_checks(world):
    world.orion[T] = [device(T, GW), device(T, GW2)]
    world.agent[T] = [agent_doc(GW), agent_doc(GW2, name=urn(T, "S1"))]
    assert run() == 0
    assert world.agent_removed == [(T, GW, True), (T, GW2, False)]


def test_agent_candidates_needs_the_gateway_urn_the_type_and_a_free_id():
    ok = agent_doc(GW)
    assert gr.agent_candidates(T, [ok], set()) == [GW]
    assert gr.agent_candidates(T, [agent_doc(GW, name=urn(T, "S1"))], set()) == []
    assert gr.agent_candidates(T, [agent_doc(GW, etype="ManufacturingMachine")], set()) == []
    assert gr.agent_candidates(T, [ok], {GW}) == []


# ── migration 123 missing ────────────────────────────────────────────────────────────────

def test_a_schema_without_the_gateway_columns_removes_nothing_and_fails(monkeypatch):
    import psycopg2.errors

    class Cur:
        def execute(self, sql, params=()):
            raise psycopg2.errors.UndefinedColumn("gateway_external_id")

    class Conn:
        def cursor(self, *a, **k):
            return Cur()

        def rollback(self):
            pass

    @contextmanager
    def db(tenant):
        yield Conn()

    removed = []
    monkeypatch.setenv(gateways.FLAG, "*")
    monkeypatch.setattr(dataloggers, "get_db_connection_with_tenant", db)
    monkeypatch.setattr(gr, "list_active_tenants", lambda: [T])
    monkeypatch.setattr(gr, "job_lock", lambda: _held())
    monkeypatch.setattr(gr, "list_orion_devices", lambda t: [device(T, GW)])
    monkeypatch.setattr(gr, "list_agent_devices", lambda t: [agent_doc(GW)])
    monkeypatch.setattr(gr, "run_removal", lambda *a: removed.append(a) or [])
    monkeypatch.setattr(gateways, "project_gateway_detailed", lambda t, d: removed.append("p") or ("done", None))
    assert run() == 1
    assert removed == []


@contextmanager
def _held():
    yield True


# ── a table that could not be read ───────────────────────────────────────────────────────

def test_a_table_that_cannot_be_read_removes_nothing_and_projects_nothing(world):
    world.table_errors[T] = 0
    world.orion[T] = [device(T, GW)]
    world.agent[T] = [agent_doc(GW2)]
    assert run() == 1
    assert world.removed == [] and world.projected == [] and world.guarded == []


def test_a_first_read_that_fails_while_the_second_would_work_still_does_nothing(world):
    world.tables[T] = [("dl-1", GW)]
    world.fail_reads[T] = {0}
    world.orion[T] = [device(T, GW2)]
    assert run() == 1
    assert world.projected == [] and world.removed == [] and world.guarded == []


def test_a_table_that_cannot_be_read_after_the_projections_removes_nothing(world):
    world.tables[T] = [("dl-1", GW)]
    world.table_errors[T] = 1  # the first read works, the second raises
    world.orion[T] = [device(T, GW2)]
    world.agent[T] = [agent_doc(GW2)]
    assert run() == 1
    assert world.projected == [(T, "dl-1")]
    assert world.removed == [] and world.guarded == []


def test_a_listing_that_failed_is_never_taken_as_empty(world):
    world.orion_error.add(T)
    world.agent[T] = [agent_doc(GW)]
    assert run() == 1
    # the agent side was readable and its orphan goes; nothing is inferred from the Orion failure
    assert world.removed == [(T, GW, False)]


def test_an_unreadable_agent_listing_removes_no_agent_device(world):
    world.agent_error.add(T)
    world.orion[T] = [device(T, GW)]
    assert run() == 1
    assert world.removed == [(T, GW, True)]


# ── the cap ──────────────────────────────────────────────────────────────────────────────

def _many(n):
    return [f"gw-{i:012x}" for i in range(n)]


def test_at_most_fifty_removals_per_run_and_the_rest_is_logged_as_deferred(world, caplog):
    ids = _many(51)
    world.orion[T] = [device(T, i) for i in ids]
    with caplog.at_level(logging.INFO):
        assert run() == 0
    assert len(world.removed) == 50
    assert removed_ids(world) == ids[:50]
    deferred = [r.getMessage() for r in caplog.records if "deferred" in r.getMessage()]
    assert len(deferred) == 1 and ids[50] in deferred[0] and T in deferred[0]


def test_exactly_fifty_orphans_are_all_removed_and_none_deferred(world, caplog):
    world.orion[T] = [device(T, i) for i in _many(50)]
    with caplog.at_level(logging.INFO):
        assert run() == 0
    assert len(world.removed) == 50
    assert not [r for r in caplog.records if "deferred" in r.getMessage()]


def test_the_cap_is_per_run_not_per_tenant(world):
    world.tenants = ["acme", "beta"]
    world.tables = {"acme": [], "beta": []}
    world.orion = {"acme": [device("acme", i) for i in _many(30)],
                   "beta": [device("beta", i) for i in _many(30)]}
    world.agent = {"acme": [], "beta": []}
    assert run() == 0
    assert len(world.removed) == 50
    assert {t for t, _, _ in world.removed[:30]} == {"acme"}


def test_a_removal_that_fails_still_counts_against_the_cap(world):
    ids = _many(51)
    world.orion[T] = [device(T, i) for i in ids]
    world.removal_failures = {(T, i) for i in ids}
    assert run() == 1
    assert len(world.removed) == 50


# ── the re-check under the lock ──────────────────────────────────────────────────────────

def test_an_id_that_appeared_in_the_table_before_the_removal_is_not_removed(world, caplog):
    world.orion[T] = [device(T, GW)]
    world.guard[(T, GW)] = "now in the table"
    with caplog.at_level(logging.INFO):
        assert run() == 0
    assert world.guarded == [(T, GW)] and world.removed == []
    assert any(GW in r.getMessage() and "not removed" in r.getMessage() for r in caplog.records)


def test_the_guard_runs_before_every_removal(world):
    world.orion[T] = [device(T, GW), device(T, GW2)]
    world.guard[(T, GW2)] = "now in the table"
    assert run() == 0
    assert world.guarded == [(T, GW), (T, GW2)]
    assert removed_ids(world) == [GW]


def test_a_guard_that_cannot_decide_removes_nothing_and_fails_the_run(world):
    world.orion[T] = [device(T, GW)]
    world.guard[(T, GW)] = RuntimeError("lock timeout")
    assert run() == 1 and world.removed == []


def test_a_removal_that_leaves_something_behind_fails_the_run(world):
    world.orion[T] = [device(T, GW)]
    world.removal_failures = {(T, GW)}
    assert run() == 1


# ── tenants ──────────────────────────────────────────────────────────────────────────────

def test_a_tenant_that_is_not_in_the_flag_is_skipped(world, monkeypatch):
    world.tenants = ["acme", "beta"]
    world.tables = {"acme": [("dl-1", GW)], "beta": [("dl-2", GW2)]}
    world.orion = {"acme": [device("acme", GW)], "beta": [device("beta", "gw-aaaaaaaaaaaa")]}
    world.agent = {"acme": [], "beta": []}
    monkeypatch.setenv(gateways.FLAG, "beta")
    assert run() == 0
    assert world.projected == [("beta", "dl-2")]
    assert [t for t, _, _ in world.removed] == ["beta"]


def test_with_the_flag_empty_nothing_is_read_at_all(world, monkeypatch):
    monkeypatch.setenv(gateways.FLAG, "")
    monkeypatch.setattr(gr, "list_active_tenants", lambda: pytest.fail("tenants listed"))
    monkeypatch.setattr(gr, "job_lock", lambda: pytest.fail("lock taken"))
    assert run() == 0


def test_the_tenants_come_from_the_active_tenants_statement(monkeypatch):
    from common.active_tenants import ACTIVE_TENANT_IDS_SQL
    seen = []

    class Cur:
        def execute(self, sql, params=()):
            seen.append(sql)

        def fetchall(self):
            return [("acme",), ("beta",)]

        def close(self):
            pass

    class Conn:
        def cursor(self, *a, **k):
            return Cur()

        def rollback(self):
            pass

        def close(self):
            seen.append("closed")

    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:pw@h/db")
    monkeypatch.setattr(gr.psycopg2, "connect", lambda *a, **k: Conn())
    assert gr.list_active_tenants() == ["acme", "beta"]
    assert seen == [ACTIVE_TENANT_IDS_SQL, "closed"]


def test_a_tenant_that_fails_does_not_stop_the_others(world):
    world.tenants = ["acme", "beta"]
    world.tables = {"acme": [("dl-1", GW)], "beta": [("dl-2", GW2)]}
    world.orion = {"acme": [], "beta": [device("beta", "gw-aaaaaaaaaaaa")]}
    world.agent = {"acme": [], "beta": []}
    world.table_errors["acme"] = 0
    assert run() == 1
    assert world.projected == [("beta", "dl-2")]
    assert [t for t, _, _ in world.removed] == ["beta"]


def test_the_tenant_argument_runs_only_that_tenant(world):
    world.tenants = ["acme", "beta"]
    world.tables = {"acme": [("dl-1", GW)], "beta": [("dl-2", GW2)]}
    world.orion = {"acme": [], "beta": []}
    world.agent = {"acme": [], "beta": []}
    assert run("--tenant", "beta") == 0
    assert world.projected == [("beta", "dl-2")]


def test_a_named_tenant_that_is_not_active_is_a_failure(world):
    assert run("--tenant", "suspended") == 1
    assert world.projected == [] and world.removed == []


def test_a_failure_to_list_the_tenants_is_a_failure(world, monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(gr, "list_active_tenants", boom)
    assert run() == 1


# ── the job lock ─────────────────────────────────────────────────────────────────────────

def test_a_second_run_exits_zero_and_does_nothing(world, caplog):
    world.lock_free = False
    world.tables[T] = [("dl-1", None)]
    world.orion[T] = [device(T, GW)]
    with caplog.at_level(logging.INFO):
        assert run() == 0
    assert world.projected == [] and world.removed == [] and world.reads == {}
    assert any("another run" in r.getMessage() for r in caplog.records)


def test_a_job_lock_that_cannot_be_taken_fails_the_run(world, monkeypatch):
    @contextmanager
    def broken():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(gr, "job_lock", broken)
    assert run() == 1 and world.projected == []


def test_the_job_lock_is_the_documented_advisory_lock(monkeypatch):
    executed = []

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=()):
            executed.append((" ".join(sql.split()), params))

        def fetchone(self):
            return (True,)

    class Conn:
        closed = False

        def cursor(self, *a, **k):
            return Cur()

        def commit(self):
            executed.append("commit")

        def close(self):
            self.closed = True
            executed.append("close")

    conn = Conn()
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:pw@h/db")
    monkeypatch.setattr(gr.psycopg2, "connect", lambda *a, **k: conn)
    with gr.job_lock() as got:
        assert got is True and not conn.closed  # held for as long as the connection lives
    assert executed[0] == ("SELECT pg_try_advisory_lock(hashtext(%s))", ("gateway-reconcile",))
    assert executed[-1] == "close"


# ── dry run ──────────────────────────────────────────────────────────────────────────────

def test_a_dry_run_writes_nothing_and_says_what_it_would_do(world, caplog):
    world.tables[T] = [("dl-1", None), ("dl-2", GW)]
    world.orion[T] = [device(T, GW), device(T, GW2)]
    world.agent[T] = [agent_doc("gw-aaaaaaaaaaaa")]
    with caplog.at_level(logging.INFO):
        assert run("--dry-run") == 0
    assert world.projected == [] and world.removed == [] and world.guarded == []
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert text.count("would project") == 2
    assert GW2 in text and "gw-aaaaaaaaaaaa" in text and text.count("would remove") == 2


def test_a_dry_run_applies_the_cap_to_what_it_prints(world, caplog):
    world.orion[T] = [device(T, i) for i in _many(51)]
    with caplog.at_level(logging.INFO):
        assert run("--dry-run") == 0
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert text.count("would remove") == 50 and text.count("deferred:") == 1


def test_a_dry_run_with_an_unreadable_table_says_so_and_fails(world):
    world.table_errors[T] = 0
    assert run("--dry-run") == 1


# ── arguments, logs ──────────────────────────────────────────────────────────────────────

def test_help_exits_zero(world, capsys, monkeypatch):
    monkeypatch.setattr(gr, "job_lock", lambda: pytest.fail("lock taken"))
    assert run("--help") == 0
    assert "--dry-run" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["--bogus"], ["--tenant"], ["extra"], ["--tenant", ""]])
def test_bad_arguments_exit_two_and_touch_nothing(world, argv, monkeypatch):
    monkeypatch.setattr(gr, "job_lock", lambda: pytest.fail("lock taken"))
    assert run(*argv) == 2
    assert world.projected == [] and world.removed == []


def test_logs_carry_no_secret_from_an_exception(world, caplog, monkeypatch):
    secret = "postgresql://user:hunter2@dbhost/nkz apikey=nkz_iot_deadbeef"
    world.tables[T] = [("dl-1", GW)]

    def boom(tenant, dl_id):
        raise RuntimeError(secret)

    monkeypatch.setattr(gateways, "project_gateway_detailed", boom)
    world.orion_error.add(T)
    world.guard[(T, GW2)] = RuntimeError(secret)
    world.agent[T] = [agent_doc(GW2)]
    with caplog.at_level(logging.DEBUG):
        run()
    text = "\n".join(r.getMessage() + str(r.exc_info) for r in caplog.records)
    assert "hunter2" not in text and "nkz_iot_deadbeef" not in text and "dbhost" not in text
    assert all(r.exc_info is None for r in caplog.records)


def test_every_action_is_one_line_with_the_tenant_and_the_ids(world, caplog):
    world.tables[T] = [("dl-1", GW)]
    world.orion[T] = [device(T, GW2)]
    with caplog.at_level(logging.INFO):
        run()
    lines = [r.getMessage() for r in caplog.records]
    assert any(f"tenant={T}" in l and "datalogger=dl-1" in l and "outcome=done" in l for l in lines)
    assert any(f"tenant={T}" in l and f"id={GW2}" in l and "outcome=removed" in l for l in lines)
    assert all("\n" not in l for l in lines)


# ── the listings ─────────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _agent_pages(monkeypatch, pages, status=200):
    calls = []

    def get(url, **kw):
        calls.append((url, kw))
        offset = kw["params"]["offset"]
        return _Resp(status, pages.get(offset, {"count": 0, "devices": []}))

    monkeypatch.setattr(gr.requests, "get", get)
    monkeypatch.setattr(gr, "AGENT_PAGE", 2)
    monkeypatch.setenv("IOT_AGENT_URL", "http://iota:4041")
    return calls


def test_the_agent_listing_is_paginated_and_sent_with_the_tenant_headers(monkeypatch):
    calls = _agent_pages(monkeypatch, {
        0: {"count": 3, "devices": [{"device_id": "a"}, {"device_id": "b"}]},
        2: {"count": 3, "devices": [{"device_id": "c"}]}})
    docs = gr.list_agent_devices(T)
    assert [d["device_id"] for d in docs] == ["a", "b", "c"]
    assert [kw["params"]["offset"] for _, kw in calls] == [0, 2]
    assert all(u == "http://iota:4041/iot/devices" for u, _ in calls)
    assert all(kw["headers"] == dataloggers.iota_headers(T) and kw["headers"] for _, kw in calls)


def test_an_agent_listing_that_does_not_add_up_raises(monkeypatch):
    _agent_pages(monkeypatch, {0: {"count": 5, "devices": [{"device_id": "a"}, {"device_id": "b"}]}})
    with pytest.raises(RuntimeError):
        gr.list_agent_devices(T)


def test_an_agent_error_status_raises(monkeypatch):
    _agent_pages(monkeypatch, {}, status=500)
    with pytest.raises(RuntimeError):
        gr.list_agent_devices(T)


def test_an_agent_body_that_is_not_a_listing_raises(monkeypatch):
    _agent_pages(monkeypatch, {0: {"unexpected": True}})
    with pytest.raises(RuntimeError):
        gr.list_agent_devices(T)


def test_the_orion_listing_is_the_device_type_read_with_the_platform_context(monkeypatch):
    import device_models
    seen = []
    monkeypatch.setattr(device_models, "list_entities", lambda t, ty: seen.append((t, ty)) or [{"id": "x"}])
    assert gr.list_orion_devices(T) == [{"id": "x"}]
    assert seen == [(T, "Device")]
