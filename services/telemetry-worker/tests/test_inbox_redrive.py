"""Redrive CLI: --dry-run counts and moves nothing; otherwise rows go back."""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from telemetry_worker import inbox_redrive
from telemetry_worker.inbox import NotificationInbox

UTC = timezone.utc
NOTIFIED = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


def test_parser_flags():
    args = inbox_redrive._build_parser().parse_args(
        ["--since", "2026-11-01T00:00:00Z", "--entity", "urn:e:1", "--dry-run"])
    assert args.since == datetime(2026, 11, 1, tzinfo=UTC)
    assert args.entity == "urn:e:1" and args.dry_run is True
    assert inbox_redrive._build_parser().parse_args([]).dry_run is False
    with pytest.raises(SystemExit):
        inbox_redrive._build_parser().parse_args(["--since", "not-a-date"])


def test_main_without_dsn_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert inbox_redrive.main(["--dry-run"]) == 2


DSN = os.getenv("INBOX_TEST_DSN")
MIGRATION = (
    Path(__file__).resolve().parents[3] / "config/timescaledb/migrations/119_notification_inbox.sql"
)
db = pytest.mark.skipif(not DSN, reason="INBOX_TEST_DSN not set")


@pytest.fixture(scope="module")
def dbname():
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

    if not DSN:
        yield None
        return
    asyncio.run(setup())
    yield name
    asyncio.run(teardown())


def run(dbname, scenario):
    import asyncpg

    async def main():
        pool = await asyncpg.create_pool(DSN, database=dbname, min_size=1, max_size=4)
        try:
            await pool.execute("TRUNCATE notification_inbox, notification_inbox_dead")
            return await scenario(pool)
        finally:
            await pool.close()

    return asyncio.run(main())


async def bury_two(pool):
    inbox = NotificationInbox(pool)
    ents = [{"id": f"urn:e:{i}", "type": "DeviceMeasurement"} for i in (1, 2)]
    await inbox.enqueue("acme", ents, NOTIFIED)
    for r in [r for rd in await inbox.claim_round(10) for r in rd]:
        await inbox.bury(r, "boom")
    return inbox


async def counts(pool):
    return (await pool.fetchval("SELECT count(*) FROM notification_inbox"),
            await pool.fetchval("SELECT count(*) FROM notification_inbox_dead"))


@db
def test_dry_run_reports_count_and_moves_nothing(dbname):
    async def s(pool):
        await bury_two(pool)
        n = await inbox_redrive.run_redrive(pool, "telemetry", None, None, dry_run=True)
        return n, await counts(pool)

    assert run(dbname, s) == (2, (0, 2))


@db
def test_redrive_moves_rows_back_with_attempts_reset(dbname):
    async def s(pool):
        await bury_two(pool)
        n = await inbox_redrive.run_redrive(pool, "telemetry", None, None, dry_run=False)
        attempts = await pool.fetch("SELECT attempts FROM notification_inbox")
        return n, await counts(pool), [a["attempts"] for a in attempts]

    assert run(dbname, s) == (2, (2, 0), [0, 0])


@db
def test_entity_and_since_filters(dbname):
    async def s(pool):
        await bury_two(pool)
        future = datetime.now(UTC) + timedelta(hours=1)
        none = await inbox_redrive.run_redrive(pool, "telemetry", future, None, dry_run=False)
        one = await inbox_redrive.run_redrive(pool, "telemetry", None, "urn:e:1", dry_run=False)
        return none, one, await counts(pool)

    assert run(dbname, s) == (0, 1, (1, 1))


@db
def test_main_uses_postgres_url(dbname, monkeypatch, capsys):
    import asyncpg

    async def prep():
        pool = await asyncpg.create_pool(DSN, database=dbname, min_size=1, max_size=2)
        try:
            await pool.execute("TRUNCATE notification_inbox, notification_inbox_dead")
            await bury_two(pool)
        finally:
            await pool.close()

    asyncio.run(prep())
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(DSN)
    monkeypatch.setenv("POSTGRES_URL", urlunsplit(parts._replace(path=f"/{dbname}")))
    assert inbox_redrive.main(["--dry-run"]) == 0
    assert "would redrive 2" in capsys.readouterr().out
    assert inbox_redrive.main([]) == 0
    assert "redriven 2" in capsys.readouterr().out
