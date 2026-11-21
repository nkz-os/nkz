"""Durable notification inbox store: pure helpers and Postgres-backed queue."""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from telemetry_worker.inbox import InboxRow, NotificationInbox, observed_at_for

UTC = timezone.utc
T1 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 1, 11, 0, tzinfo=UTC)
RECEIVED = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
NOTIFIED = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


def dm(entity_id, when):
    return {
        "id": entity_id,
        "type": "DeviceMeasurement",
        "dateObserved": {"type": "Property", "value": when.isoformat()},
    }


def parcel(entity_id):
    return {"id": entity_id, "type": "AgriParcel", "name": {"type": "Property", "value": "x"}}


# --- pure -------------------------------------------------------------------


def test_observed_at_device_measurement_uses_date_observed():
    assert observed_at_for(dm("urn:dm:1", T1), NOTIFIED, RECEIVED) == T1


def test_observed_at_other_type_uses_notified_at():
    assert observed_at_for(parcel("urn:p:1"), NOTIFIED, RECEIVED) == NOTIFIED


def test_observed_at_other_type_prefers_attribute_observed_at():
    ent = parcel("urn:p:1")
    ent["temp"] = {"type": "Property", "value": 1, "observedAt": T2.isoformat()}
    assert observed_at_for(ent, NOTIFIED, RECEIVED) == T2


def test_observed_at_nothing_falls_back_to_received_at():
    assert observed_at_for(parcel("urn:p:1"), None, RECEIVED) == RECEIVED


def test_observed_at_device_measurement_without_date_falls_back():
    ent = {"id": "urn:dm:1", "type": "DeviceMeasurement"}
    assert observed_at_for(ent, NOTIFIED, RECEIVED) == NOTIFIED
    assert observed_at_for(ent, None, RECEIVED) == RECEIVED


def test_observed_at_unparseable_observed_at_does_not_fail():
    ent = parcel("urn:p:1")
    ent["temp"] = {"type": "Property", "value": 1, "observedAt": "garbage"}
    assert observed_at_for(ent, None, RECEIVED) == RECEIVED


def test_inbox_row_is_frozen():
    row = InboxRow(1, "acme", "e", "t", T1, {}, 0)
    with pytest.raises(Exception):
        row.attempts = 3  # type: ignore[misc]


# --- Postgres ---------------------------------------------------------------

DSN = os.getenv("INBOX_TEST_DSN")
MIGRATION = (
    Path(__file__).resolve().parents[3] / "config/timescaledb/migrations/119_notification_inbox.sql"
)
db = pytest.mark.skipif(not DSN, reason="INBOX_TEST_DSN not set")


@pytest.fixture(scope="module")
def dbname():
    """Fresh database per module, migration 119 applied, dropped afterwards."""
    import asyncpg

    name = f"inbox_test_{uuid.uuid4().hex[:8]}"

    async def setup():
        admin = await asyncpg.connect(DSN)
        try:
            exists = await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname='nkz_core'")
            if not exists:
                await admin.execute("CREATE ROLE nkz_core NOLOGIN")
            await admin.execute(f'CREATE DATABASE "{name}"')
        finally:
            await admin.close()
        conn = await asyncpg.connect(DSN, database=name)
        try:
            await conn.execute(MIGRATION.read_text())
        finally:
            await conn.close()

    async def teardown():
        admin = await asyncpg.connect(DSN)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()

    asyncio.run(setup())
    yield name
    asyncio.run(teardown())


def run(dbname, scenario):
    """Run `scenario(inbox, pool)` on a clean inbox in its own event loop."""
    import asyncpg

    async def main():
        pool = await asyncpg.create_pool(DSN, database=dbname, min_size=1, max_size=4)
        try:
            await pool.execute("TRUNCATE notification_inbox, notification_inbox_dead")
            return await scenario(NotificationInbox(pool), pool)
        finally:
            await pool.close()

    return asyncio.run(main())


def key(row):
    return (row.entity_id, row.observed_at)


@db
def test_enqueue_three_entities(dbname):
    async def s(inbox, pool):
        n = await inbox.enqueue("acme", [dm("e1", T1), dm("e2", T1), parcel("p1")], NOTIFIED)
        return n, await pool.fetchval("SELECT count(*) FROM notification_inbox")

    assert run(dbname, s) == (3, 3)


