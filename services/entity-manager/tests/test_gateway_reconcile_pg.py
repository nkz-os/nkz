"""The repair job with the real locks, on a throwaway PostgreSQL (Orion, agent and broker are fakes).

Skipped unless GATEWAY_PROJECTION_PG_URL is set (run-em-pg.sh in the task directory).
"""
import logging
import threading
import time

import psycopg2
import pytest

from tests.telemetry_fakes import FakeState, TENANT, wired
from tests.test_gateway_projection_pg import ADMIN_URL, DL, USER, db, dsn  # noqa: F401  (fixtures)

import dataloggers  # noqa: E402
import gateway_reconcile as gr  # noqa: E402
import gateways  # noqa: E402

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="GATEWAY_PROJECTION_PG_URL not set")

DL2 = "22222222-2222-2222-2222-222222222222"
ORPHAN = "gw-aaaaaaaaaaaa"
DEAD = "gw-bbbbbbbbbbbb"


def _urn(ext):
    return f"urn:ngsi-ld:Device:{TENANT}:{ext}"


def _run(target, *args):
    box = []
    t = threading.Thread(target=lambda: box.append(target(*args)))
    t.start()
    return t, box


def _seed_gateway(st, w, ext):
    """A gateway as the projection leaves it: the agent device and the entity."""
    st.iota_devices[ext] = {"device_id": ext, "entity_type": "Device", "entity_name": _urn(ext),
                            "attributes": []}
    w.orion.device_models[_urn(ext)] = {"id": _urn(ext), "type": "Device",
                                        "deviceCategory": {"type": "Property", "value": ["network"]}}


def _fake_world(st, w, monkeypatch):
    """Listings from the fakes; the Orion delete reaches the entities the SDK upsert created."""
    monkeypatch.setattr(gr, "list_orion_devices", lambda t: list(w.orion.device_models.values()))
    monkeypatch.setattr(gr, "list_agent_devices",
                        lambda t: [dict(d) for d in list(st.iota_devices.values())])

    def delete_entity(tenant, urn):
        st.log.append(("orion", "delete", urn))
        w.orion.device_models.pop(urn, None)
        w.orion.entities.pop(urn, None)

    monkeypatch.setattr(dataloggers, "delete_orion_entity", delete_entity)


@pytest.fixture
def pg(db, monkeypatch):  # noqa: F811
    conn, dsn_, real_db = db
    monkeypatch.setenv("POSTGRES_URL", dsn_)
    return conn, dsn_, real_db


def _deleted(st, *what):
    return [e for e in st.log if e[:2] in (("iota", "delete"), ("orion", "delete")) and e[2] in what]


# ── the job lock ─────────────────────────────────────────────────────────────────────────

def test_a_second_run_exits_zero_while_the_first_holds_the_job_lock(pg, monkeypatch):
    conn, dsn_, real_db = pg
    holder = psycopg2.connect(dsn_)
    holder.cursor().execute("SELECT pg_advisory_lock(hashtext(%s))", (gr.JOB_LOCK,))
    holder.commit()
    called = []
    monkeypatch.setattr(gr, "list_active_tenants", lambda: called.append("listed") or [])
    try:
        assert gr.main([]) == 0
        assert called == []  # it did nothing
    finally:
        holder.close()  # the session ends: the lock is released
    assert gr.main([]) == 0
    assert called == ["listed"]


def test_the_job_lock_is_released_when_the_run_ends(pg):
    conn, dsn_, real_db = pg
    with gr.job_lock() as first:
        with gr.job_lock() as second:
            assert first is True and second is False
    with gr.job_lock() as again:
        assert again is True


# ── the guard ────────────────────────────────────────────────────────────────────────────

