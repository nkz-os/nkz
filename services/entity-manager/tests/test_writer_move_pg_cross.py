"""A sensor profile change against a writer move, on a real PostgreSQL: no interleaving leaves a farm
sensor silent.

Skipped unless WRITER_MOVE_PG_URL is set to a SUPERUSER url of a server that may be written to (the
suite gives POSTGRES_URL a dummy value, and CI has no PostgreSQL here). The module creates its own
database and a NON-superuser role that owns FORCE ROW LEVEL SECURITY tables shaped like migrations
109/120 (`iot_datalogger_devices`) and 010 (`sensor_profiles`), and drops both at the end.

Real: PostgreSQL, `writer_move_lock` (advisory locks, dedicated connections, the compare-and-set row
write), the row and profile reads and writes (a pool of the non-superuser role through
`set_current_tenant`), `dataloggers.change_device_profile`, `update_sensor_profile`'s locked body
through the Flask routes, `dm_writer_cutover.main` with MOVE_TO_IOTA_ENABLED patched True here only.
Fake, thread-safe, with one ordered event log: the IoT Agent (per-device attributes, blocks on a gate
at a given PUT, can fail), Orion and its DeviceModel writes (the suite's FakeOrion). `_sleep` of the
cutover and `time` of `writer_move_lock` are Event-controlled shims.

Threads: the move runs in the main thread (its signal handlers need it); a profile change runs in a
thread named "P" (or "Q"). Every wait has a timeout and a timeout is a failure. One helper decides
every case: `assert_no_silent_device`.
"""
import copy  # noqa: I001 (tests.telemetry_fakes stubs `common` and must come before the services)
import importlib.util
import json
import logging
import os
import signal
import threading
import time
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import psycopg2
import pytest
from psycopg2 import extensions
from psycopg2.pool import ThreadedConnectionPool

from tests.telemetry_fakes import (AUTH, PROFILE, TENANT, FakeState, _services_dir, client,
                                   wired)

from blueprints import sensors
import dataloggers
import device_models
import dm_writer_cutover as cut
import writer_move_lock as wml