@db
def test_enqueue_skips_malformed(dbname):
    async def s(inbox, pool):
        bad = [{"type": "DeviceMeasurement"}, {"id": "x"}, {"id": 5, "type": "T"}]
        n = await inbox.enqueue("acme", [dm("e1", T1), *bad, dm("e2", T1)], None)
        return n, await pool.fetchval("SELECT count(*) FROM notification_inbox")

    assert run(dbname, s) == (2, 2)


@db
def test_enqueue_empty_and_all_bad_insert_nothing(dbname):
    async def s(inbox, pool):
        return await inbox.enqueue("acme", [], None), await inbox.enqueue("acme", [{}], None)

    assert run(dbname, s) == (0, 0)


@db
def test_enqueue_stores_fields(dbname):
    async def s(inbox, pool):
        await inbox.enqueue("acme", [parcel("p1")], NOTIFIED)
        return await pool.fetchrow("SELECT * FROM notification_inbox")

    r = run(dbname, s)
    assert (r["queue"], r["tenant_id"], r["entity_id"], r["entity_type"]) == (
        "telemetry", "acme", "p1", "AgriParcel")
    assert r["observed_at"] == NOTIFIED and r["notified_at"] == NOTIFIED
    assert r["attempts"] == 0 and r["payload"]


@db
def test_enqueue_raises_on_db_failure(dbname):
    async def s(inbox, pool):
        await pool.close()
        with pytest.raises(Exception):
            await inbox.enqueue("acme", [dm("e1", T1)], None)

    import asyncpg

    async def main():
        pool = await asyncpg.create_pool(DSN, database=dbname, min_size=1, max_size=1)
        await s(NotificationInbox(pool), pool)

    asyncio.run(main())


@db
def test_claim_round_orders_out_of_order_arrivals(dbname):
    async def s(inbox, pool):
        await inbox.enqueue("acme", [dm("E", T2)], None)
        await inbox.enqueue("acme", [dm("E", T1), dm("F", T1)], None)
        return await inbox.claim_round(100)

    rounds = run(dbname, s)
    assert [[key(r) for r in rd] for rd in rounds] == [[("E", T1), ("F", T1)], [("E", T2)]]
    assert all(isinstance(r, InboxRow) and r.attempts == 0 for rd in rounds for r in rd)


@db
def test_claim_round_ties_break_by_id(dbname):
    async def s(inbox, pool):
        await inbox.enqueue("acme", [dm("E", T1), dm("F", T1), dm("E", T1)], None)
        return await inbox.claim_round(100)

    rounds = run(dbname, s)
    assert [[r.entity_id for r in rd] for rd in rounds] == [["E", "F"], ["E"]]
    assert rounds[0][0].id < rounds[1][0].id


@db
def test_claim_round_limit_keeps_entity_order(dbname):
    async def s(inbox, pool):
        await inbox.enqueue("acme", [dm("E", T2), dm("E", T1), dm("F", T2)], None)
        return await inbox.claim_round(2)

    rounds = run(dbname, s)
    flat = [key(r) for rd in rounds for r in rd]
    assert flat == [("E", T1), ("E", T2)]


@db
def test_claim_round_empty(dbname):
    async def s(inbox, pool):
        return await inbox.claim_round(10)

    assert run(dbname, s) == []


@db
def test_tenants_are_separate_entities(dbname):
    async def s(inbox, pool):
        await inbox.enqueue("acme", [dm("E", T1)], None)
        await inbox.enqueue("tenant-a", [dm("E", T2)], None)
        return await inbox.claim_round(10)

    rounds = run(dbname, s)
    assert len(rounds) == 1 and len(rounds[0]) == 2


@db
def test_retry_holds_back_entity_later_rows(dbname):
    async def s(inbox, pool):
        await inbox.enqueue("acme", [dm("E", T1), dm("E", T2), dm("F", T1)], None)
        first = (await inbox.claim_round(10))[0]
        e1 = next(r for r in first if r.entity_id == "E")
        await inbox.retry(e1, "pg down")
        after = await inbox.claim_round(10)
        row = await pool.fetchrow("SELECT attempts, last_error, available_at - now() AS wait "
                                  "FROM notification_inbox WHERE id=$1", e1.id)
        return after, row

    after, row = run(dbname, s)
    assert [[key(r) for r in rd] for rd in after] == [[("F", T1)]]
    assert row["attempts"] == 1 and row["last_error"] == "pg down"
    assert timedelta(seconds=0) < row["wait"] <= timedelta(seconds=2)


