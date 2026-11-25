"""project_gateway with the real locks, on a throwaway PostgreSQL (Orion, agent and broker are fakes).

Skipped unless GATEWAY_PROJECTION_PG_URL is set to a SUPERUSER url of a server that may be written
to; the test makes its own database and drops it.
"""
import datetime
import os
import pathlib
import threading
import time
import uuid
from contextlib import contextmanager

import psycopg2
import pytest
from psycopg2 import extensions

from tests.telemetry_fakes import FakeState, TENANT, wired

import dataloggers  # noqa: E402
import gateways  # noqa: E402

ADMIN_URL = os.environ.get("GATEWAY_PROJECTION_PG_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="GATEWAY_PROJECTION_PG_URL not set")

MIGRATIONS = pathlib.Path(__file__).resolve().parents[3] / "config" / "timescaledb" / "migrations"
DL = "11111111-1111-1111-1111-111111111111"
USER = "dl_aaaaaaaaaaaaaaaa"


def _connect(dbname=None):
    dsn = extensions.make_dsn(ADMIN_URL, **({"dbname": dbname} if dbname else {}))
    return psycopg2.connect(dsn)


@pytest.fixture(scope="module")
def dsn():
    name = "gw_proj_" + uuid.uuid4().hex[:10]
    admin = _connect()
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'nkz_core'")
    made_role = cur.fetchone() is None
    if made_role:
        cur.execute("CREATE ROLE nkz_core NOLOGIN")
    cur.execute(f"CREATE DATABASE {name}")
    conn = _connect(name)
    conn.autocommit = True
    c = conn.cursor()
    c.execute("CREATE TABLE public.tenants (tenant_id text PRIMARY KEY, deleted_at timestamptz)")
    c.execute((MIGRATIONS / "109_iot_dataloggers.sql").read_text(encoding="utf-8"))
    c.execute((MIGRATIONS / "123_gateway_devices.sql").read_text(encoding="utf-8"))
    conn.close()
    try:
        yield extensions.make_dsn(ADMIN_URL, dbname=name)
    finally:
        cur.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        if made_role:
            cur.execute("DROP ROLE IF EXISTS nkz_core")
        admin.close()


@pytest.fixture
def db(dsn, monkeypatch):
    monkeypatch.setenv(gateways.FLAG, TENANT)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("TRUNCATE public.iot_datalogger_devices, public.iot_dataloggers, public.tenants CASCADE")
    cur.execute("INSERT INTO public.tenants (tenant_id) VALUES (%s)", (TENANT,))
    cur.execute("INSERT INTO public.iot_dataloggers (id, tenant_id, name, kind, mqtt_username, created_at) "
                "VALUES (%s, %s, 'Box', 'datak', %s, %s)",
                (DL, TENANT, USER, datetime.datetime(2026, 11, 20, 8, 30, tzinfo=datetime.timezone.utc)))
    for s in ("S1", "S2"):
        cur.execute("INSERT INTO public.iot_datalogger_devices (datalogger_id, tenant_id, device_urn, external_id) "
                    "VALUES (%s, %s, %s, %s)", (DL, TENANT, f"urn:ngsi-ld:Device:{TENANT}:{s}", s))

    @contextmanager
    def real_db(tenant):
        c = psycopg2.connect(dsn)
        try:
            yield c
        finally:
            c.close()

    monkeypatch.setattr(dataloggers, "get_db_connection_with_tenant", real_db)
    yield conn, dsn, real_db
    conn.close()


def test_two_projections_at_once_end_equal_to_the_rows(db):
    conn, dsn, real_db = db
    st = FakeState()
    st.iota_strict = True
    with wired(st) as w:
        dataloggers.get_db_connection_with_tenant = real_db
        real = w.iota.provision_device

        def slow(url, headers, **kw):
            time.sleep(0.4)
            return real(url, headers, **kw)

        w.iota.provision_device = slow
        out = []
        threads = [threading.Thread(target=lambda: out.append(gateways.project_gateway(TENANT, DL)))
                   for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert sorted(out) == ["done", "done"]
        cur = conn.cursor()
        cur.execute("SELECT gateway_external_id, gateway_provisioned_at FROM public.iot_dataloggers WHERE id = %s", (DL,))
        ext, marker = cur.fetchone()
        urn = f"urn:ngsi-ld:Device:{TENANT}:{ext}"
        entity = w.orion.device_models[urn]
        assert gateways.is_gateway_external_id(ext) and marker is not None
        assert sorted(entity["controlledAsset"]["object"]) == [f"urn:ngsi-ld:Device:{TENANT}:S1",
                                                              f"urn:ngsi-ld:Device:{TENANT}:S2"]
        assert list(st.iota_devices) == [ext]
        assert st.role_topics[USER] == {"/KEY123/S1/attrs", "/KEY123/S2/attrs", f"/KEY123/{ext}/attrs"}
    # one creation, one provisioning: the second saw the first's work
    assert len([e for e in st.log if e[:2] == ("orion", "upsert")]) == 1
    assert len([e for e in st.log if e[:2] == ("iota", "provision")]) == 1


def test_a_suspension_that_wins_the_lock_race_stops_the_projection(db):
    conn, dsn, real_db = db
    st = FakeState()
    holder = psycopg2.connect(dsn)
    hc = holder.cursor()
    hc.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"ingestion:{TENANT}",))
    with wired(st):
        dataloggers.get_db_connection_with_tenant = real_db
        out = []
        t = threading.Thread(target=lambda: out.append(gateways.project_gateway(TENANT, DL)))
        t.start()
        cur = conn.cursor()
        for _ in range(100):  # the projection is waiting for the ingestion lock
            cur.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")
            if cur.fetchone()[0]:
                break
            time.sleep(0.05)
        else:
            pytest.fail("the projection never waited for the ingestion lock")
        hc.execute("UPDATE public.tenants SET deleted_at = now() WHERE tenant_id = %s", (TENANT,))
        holder.commit()
        t.join(30)
    holder.close()
    assert out == ["off"]
    assert [e for e in st.log if e[0] in ("orion", "iota", "dynsec", "db")] == []
    cur.execute("SELECT gateway_external_id FROM public.iot_dataloggers WHERE id = %s", (DL,))
    assert cur.fetchone()[0] is None