ADMIN_URL = os.environ.get("WRITER_MOVE_PG_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="WRITER_MOVE_PG_URL not set")

T = 20.0                      # seconds: the longest any wait of these tests may take
ROLE_PASSWORD = "role-" + uuid.uuid4().hex
EXT = "S1"
URN = f"urn:ngsi-ld:Device:{TENANT}:{EXT}"
OLD = PROFILE["mapping"]["measurements"]                       # profile "p"
NEW = [{"type": "x", "sdmAttribute": "stemDiameter", "unit": "mm"}]   # profile "p2"
THIRD = [{"type": "h", "incomingKey": "k3", "sdmAttribute": "relativeHumidity", "unit": "%"}]  # "p3"
SAVED = [{"type": "t2", "incomingKey": "r2", "sdmAttribute": "airTemperature", "unit": "°C"}]
EM = ("em", None)
LEGACY = ("iota", "legacy")

SCHEMA = """
CREATE FUNCTION set_current_tenant(tenant TEXT) RETURNS VOID AS $$
BEGIN PERFORM set_config('app.current_tenant', tenant, false); END; $$ LANGUAGE plpgsql;
CREATE TABLE public.tenants (tenant_id text PRIMARY KEY, deleted_at timestamptz);
CREATE TABLE public.iot_datalogger_devices (
    datalogger_id uuid NOT NULL,
    tenant_id text NOT NULL CHECK (tenant_id <> ''),
    device_urn text NOT NULL,
    external_id text NOT NULL,
    dm_writer text NOT NULL DEFAULT 'em' CHECK (dm_writer IN ('em', 'iota')),
    dm_shape text CHECK (dm_shape IN ('legacy', 'sdm')),
    PRIMARY KEY (datalogger_id, device_urn),
    UNIQUE (tenant_id, device_urn)
);
CREATE TABLE public.sensor_profiles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text,
    code text NOT NULL,
    name text NOT NULL,
    description text,
    sdm_entity_type text NOT NULL,
    sdm_device_category text,
    sdm_category text,
    mapping jsonb NOT NULL,
    manufacturer_name text,
    brand_name text,
    model_name text
);
CREATE UNIQUE INDEX ON public.sensor_profiles (COALESCE(tenant_id, '__global__'), code);
ALTER TABLE public.iot_datalogger_devices OWNER TO {role};
ALTER TABLE public.sensor_profiles OWNER TO {role};
ALTER TABLE public.tenants OWNER TO {role};
ALTER TABLE public.iot_datalogger_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.iot_datalogger_devices FORCE ROW LEVEL SECURITY;
ALTER TABLE public.sensor_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sensor_profiles FORCE ROW LEVEL SECURITY;
CREATE POLICY iot_datalogger_devices_tenant_isolation ON public.iot_datalogger_devices
  USING (tenant_id = current_setting('app.current_tenant', true))
  WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
CREATE POLICY sensor_profiles_select ON public.sensor_profiles
  USING (tenant_id IS NULL OR tenant_id = current_setting('app.current_tenant', true));
CREATE POLICY sensor_profiles_write ON public.sensor_profiles
  USING (tenant_id = current_setting('app.current_tenant', true))
  WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
GRANT EXECUTE ON FUNCTION set_current_tenant(TEXT) TO {role};
"""


# ---- the real database --------------------------------------------------------------------

def _admin(dbname=None):
    dsn = extensions.make_dsn(ADMIN_URL, **({"dbname": dbname} if dbname else {}))
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


@pytest.fixture(scope="module")
def pg():
    suffix = uuid.uuid4().hex[:10]
    dbname, role = f"wmx_test_{suffix}", f"wmx_role_{suffix}"
    admin = _admin()
    cur = admin.cursor()
    cur.execute(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %s", (ROLE_PASSWORD,))
    cur.execute(f"CREATE DATABASE {dbname} OWNER {role}")
    db = _admin(dbname)
    db.cursor().execute(SCHEMA.format(role=role))
    role_url = extensions.make_dsn(ADMIN_URL, dbname=dbname, user=role, password=ROLE_PASSWORD)
    admin_url = extensions.make_dsn(ADMIN_URL, dbname=dbname)
    try:
        yield SimpleNamespace(db=db, role_url=role_url, admin_url=admin_url, dbname=dbname, role=role)
    finally:
        db.close()
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (dbname,))
        cur.execute(f"DROP DATABASE IF EXISTS {dbname}")
        cur.execute(f"DROP ROLE IF EXISTS {role}")
        admin.close()


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
    """The shipped constant is off; these tests switch it on in their own process only."""
    monkeypatch.setattr(cut, "MOVE_TO_IOTA_ENABLED", True)


# ---- the fake IoT Agent ----------------------------------------------------------------------

class _IotaError(Exception):
    def __init__(self, status_code=500, body=""):
        super().__init__(f"agent {status_code}")
        self.status_code = status_code


def who() -> str:
    """The actor of the current thread: "move" is the main thread, the others have their own name."""
    return "move" if threading.current_thread() is threading.main_thread() \
        else threading.current_thread().name


class Agent:
    """Thread-safe IoT Agent: the attributes of each device, an ordered record of every PUT, hooks
    that run before a PUT is applied (they may block on a gate) and after it."""

    def __init__(self, world):
        self.world = world
        self.lock = threading.Lock()
        self.attrs = {}
        self.puts = []                # (n, who, device_id, attributes)
        self.before = []              # callables(n, who, device_id, attributes)
        self.after = []
        self.fail = lambda n, actor: False

    def update(self, url, headers, device_id, attributes):
        actor = who()
        with self.lock:
            self.world.put_attempts += 1
            n = self.world.put_attempts
        for hook in list(self.before):
            hook(n, actor, device_id, attributes)
        if self.fail(n, actor):
            self.world.log.append(("put_failed", device_id, actor, n))
            raise _IotaError(500)
        with self.lock:
            self.attrs[device_id] = copy.deepcopy(attributes)
            self.puts.append((n, actor, device_id, copy.deepcopy(attributes)))
            self.world.log.append(("put", device_id, actor, n))
        for hook in list(self.after):
            hook(n, actor, device_id, attributes)

    def get(self, url, headers, device_id):
        with self.lock:
            if device_id not in self.attrs:
                raise _IotaError(404)
            return {"device_id": device_id, "attributes": copy.deepcopy(self.attrs[device_id])}


class GateTimeout(BaseException):
    """BaseException so that no `except Exception` of the code under test can swallow it."""


class Gate:
    """A point where a thread stops until the test lets it go."""

    def __init__(self, world, name):
        self.world, self.name = world, name
        self.reached, self.go = threading.Event(), threading.Event()

    def stall(self):
        self.world.log.append(("stall", self.name, who()))
        self.reached.set()
        if not self.go.wait(T):
            self.world.errors.append(f"gate {self.name} never released")
            raise GateTimeout(self.name)

    def wait_reached(self):
        assert self.reached.wait(T), f"gate {self.name} was never reached"

    def release(self):
        self.go.set()


class Handle:
    def __init__(self, world, name, fn):
        self.world, self.name = world, name
        self.box = {}

        def run():
            try:
                self.box["result"] = fn()
            except BaseException as exc:    # noqa: BLE001 - reported by `result()`
                self.box["error"] = exc

        self.thread = threading.Thread(target=run, name=name, daemon=True)
        world.threads.append(self)
        self.thread.start()

    def result(self):
        self.thread.join(T)
        assert not self.thread.is_alive(), f"thread {self.name} did not finish in {T:g} s"
        if "error" in self.box:
            raise self.box["error"]
        return self.box["result"]


# ---- the world --------------------------------------------------------------------------------

class Cross:
    def __init__(self, pg, st, orion, monkeypatch):
        self.pg, self.st, self.orion, self.mp = pg, st, orion, monkeypatch
        self.log = st.log
        self.errors = []
        self.threads = []
        self.put_attempts = 0
        self.pids = []                 # (actor, backend pid) of every lock connection, in order
        self.connect_plan = {}         # actor -> callable(index) raising LockUnavailable, or None
        self.connects = {}
        self.on_settle = None
        self.on_lock_wait = None
        self.agent = Agent(self)

    # -- seeding and reading
    def sql(self, text, params=()):
        conn = psycopg2.connect(self.pg.admin_url)
        conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute(text, params)
            return cur.fetchall() if cur.description else None
        finally:
            conn.close()

    def seed_profile(self, code, measurements):
        self.sql("INSERT INTO public.sensor_profiles (tenant_id, code, name, sdm_entity_type, mapping) "
                 "VALUES (%s, %s, %s, 'Device', %s) ON CONFLICT (COALESCE(tenant_id, '__global__'), code) "
                 "DO UPDATE SET mapping = EXCLUDED.mapping",
                 (TENANT, code, code, json.dumps({"measurements": measurements})))

    def seed_device(self, ext=EXT, state=EM, code="p"):
        urn = f"urn:ngsi-ld:Device:{TENANT}:{ext}"
        self.sql("INSERT INTO public.iot_datalogger_devices "
                 "(datalogger_id, tenant_id, device_urn, external_id, dm_writer, dm_shape) "
                 "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s)", (TENANT, urn, ext, state[0], state[1]))
        self.orion.entities[urn] = {
            "id": urn, "type": "Device", "name": {"type": "Property", "value": "Probe"},
            "controlledProperty": {"type": "Property", "value": ["temperature"]},
            "refDeviceModel": {"type": "Relationship",
                               "object": f"urn:ngsi-ld:DeviceModel:{TENANT}:{code}"}}
        self.agent.attrs[ext] = self.attributes(urn, ext, code, state)
        return urn

    def measurements(self, code):
        rows = self.sql("SELECT mapping FROM public.sensor_profiles WHERE code = %s "
                        "AND (tenant_id = %s OR tenant_id IS NULL) ORDER BY tenant_id NULLS LAST LIMIT 1",
                        (code, TENANT))
        return rows[0][0]["measurements"] if rows else None

    def attributes(self, urn, ext, code, state):
        return dataloggers._attributes_for_state(state, TENANT, urn, ext, code, self.measurements(code))

    def row(self, urn=URN):
        rows = self.sql("SELECT dm_writer, dm_shape FROM public.iot_datalogger_devices WHERE device_urn = %s",
                        (urn,))
        return tuple(rows[0]) if rows else None

    def set_row(self, state, urn=URN):
        self.sql("UPDATE public.iot_datalogger_devices SET dm_writer = %s, dm_shape = %s "
                 "WHERE device_urn = %s", (state[0], state[1], urn))

    def entity_code(self, urn=URN):
        return dataloggers.entity_profile_code(TENANT, self.orion.entities.get(urn))

    def put_of(self, actor=None):
        return [p for p in self.agent.puts if actor is None or p[1] == actor]

    def last_put_n(self, actor):
        return max(p[0] for p in self.agent.puts if p[1] == actor)

    # -- locks
    def advisory_locks(self):
        return self.sql("SELECT mode, granted, pid FROM pg_locks WHERE locktype = 'advisory'")

    def shared_holders(self):
        return [r for r in self.advisory_locks() if r[0] == "ShareLock" and r[1]]

    def pid_of(self, actor):
        mine = [pid for a, pid in self.pids if a == actor]
        assert mine, f"{actor} has no lock connection"
        return mine[-1]

    def kill(self, actor):
        """The server ends the newest lock connection of `actor` and, with it, its lock."""
        pid = self.pid_of(actor)
        assert self.sql("SELECT pg_terminate_backend(%s)", (pid,))[0][0] is True
        deadline = time.monotonic() + T
        while self.sql("SELECT count(*) FROM pg_stat_activity WHERE pid = %s", (pid,))[0][0]:
            assert time.monotonic() < deadline, "the backend did not go away"
            time.sleep(0.01)
        self.log.append(("kill", actor, pid))

    # -- the actors
    def thread(self, name, fn):
        return Handle(self, name, fn)

    def change_profile(self, code="p2", urn=URN):
        r = client().put(f"/api/sensors/devices/{urn}/profile", headers=AUTH,
                         content_type="application/json", data=json.dumps({"profile": code}))
        return r.status_code, r.get_json()

    def save_profile(self, measurements, code="p"):
        body = {"code": code, "name": code, "sdm_entity_type": "Device",
                "mapping": {"measurements": measurements}}
        r = client().put(f"/api/sensors/profiles/{code}", headers=AUTH,
                         content_type="application/json", data=json.dumps(body))
        return r.status_code, r.get_json()

    def move(self, to="iota", shape="legacy", settle="60", device=None):
        argv = ["--tenant", TENANT, "--to", to, "--apply"]
        if to == "iota":
            argv += ["--shape", shape, "--settle", settle]
        if device:
            argv += ["--device", device]
        return cut.main(argv)


def expected_attributes(world, urn, ext, state):
    code = world.entity_code(urn)
    return world.attributes(urn, ext, code, state)


def assert_no_silent_device(world):
    """No device of the tenant is silent.

    For every row in `iota`, the agent's attributes contain every mapping of the row's shape
    (the DeviceMeasurement ones AND the Device ones) built from the profile of the entity; for every
    row in `em`, the Device mappings of the entity's profile are present (extra DM mappings are
    allowed). A device with no row, no entity, no profile or nothing in the agent is silent."""
    rows = world.sql("SELECT device_urn, external_id, dm_writer, dm_shape FROM public.iot_datalogger_devices "
                     "WHERE tenant_id = %s ORDER BY external_id", (TENANT,))
    assert rows, "no row to judge"
    for urn, ext, writer, shape in rows:
        entity = world.orion.entities.get(urn)
        assert entity, f"{ext}: the entity is gone"
        code = world.entity_code(urn)
        assert code, f"{ext}: the entity has no profile"
        measurements = world.measurements(code)
        assert measurements is not None, f"{ext}: profile {code} is not readable"
        agent = world.agent.attrs.get(ext)
        assert agent is not None, f"{ext}: the agent has no device"
        have = cut._normalise(agent)
        if writer == "iota":
            want = cut._normalise(dataloggers._attributes_for_state(
                (writer, shape), TENANT, urn, ext, code, measurements))
            where = f"row iota/{shape}"
        else:
            want = cut._normalise(dataloggers._attributes_for_state(
                ("em", None), TENANT, urn, ext, code, measurements))
            where = "row em"
        missing = [a for a in want if a not in have]
        assert not missing, (f"SILENT DEVICE {ext}: {where}, profile {code}; the agent lacks "
                             f"{[a.get('name') or a.get('object_id') for a in missing]} "
                             f"(it holds {[a.get('name') or a.get('object_id') for a in have]})")


@pytest.fixture
def w(pg, monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", pg.role_url)
    pool = ThreadedConnectionPool(1, 12, pg.role_url)

    @contextmanager
    def tenant_connection(tenant):
        """What `db_helper.get_db_connection_with_tenant` does, on a pool of the non-superuser role
        (the suite replaces `db_helper` by a mock in some files)."""
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT set_current_tenant(%s)", (tenant,))
            yield conn
        except BaseException:
            conn.rollback()
            raise
        finally:
            pool.putconn(conn)

    st = FakeState()
    admin = pg.db.cursor()
    admin.execute("TRUNCATE public.iot_datalogger_devices, public.sensor_profiles, public.tenants")
    admin.execute("INSERT INTO public.tenants (tenant_id) VALUES (%s)", (TENANT,))
    with wired(st) as wi:
        world = Cross(pg, st, wi.orion, monkeypatch)
        iota = SimpleNamespace(
            update_device_attributes=world.agent.update, get_device=world.agent.get,
            build_iota_attributes=__import__("sys").modules["common.iot_agent"].build_iota_attributes,
            IotAgentError=_IotaError)
        for module in (dataloggers, sensors, cut):
            monkeypatch.setattr(module, "get_db_connection_with_tenant", tenant_connection)
        monkeypatch.setattr(dataloggers, "iot_agent", iota)
        monkeypatch.setattr(cut, "iot_agent", iota)

        real_connect = wml._connect

        def connect():
            actor = who()
            index = world.connects.get(actor, 0)
            world.connects[actor] = index + 1
            plan = world.connect_plan.get(actor)
            if plan is not None:
                plan(index)
            conn = real_connect()
            world.pids.append((actor, conn.get_backend_pid()))
            return conn

        monkeypatch.setattr(wml, "_connect", connect)

        def lock_time_sleep(seconds):
            """The wait of the move for the lock: logged once, released by the test, quick."""
            if not world.log or world.log[-1] != ("move_waits",):
                world.log.append(("move_waits",))
            if world.on_lock_wait:
                hook, world.on_lock_wait = world.on_lock_wait, None
                hook()
            time.sleep(min(seconds, 0.02))

        monkeypatch.setattr(wml, "time", SimpleNamespace(monotonic=time.monotonic, sleep=lock_time_sleep))

        settled = []

        def settle_sleep(seconds):
            if not settled:
                settled.append(seconds)
                world.log.append(("settle", seconds))
                if world.on_settle:
                    world.on_settle()

        monkeypatch.setattr(cut, "_sleep", settle_sleep)
        for code, ms in (("p", OLD), ("p2", NEW), ("p3", THIRD)):
            world.seed_profile(code, ms)
        try:
            yield world
        finally:
            for handle in world.threads:
                handle.thread.join(T)
            deadline = time.monotonic() + T
            while world.advisory_locks() and time.monotonic() < deadline:
                time.sleep(0.02)
            leftovers = world.advisory_locks()
            pool.closeall()
            assert not world.errors, world.errors
            assert not leftovers, f"advisory locks still held at the end: {leftovers}"


def put_attrs_after(world, n):
    return [p for p in world.agent.puts if p[0] > n]


# ---- the harness itself ---------------------------------------------------------------------

def test_the_role_is_not_a_superuser_and_the_tables_force_rls(w):
    assert w.sql("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (w.pg.role,)) \
        == [(False, False)]
    assert w.sql("SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                 "WHERE relname IN ('iot_datalogger_devices', 'sensor_profiles') ORDER BY relname") \
        == [("iot_datalogger_devices", True, True), ("sensor_profiles", True, True)]


def test_the_check_passes_on_a_healthy_world(w):
    w.seed_device(state=EM)
    assert_no_silent_device(w)
    w.set_row(LEGACY)
    w.agent.attrs[EXT] = w.attributes(URN, EXT, "p", LEGACY)
    assert_no_silent_device(w)


@pytest.mark.parametrize("make_silent", [
    pytest.param(lambda w: (w.set_row(LEGACY), w.agent.attrs.__setitem__(
        EXT, w.attributes(URN, EXT, "p", EM))), id="iota-row-with-device-only-mappings"),
    pytest.param(lambda w: (w.set_row(LEGACY), w.agent.attrs.__setitem__(
        EXT, w.attributes(URN, EXT, "p", ("iota", "sdm")))), id="iota-row-with-the-other-shape"),
    pytest.param(lambda w: w.agent.attrs.__setitem__(
        EXT, w.attributes(URN, EXT, "p2", EM)), id="em-row-with-the-mappings-of-another-profile"),
    pytest.param(lambda w: w.agent.attrs.__setitem__(EXT, []), id="em-row-with-nothing-in-the-agent"),
    pytest.param(lambda w: w.agent.attrs.pop(EXT), id="no-device-in-the-agent"),
])
def test_the_check_fails_on_a_hand_made_silent_device(w, make_silent):
    w.seed_device(state=EM)
    make_silent(w)
    with pytest.raises(AssertionError):
        assert_no_silent_device(w)


# ---- case 1: the profile change starts first, the move waits and moves with the NEW profile ----

def _stall_agent_put(w, gate, actor="P"):
    """`actor`'s first PUT stops BEFORE it is applied, under a live lock."""
    seen = []

    def hook(n, who_, device_id, attributes):
        if who_ == actor and not seen:
            seen.append(n)
            gate.stall()

    w.agent.before.append(hook)


@pytest.mark.parametrize("route", ["device", "save"])
def test_1_a_profile_change_that_started_first_makes_the_move_wait_and_the_move_uses_its_profile(w, route):
    w.seed_device(state=EM)
    gate = Gate(w, "first PUT")
    _stall_agent_put(w, gate)
    if route == "device":
        P = w.thread("P", lambda: w.change_profile("p2"))
        want_code, want_measurements = "p2", NEW
    else:
        P = w.thread("P", lambda: w.save_profile(SAVED, "p"))
        want_code, want_measurements = "p", SAVED
    gate.wait_reached()
    assert len(w.shared_holders()) == 1                    # the change holds the shared lock
    w.on_lock_wait = gate.release                          # the move has tried and failed once
    assert w.move() == 0
    status, body = P.result()
    assert status == 200, body
    assert ("move_waits",) in w.log
    first_move_put = min(p[0] for p in w.put_of("move"))
    assert w.last_put_n("P") < first_move_put              # the change's PUT landed before the move's
    assert w.row() == LEGACY
    assert w.entity_code() == want_code
    assert cut._normalise(w.agent.attrs[EXT]) == cut._normalise(
        dataloggers._attributes_for_state(LEGACY, TENANT, URN, EXT, want_code, want_measurements))
    assert_no_silent_device(w)


# ---- case 2: the move first, the profile change is 409; mappings and row stay consistent -------

def test_2_a_profile_change_during_a_move_is_409_and_nothing_changes(w):
    w.seed_device(state=EM)
    seen = {}

    def during_the_wait():
        before = len(w.agent.puts)
        seen["answer"] = w.thread("P", lambda: w.change_profile("p2")).result()
        seen["puts"] = len(w.agent.puts) - before
        seen["row"] = w.row()
        seen["entity"] = w.entity_code()

    w.on_settle = during_the_wait
    assert w.move() == 0
    status, body = seen["answer"]
    assert status == 409 and body == {"error": "Sensors are being moved; try again in a few minutes",
                                      "step": "writer_move"}, body
    assert seen["puts"] == 0 and seen["row"] == EM and seen["entity"] == "p"
    assert w.row() == LEGACY and w.put_of("P") == []
    assert_no_silent_device(w)
    status, body = w.thread("P", lambda: w.change_profile("p2")).result()   # after the move: it works
    assert status == 200, body
    assert w.entity_code() == "p2" and w.row() == LEGACY
    assert_no_silent_device(w)


# ---- case 3: the change's lock is killed, the move enters, the change tries a PUT in the BAD order

def _stall_first_guard(w, monkeypatch, gate):
    """The change stops right BEFORE it checks its lock for the first PUT."""
    real, seen = dataloggers._lock_alive, []

    def alive(lock):
        if who() == "P" and not seen:
            seen.append(1)
            gate.stall()
        return real(lock)

    monkeypatch.setattr(dataloggers, "_lock_alive", alive)


def _kill_then_move(w, P_gate):
    P_gate.wait_reached()
    w.kill("P")
    assert w.move() == 0
    return w.last_put_n("move")


def test_3_first_put_after_the_move_is_refused(w, monkeypatch):
    w.seed_device(state=EM)
    gate = Gate(w, "before the first guard")
    _stall_first_guard(w, monkeypatch, gate)
    P = w.thread("P", lambda: w.change_profile("p2"))
    move_end = _kill_then_move(w, gate)
    gate.release()
    status, body = P.result()
    assert status == 502 and body["step"] == "db", body
    assert put_attrs_after(w, move_end) == []              # not a single PUT after the move's last
    assert w.put_of("P") == []
    assert w.row() == LEGACY and w.entity_code() == "p"
    assert_no_silent_device(w)


def test_3_a_later_put_after_the_move_is_refused(w):
    w.seed_device(state=EM)
    gate = Gate(w, "after the first PUT")
    seen = []

    def after(n, actor, device_id, attributes):
        if actor == "P" and not seen:
            seen.append(n)
            gate.stall()

    w.agent.after.append(after)
    P = w.thread("P", lambda: w.change_profile("p2"))
    move_end = _kill_then_move(w, gate)
    gate.release()
    status, body = P.result()
    assert status == 502 and body["step"] == "dm_state", body
    # What the change sends after the move's last PUT is only the restore, built for the `iota`
    # row; the attributes of the new profile (built for it) are never sent.
    new_for_iota = cut._normalise(w.attributes(URN, EXT, "p2", LEGACY))
    assert [p for p in put_attrs_after(w, move_end) if cut._normalise(p[3]) == new_for_iota] == []
    assert w.row() == LEGACY and w.entity_code() == "p"
    assert_no_silent_device(w)


def _stall_model_write(w, gate):
    """The change stops right BEFORE its first Orion write, after the check that preceded it."""
    base = device_models.SyncOrionClient
    seen = []

    class Stalling(base):
        def upsert_entities_batch(self, entities):
            if who() == "P" and not seen:
                seen.append(1)
                gate.stall()
            return super().upsert_entities_batch(entities)

    w.mp.setattr(device_models, "SyncOrionClient", Stalling)


def test_3_the_restore_after_the_move_is_built_for_the_iota_row(w):
    w.seed_device(state=EM)
    gate = Gate(w, "before the Orion write")
    _stall_model_write(w, gate)
    P = w.thread("P", lambda: w.change_profile("p2"))
    move_end = _kill_then_move(w, gate)
    gate.release()
    status, body = P.result()
    assert status == 502 and body["step"] == "db", body
    assert ("orion", "attrs", URN) not in w.log              # the entity was never written
    after = put_attrs_after(w, move_end)
    assert len(after) == 1 and after[0][1] == "P"            # only the restore of the old profile
    assert cut._normalise(after[0][3]) == cut._normalise(w.attributes(URN, EXT, "p", LEGACY))
    assert w.row() == LEGACY and w.entity_code() == "p"
    assert_no_silent_device(w)


def test_3_the_fallback_with_the_database_down_and_more_than_30_s_puts_nothing(w, monkeypatch, caplog):
    w.seed_device(state=EM)
    gate = Gate(w, "before the Orion write")
    _stall_model_write(w, gate)
    offset = [0.0]
    monkeypatch.setattr(dataloggers, "time", SimpleNamespace(
        monotonic=lambda: time.monotonic() + offset[0], sleep=time.sleep))

    def down(index):
        if index >= 1:                                       # its first connection worked; the re-take does not
            raise wml.LockUnavailable("cannot connect (OperationalError)")

    w.connect_plan["P"] = down
    P = w.thread("P", lambda: w.change_profile("p2"))
    with caplog.at_level(logging.CRITICAL):
        gate.wait_reached()
        w.kill("P")
        offset[0] = 31.0                                     # 31 s since the lock last answered
        assert w.move() == 0
        move_end = w.last_put_n("move")
        gate.release()
        status, body = P.result()
    assert status == 502, body
    assert put_attrs_after(w, move_end) == []
    assert any("lost more than 30 s ago" in r.getMessage() for r in caplog.records)
    assert w.row() == LEGACY and w.entity_code() == "p"
    assert_no_silent_device(w)


# ---- case 4: the move's connection is killed before and after its commit ---------------------

def _wrap_commit(w, before=None, after=None):
    real = wml.ExclusiveLock.commit_state

    def commit_state(self, urn, previous, target):
        if before:
            before()
        out = real(self, urn, previous, target)
        if after:
            after()
        return out

    w.mp.setattr(wml.ExclusiveLock, "commit_state", commit_state)


def test_4_the_move_connection_killed_before_the_commit(w):
    w.seed_device(state=EM)
    _wrap_commit(w, before=lambda: w.kill("move"))
    assert w.move() == 1
    assert w.row() == EM                                     # nothing was committed
    assert_no_silent_device(w)                               # the old Device mappings are back


def test_4_the_move_connection_killed_right_after_the_commit(w):
    w.seed_device(state=EM)
    _wrap_commit(w, after=lambda: w.kill("move"))
    assert w.move() == 0
    assert w.row() == LEGACY
    assert_no_silent_device(w)                               # never "row iota with Device-only mappings"


def test_4_killed_after_the_commit_and_a_profile_change_takes_the_gap(w):
    """Between the move's read of the profile and its final PUT the lock is lost and a profile change
    runs to the end; the final PUT must not put the profile it read before."""
    w.seed_device(state=EM)
    armed = []
    _wrap_commit(w, after=lambda: armed.append(1))
    real = dataloggers._orion_devices
    answers = []

    def orion_devices(tenant, urns):
        out = real(tenant, urns)
        if armed and who() == "move" and not answers:
            w.kill("move")
            answers.append(w.thread("P", lambda: w.change_profile("p2")).result())
        return out

    w.mp.setattr(dataloggers, "_orion_devices", orion_devices)
    assert w.move() == 0
    assert answers and answers[0][0] == 200, answers
    assert w.row() == LEGACY and w.entity_code() == "p2"
    assert_no_silent_device(w)


def test_4_the_row_is_never_overwritten_when_someone_else_changed_it(w):
    """The commit is a compare-and-set: a row that is no longer the one the plan read is left alone."""
    w.seed_device(state=EM)

    def somebody_else():
        w.set_row(("iota", "sdm"))
        w.agent.attrs[EXT] = w.attributes(URN, EXT, "p", ("iota", "sdm"))

    w.on_settle = somebody_else
    assert w.move() == 1
    assert w.row() == ("iota", "sdm")


# ---- case 5: two profile changes at once, both finish ------------------------------------------

@pytest.mark.parametrize("state", [EM, LEGACY], ids=["em", "iota"])
def test_5_two_profile_changes_at_once_both_finish(w, state):
    w.seed_device(state=state)
    gate = Gate(w, "first PUT of P")
    _stall_agent_put(w, gate, "P")
    seen = {}
    w.agent.before.append(lambda n, a, d, at: seen.setdefault(
        "holders", len(w.shared_holders())) if a == "Q" else None)
    P = w.thread("P", lambda: w.change_profile("p2"))
    gate.wait_reached()
    status_q, body_q = w.thread("Q", lambda: w.change_profile("p3")).result()
    assert status_q == 200, body_q
    assert seen["holders"] == 2                              # both shared locks held at the same time
    gate.release()
    status_p, body_p = P.result()
    assert status_p == 200, body_p
    assert w.row() == state
    assert w.entity_code() == "p2"                           # the one that wrote last
    assert_no_silent_device(w)


# ---- case 6: two moves at once, the second exits 1 with nothing written ------------------------

def test_6_two_moves_at_once_the_second_exits_1_and_writes_nothing(w, monkeypatch, capsys):
    w.seed_device(state=EM)
    seen = {}

    def second_move():
        monkeypatch.setattr(cut, "_exclusive_lock", lambda tenant: wml.ExclusiveLock(tenant, wait=0.3))
        puts, rows = len(w.agent.puts), w.row()
        capsys.readouterr()
        seen["code"] = w.move(shape="sdm")
        seen["out"] = capsys.readouterr().out
        seen["unchanged"] = (len(w.agent.puts) == puts, w.row() == rows)

    w.on_settle = second_move
    assert w.move() == 0
    assert seen["code"] == 1
    assert "a profile change or another move is running; nothing was written" in seen["out"]
    assert seen["unchanged"] == (True, True)
    assert w.row() == LEGACY
    assert_no_silent_device(w)


# ---- case 7: saving a profile during a move is 409 and the profile row is unchanged -----------

def test_7_saving_a_profile_during_a_move_is_409_and_the_row_stays(w):
    w.seed_device(state=EM)
    seen = {}

    def during_the_wait():
        seen["answer"] = w.thread("P", lambda: w.save_profile(SAVED, "p")).result()
        seen["mapping"] = w.measurements("p")

    w.on_settle = during_the_wait
    assert w.move() == 0
    status, body = seen["answer"]
    assert status == 409 and body["step"] == "writer_move", body
    assert seen["mapping"] == OLD and w.measurements("p") == OLD
    assert w.row() == LEGACY
    assert_no_silent_device(w)
    status, body = w.thread("P", lambda: w.save_profile(SAVED, "p")).result()   # after the move: saved
    assert status == 200, body
    assert w.measurements("p") == SAVED
    assert_no_silent_device(w)                               # the iota device got the new mappings


# ---- case 8: the move's backend is terminated during the wait -------------------------------

def test_8_the_move_backend_terminated_during_the_wait(w):
    w.seed_device(state=EM)
    w.on_settle = lambda: w.kill("move")
    assert w.move() == 1
    assert w.row() == EM
    assert_no_silent_device(w)


def test_8_and_a_profile_change_slips_into_the_gap(w):
    w.seed_device(state=EM)
    seen = {}

    def during_the_wait():
        w.kill("move")
        seen["answer"] = w.thread("P", lambda: w.change_profile("p2")).result()

    w.on_settle = during_the_wait
    assert w.move() == 1
    assert seen["answer"][0] == 200, seen["answer"]
    assert w.row() == EM and w.entity_code() == "p2"
    assert_no_silent_device(w)


# ---- case 9: the change's backend is terminated right before its Orion write -------------------

def test_9_the_change_backend_terminated_before_its_orion_write(w):
    w.seed_device(state=EM)
    gate = Gate(w, "before the Orion write")
    _stall_model_write(w, gate)
    P = w.thread("P", lambda: w.change_profile("p2"))
    gate.wait_reached()
    w.kill("P")
    gate.release()
    status, body = P.result()
    assert status == 502 and body["step"] == "db", body
    assert ("orion", "attrs", URN) not in w.log
    assert w.entity_code() == "p" and w.row() == EM
    assert cut._normalise(w.agent.attrs[EXT]) == cut._normalise(w.attributes(URN, EXT, "p", EM))
    assert_no_silent_device(w)


# ---- case 10: moving back to em while a profile change runs ------------------------------------

def test_10_a_move_to_em_while_a_profile_change_runs_exits_1_and_writes_nothing(w, monkeypatch, capsys):
    w.seed_device(state=LEGACY)
    monkeypatch.setattr(cut, "_exclusive_lock", lambda tenant: wml.ExclusiveLock(tenant, wait=0.3))
    gate = Gate(w, "first PUT")
    _stall_agent_put(w, gate)
    P = w.thread("P", lambda: w.change_profile("p2"))
    gate.wait_reached()
    puts = len(w.agent.puts)
    assert w.move(to="em") == 1
    assert "a profile change or another move is running; nothing was written" in capsys.readouterr().out
    assert len(w.agent.puts) == puts and w.row() == LEGACY
    gate.release()
    status, body = P.result()
    assert status == 200, body
    assert w.row() == LEGACY and w.entity_code() == "p2"
    assert_no_silent_device(w)


def test_10_a_profile_change_during_a_move_to_em_is_409_and_works_afterwards(w):
    w.seed_device(state=LEGACY)
    seen = {}

    def at_the_put(n, actor, device_id, attributes):
        if actor == "move" and "answer" not in seen:
            seen["answer"] = w.thread("P", lambda: w.change_profile("p2")).result()
            seen["row"] = w.row()

    w.agent.before.append(at_the_put)
    assert w.move(to="em") == 0
    assert seen["answer"][0] == 409 and seen["answer"][1]["step"] == "writer_move", seen["answer"]
    assert seen["row"] == EM                                 # the row is committed before the PUT
    assert w.row() == EM and w.entity_code() == "p"
    assert_no_silent_device(w)
    status, body = w.thread("P", lambda: w.change_profile("p2")).result()
    assert status == 200, body
    assert w.row() == EM and w.entity_code() == "p2"
    assert_no_silent_device(w)


# ---- case 11: the change's lock dies and a move that does NOT include its device holds the lock ---

@pytest.mark.parametrize("how", ["move-of-another-device", "move-interrupted-before-the-device"])
def test_11_a_restore_that_meets_a_move_that_skips_its_device_says_so_and_saving_again_repairs_it(
        w, caplog, how):
    """The change of S2 pushes the new profile, its lock dies, and a move that never reaches S2 takes
    the lock before the restore. Nothing can be put back (waiting would hold the only worker): the
    device keeps the new mappings, the log says so, and saving the profile again repairs it."""
    urn1, urn2 = w.seed_device("S1"), w.seed_device("S2")
    gate = Gate(w, "before the Orion write")
    _stall_model_write(w, gate)
    P = w.thread("P", lambda: w.change_profile("p2", urn=urn2))
    seen = {}

    def while_the_move_holds_the_lock():
        gate.release()
        seen["answer"] = P.result()

    gate.wait_reached()
    w.kill("P")
    with caplog.at_level(logging.CRITICAL):
        if how == "move-of-another-device":
            w.on_settle = while_the_move_holds_the_lock
            assert w.move(device=urn1) == 0
        else:
            def at_the_first_put(n, actor, device_id, attributes):
                if actor == "move" and "answer" not in seen:
                    while_the_move_holds_the_lock()
                    os.kill(os.getpid(), signal.SIGINT)         # the move stops before S2

            w.agent.before.append(at_the_first_put)
            assert w.move() == 1
    status, body = seen["answer"]
    assert status == 502 and body["step"] == "db", body
    assert len(w.put_of("P")) == 1                              # the first push only: no restore
    assert any(r.getMessage().startswith("INCONSISTENCY: a move holds the lock;")
               and "device=S2" in r.getMessage() for r in caplog.records)
    assert w.row(urn2) == EM
    with pytest.raises(AssertionError, match="SILENT DEVICE S2"):     # the state the log warns about
        assert_no_silent_device(w)
    status, body = w.thread("P", lambda: w.change_profile("p2", urn=urn2)).result()   # save again
    assert status == 200, body
    assert_no_silent_device(w)