def test_the_guard_reads_the_table_under_the_orphan_lock(pg):
    conn, dsn_, real_db = pg
    cur = conn.cursor()
    with gr.orphan_guard(TENANT, ORPHAN, False) as verdict:
        assert verdict is None
        cur.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted")
        assert cur.fetchone()[0] >= 1
    cur.execute("UPDATE public.iot_dataloggers SET gateway_external_id = %s WHERE id = %s", (ORPHAN, DL))
    with gr.orphan_guard(TENANT, ORPHAN, False) as verdict:
        assert verdict == "now in the table"
    cur.execute("UPDATE public.iot_dataloggers SET gateway_external_id = NULL WHERE id = %s", (DL,))
    cur.execute("UPDATE public.tenants SET deleted_at = now() WHERE tenant_id = %s", (TENANT,))
    with gr.orphan_guard(TENANT, ORPHAN, False) as verdict:
        assert verdict == "tenant suspended"


def test_an_id_that_enters_the_table_between_the_listing_and_the_removal_is_kept(pg, monkeypatch):
    conn, dsn_, real_db = pg
    st = FakeState()
    cur = conn.cursor()
    with wired(st) as w:
        dataloggers.get_db_connection_with_tenant = real_db
        _fake_world(st, w, monkeypatch)
        _seed_gateway(st, w, ORPHAN)
        cur.execute("INSERT INTO public.iot_dataloggers (id, tenant_id, name, kind, mqtt_username, created_at) "
                    "VALUES (%s, %s, 'Box 2', 'datak', 'dl_bbbbbbbbbbbbbbbb', now())", (DL2, TENANT))
        listing = gr.list_agent_devices

        def listed_then_claimed(tenant):
            found = listing(tenant)  # ORPHAN is not in the table here...
            cur.execute("UPDATE public.iot_dataloggers SET gateway_external_id = %s, "
                        "gateway_provisioned_at = now() WHERE id = %s", (ORPHAN, DL2))  # ...and then it is
            return found

        monkeypatch.setattr(gr, "list_agent_devices", listed_then_claimed)
        monkeypatch.setattr(gateways, "project_gateway_detailed", lambda t, d: ("done", None))
        assert gr.main([]) == 0
    assert _deleted(st, ORPHAN, _urn(ORPHAN)) == []
    assert ORPHAN in st.iota_devices and _urn(ORPHAN) in w.orion.device_models


def test_the_guard_refuses_a_sensor_id_and_an_agent_device_of_another_entity(pg, monkeypatch):
    conn, dsn_, real_db = pg
    st = FakeState()
    cur = conn.cursor()
    with wired(st) as w:
        dataloggers.get_db_connection_with_tenant = real_db
        # a sensor registered under the gateway id (before the id was reserved)
        cur.execute("INSERT INTO public.iot_datalogger_devices (datalogger_id, tenant_id, device_urn, external_id) "
                    "VALUES (%s, %s, %s, %s)", (DL, TENANT, _urn("S9"), ORPHAN))
        with gr.orphan_guard(TENANT, ORPHAN, False) as verdict:
            assert verdict == "is a sensor"
        cur.execute("DELETE FROM public.iot_datalogger_devices WHERE external_id = %s", (ORPHAN,))
        # the agent device of that id points at a sensor entity
        st.iota_devices[DEAD] = {"device_id": DEAD, "entity_type": "Device", "entity_name": _urn("S1"),
                                 "attributes": []}
        with gr.orphan_guard(TENANT, DEAD, True) as verdict:
            assert verdict == "agent device is not this gateway's"
        with gr.orphan_guard(TENANT, DEAD, False) as verdict:  # no agent removal asked: nothing to refuse
            assert verdict is None
        st.iota_devices[DEAD]["entity_name"] = _urn(DEAD)
        with gr.orphan_guard(TENANT, DEAD, True) as verdict:
            assert verdict is None
        st.iota_devices.pop(DEAD)  # gone meanwhile: nothing to refuse either
        with gr.orphan_guard(TENANT, DEAD, True) as verdict:
            assert verdict is None


def test_a_sensors_agent_device_with_a_gateway_id_survives_the_whole_job(pg, monkeypatch):
    conn, dsn_, real_db = pg
    st = FakeState()
    with wired(st) as w:
        dataloggers.get_db_connection_with_tenant = real_db
        _fake_world(st, w, monkeypatch)
        st.iota_devices[ORPHAN] = {"device_id": ORPHAN, "entity_type": "Device",
                                   "entity_name": _urn("S1"), "attributes": []}
        monkeypatch.setattr(gateways, "project_gateway_detailed", lambda t, d: ("done", None))
        assert gr.main([]) == 0
    assert ORPHAN in st.iota_devices and _deleted(st, ORPHAN) == []