def test_the_ingestion_lock_comes_before_the_row_lock_so_nothing_deadlocks(db):
    """A suspension takes the ingestion lock then the row (as `set_tenant_ingestion` and revoke do)."""
    conn, dsn, real_db = db
    st = FakeState()
    cur = conn.cursor()
    cur.execute("UPDATE public.iot_dataloggers SET gateway_external_id = 'gw-0123456789ab' WHERE id = %s", (DL,))
    other = psycopg2.connect(dsn)
    oc = other.cursor()
    oc.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"ingestion:{TENANT}",))
    errors = []
    with wired(st):
        dataloggers.get_db_connection_with_tenant = real_db
        out = []
        t = threading.Thread(target=lambda: out.append(gateways.project_gateway(TENANT, DL)))
        t.start()
        time.sleep(0.8)  # the projection has run as far as it can without the ingestion lock
        try:
            oc.execute("SELECT 1 FROM public.iot_dataloggers WHERE id = %s FOR UPDATE", (DL,))
            other.commit()
        except psycopg2.errors.DeadlockDetected:  # pragma: no cover - the failure this test is for
            errors.append("deadlock")
            other.rollback()
        t.join(30)
    other.close()
    assert errors == [] and out == ["done"]


def _run(target, *args):
    box = []
    t = threading.Thread(target=lambda: box.append(target(*args)))
    t.start()
    return t, box