@db
def test_retry_backoff_is_capped(dbname):
    async def s(inbox, pool):
        await inbox.enqueue("acme", [dm("E", T1)], None)
        row = (await inbox.claim_round(10))[0][0]
        waits = []
        for n in range(1, 12):
            await inbox.retry(row, "x")
            w = await pool.fetchval(
                "SELECT extract(epoch FROM available_at - now()) FROM notification_inbox")
            waits.append(round(w))
            row = InboxRow(row.id, row.tenant_id, row.entity_id, row.entity_type,
                           row.observed_at, row.payload, n)
        return waits

    waits = run(dbname, s)
    expected = [2, 4, 8, 16, 32, 64, 128, 256, 300, 300, 300]
    assert all(abs(w - e) <= 1 for w, e in zip(waits, expected)), waits


@db
def test_twelfth_retry_buries(dbname):
    async def s(inbox, pool):
        await inbox.enqueue("acme", [dm("E", T1)], None)
        row = (await inbox.claim_round(10))[0][0]
        for _ in range(11):
            await inbox.retry(row, "x")
        live_before = await pool.fetchval("SELECT count(*) FROM notification_inbox")
        await inbox.retry(row, "last")
        return (live_before,
                await pool.fetchval("SELECT count(*) FROM notification_inbox"),
                await pool.fetchrow("SELECT id, attempts, last_error, died_at FROM notification_inbox_dead"),
                row.id)

    live_before, live_after, dead, rid = run(dbname, s)
    assert live_before == 1 and live_after == 0
    assert dead["id"] == rid and dead["attempts"] == 12 and dead["last_error"] == "last"
    assert dead["died_at"] is not None


@db
def test_bury_moves_row_atomically_and_is_idempotent(dbname):
    async def s(inbox, pool):
        await inbox.enqueue("acme", [dm("E", T1)], None)
        row = (await inbox.claim_round(10))[0][0]
        await inbox.bury(row, "boom")
        await inbox.bury(row, "boom")
        return (await pool.fetchval("SELECT count(*) FROM notification_inbox"),
                await pool.fetchval("SELECT count(*) FROM notification_inbox_dead"))

    assert run(dbname, s) == (0, 1)


@db
def test_redrive_brings_dead_back_with_attempts_zero(dbname):
    async def s(inbox, pool):
        await inbox.enqueue("acme", [dm("E", T1), dm("F", T1)], None)
        rows = (await inbox.claim_round(10))[0]
        for r in rows:
            await inbox.bury(r, "boom")
        e = next(r for r in rows if r.entity_id == "E")
        moved = await inbox.redrive(None, "E")
        dead_left = await pool.fetchval("SELECT count(*) FROM notification_inbox_dead")
        back = await inbox.claim_round(10)
        moved_all = await inbox.redrive(datetime.now(UTC) - timedelta(hours=1), None)
        return e.id, moved, dead_left, back, moved_all

    eid, moved, dead_left, back, moved_all = run(dbname, s)
    assert moved == 1 and dead_left == 1 and moved_all == 1
    assert [[(r.id, r.attempts) for r in rd] for rd in back] == [[(eid, 0)]]


@db
def test_redrive_since_filters_by_died_at(dbname):
    async def s(inbox, pool):
        await inbox.enqueue("acme", [dm("E", T1)], None)
        row = (await inbox.claim_round(10))[0][0]
        await inbox.bury(row, "boom")
        future = datetime.now(UTC) + timedelta(hours=1)
        return await inbox.redrive(future, None)

    assert run(dbname, s) == 0


@db
def test_ack_deletes(dbname):
    async def s(inbox, pool):
        await inbox.enqueue("acme", [dm("E", T1), dm("F", T1)], None)
        rows = (await inbox.claim_round(10))[0]
        await inbox.ack([rows[0].id])
        await inbox.ack([])
        return await pool.fetchval("SELECT count(*) FROM notification_inbox")

    assert run(dbname, s) == 1


@db
def test_stats_empty_and_with_rows(dbname):
    async def s(inbox, pool):
        empty = await inbox.stats()
        await inbox.enqueue("acme", [dm("E", T1), dm("F", T1)], None)
        await pool.execute("UPDATE notification_inbox SET received_at = now() - interval '30 seconds'")
        return empty, await inbox.stats()

    empty, (depth, age) = run(dbname, s)
    assert empty == (0, 0.0)
    assert depth == 2 and 29 <= age < 40


@db
def test_queues_are_isolated(dbname):
    async def s(inbox, pool):
        other = NotificationInbox(pool, queue="other")
        await inbox.enqueue("acme", [dm("E", T1)], None)
        return await other.claim_round(10), await other.stats()

    assert run(dbname, s) == ([], (0, 0.0))