# ── R18: projection, deletion and the job at the same time ───────────────────────────────

def test_a_deletion_that_starts_while_a_projection_holds_the_gateway_lock_runs_after_it(pg, monkeypatch):
    conn, dsn_, real_db = pg
    st = FakeState()
    st.iota_strict = True
    cur = conn.cursor()
    entered, release = threading.Event(), threading.Event()
    real_sync = gateways._sync_entity

    def slow(tenant, entity):
        entered.set()
        assert release.wait(30)
        return real_sync(tenant, entity)

    monkeypatch.setattr(gateways, "_sync_entity", slow)
    with wired(st) as w:
        dataloggers.get_db_connection_with_tenant = real_db
        _fake_world(st, w, monkeypatch)
        projection, pout = _run(gateways.project_gateway, TENANT, DL)
        assert entered.wait(15)  # inside Orion, holding gateway:<dl>
        cur.execute("SELECT gateway_external_id FROM public.iot_dataloggers WHERE id = %s", (DL,))
        ext = cur.fetchone()[0]
        assert gateways.is_gateway_external_id(ext)
        # the user deletes the datalogger: its row is gone and committed, then the deletion starts
        cur.execute("DELETE FROM public.iot_datalogger_devices WHERE datalogger_id = %s", (DL,))
        cur.execute("DELETE FROM public.iot_dataloggers WHERE id = %s", (DL,))
        deletion, dout = _run(gateways.delete_gateway, TENANT, DL, ext)
        deletion.join(1.0)
        assert deletion.is_alive(), "the deletion did not wait for the projection"
        assert _deleted(st, ext, _urn(ext)) == []
        release.set()
        projection.join(30)
        deletion.join(30)
        assert not deletion.is_alive()
    # nothing is left behind, and the deletion came after the projection's writes
    assert st.iota_devices == {} and w.orion.device_models == {}
    kinds = [e[:2] for e in st.log if e[0] in ("iota", "orion") and e[1] in ("provision", "upsert", "delete")]
    assert kinds.index(("iota", "provision")) < kinds.index(("iota", "delete"))
    assert kinds.index(("orion", "upsert")) < kinds.index(("orion", "delete"))


def test_the_job_a_projection_and_a_deletion_at_once_converge(pg, monkeypatch, caplog):
    conn, dsn_, real_db = pg
    st = FakeState()
    st.iota_strict = True
    cur = conn.cursor()
    real_sync = gateways._sync_entity

    def slow(tenant, entity):
        time.sleep(0.3)
        return real_sync(tenant, entity)

    monkeypatch.setattr(gateways, "_sync_entity", slow)
    with wired(st) as w:
        dataloggers.get_db_connection_with_tenant = real_db
        _fake_world(st, w, monkeypatch)
        _seed_gateway(st, w, ORPHAN)   # no row: the job removes it
        _seed_gateway(st, w, DEAD)     # its row was just deleted: the deletion removes it
        with caplog.at_level(logging.INFO):
            projection, pout = _run(gateways.project_gateway, TENANT, DL)
            deletion, dout = _run(gateways.delete_gateway, TENANT, DL2, DEAD)
            job, jout = _run(gr.main, [])
            for t in (projection, deletion, job):
                t.join(60)
            assert not any(t.is_alive() for t in (projection, deletion, job))
            assert gr.main([]) == 0  # one more run: nothing is left to converge
        cur.execute("SELECT gateway_external_id, gateway_provisioned_at FROM public.iot_dataloggers WHERE id = %s",
                    (DL,))
        ext, marker = cur.fetchone()
        assert gateways.is_gateway_external_id(ext) and marker is not None
        # exactly the live gateway remains, in the agent and in Orion
        assert set(st.iota_devices) == {ext}
        assert set(w.orion.device_models) == {_urn(ext)}
        # and it was never touched by a removal, at any moment
        assert _deleted(st, ext, _urn(ext)) == []
        assert jout == [0] and pout == ["done"]
    assert st.locked_network == []