def test_a_slow_orion_blocks_no_rotation_reconcile_or_suspension(db, monkeypatch):
    conn, dsn, real_db = db
    st = FakeState()
    entered, release = threading.Event(), threading.Event()
    real_sync = gateways._sync_entity

    def slow(tenant, entity):
        entered.set()
        assert release.wait(30)
        return real_sync(tenant, entity)

    monkeypatch.setattr(gateways, "_sync_entity", slow)
    with wired(st):
        dataloggers.get_db_connection_with_tenant = real_db
        projection, out = _run(gateways.project_gateway, TENANT, DL)
        assert entered.wait(15)  # the projection is inside the Orion call

        def operations():
            dataloggers.rotate_datalogger(TENANT, DL)          # ingestion lock, then row lock
            dataloggers.reconcile_datalogger_acl(TENANT, DL)   # row lock (a sensor registration/move)
            conn.cursor().execute("UPDATE public.tenants SET deleted_at = now() WHERE tenant_id = %s", (TENANT,))
            return dataloggers.set_tenant_ingestion(TENANT, False)  # ingestion lock

        ops, result = _run(operations)
        ops.join(10)
        still_blocked = ops.is_alive()
        release.set()
        projection.join(30)
        ops.join(30)
    assert not still_blocked, "a datalogger operation waited for the slow Orion call"
    assert result[0] == {"changed": 1, "failed": []}
    assert out == ["off"] or out == ["done"]  # suspended in between: the topics step stops


def test_two_projections_and_a_sensor_move_interleaved_end_equal_to_the_rows(db, monkeypatch):
    conn, dsn, real_db = db
    cur = conn.cursor()
    DL2, USER2 = "22222222-2222-2222-2222-222222222222", "dl_bbbbbbbbbbbbbbbb"
    cur.execute("INSERT INTO public.iot_dataloggers (id, tenant_id, name, kind, mqtt_username, created_at) "
                "VALUES (%s, %s, 'Box 2', 'datak', %s, now())", (DL2, TENANT, USER2))
    st = FakeState()
    st.iota_strict = True
    real_sync = gateways._sync_entity

    def slow(tenant, entity):
        time.sleep(0.25)
        return real_sync(tenant, entity)

    monkeypatch.setattr(gateways, "_sync_entity", slow)
    with wired(st) as w:
        dataloggers.get_db_connection_with_tenant = real_db
        first = [_run(gateways.project_gateway, TENANT, DL) for _ in range(2)]
        time.sleep(0.1)
        cur.execute("UPDATE public.iot_datalogger_devices SET datalogger_id = %s WHERE external_id = 'S2'", (DL2,))
        # an operation projects after its own commit, both loggers
        later = [_run(gateways.project_gateway, TENANT, d) for d in (DL, DL2)]
        for t, _ in first + later:
            t.join(60)
        outcomes = [box[0] for _, box in first + later]
        assert outcomes == ["done"] * 4, outcomes
        for dl, user, sensors in ((DL, USER, {"S1"}), (DL2, USER2, {"S2"})):
            cur.execute("SELECT gateway_external_id, gateway_provisioned_at FROM public.iot_dataloggers "
                        "WHERE id = %s", (dl,))
            ext, marker = cur.fetchone()
            entity = w.orion.device_models[f"urn:ngsi-ld:Device:{TENANT}:{ext}"]
            assets = entity["controlledAsset"]["object"]
            assets = assets if isinstance(assets, list) else [assets]
            assert sorted(assets) == sorted(f"urn:ngsi-ld:Device:{TENANT}:{s}" for s in sensors)
            assert marker is not None
            assert st.role_topics[user] == {f"/KEY123/{s}/attrs" for s in sensors} | {f"/KEY123/{ext}/attrs"}
    assert st.locked_network == []


def test_a_gateway_lock_that_is_held_makes_the_projection_busy_and_touches_nothing(db, monkeypatch):
    conn, dsn, real_db = db
    monkeypatch.setattr(gateways, "LOCK_TIMEOUT", "300ms")
    st = FakeState()
    holder = psycopg2.connect(dsn)
    holder.cursor().execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"gateway:{DL}",))
    try:
        with wired(st):
            dataloggers.get_db_connection_with_tenant = real_db
            assert gateways.project_gateway(TENANT, DL) == "failed:busy"
    finally:
        holder.rollback()
        holder.close()
    assert [e for e in st.log if e[0] in ("orion", "iota", "dynsec", "db")] == []
    cur = conn.cursor()
    cur.execute("SELECT gateway_external_id FROM public.iot_dataloggers WHERE id = %s", (DL,))
    assert cur.fetchone()[0] is None
