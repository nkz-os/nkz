"""Support welcome backfill: who gets it, and that a re-run sends nothing twice."""

from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))
backfill = importlib.import_module("send_support_welcome")

CUTOFF = 2_000


def _user(uid, *, email="u@example.com", tenant="acme", enabled=True, created=1_000, attr="tenant_id"):
    attrs = {attr: [tenant]} if tenant else {}
    return {"id": uid, "email": email, "enabled": enabled, "createdTimestamp": created, "attributes": attrs}


def test_eligible_filters():
    users = [
        _user("ok"),
        _user("legacy-attr", attr="tenant"),
        _user("disabled", enabled=False),
        _user("no-email", email=""),
        _user("no-tenant", tenant=""),
        _user("new", created=CUTOFF),
        _user("other", tenant="tenant-b"),
    ]
    ids = [r.user_id for r in backfill.eligible(users, CUTOFF, None)]
    assert ids == ["ok", "legacy-attr", "other"]
    assert [r.user_id for r in backfill.eligible(users, CUTOFF, "tenant-b")] == ["other"]


class FakeConn:
    """Minimal psycopg2 stand-in backed by a set of recorded user ids."""

    def __init__(self, recorded=()):
        self.recorded = set(recorded)
        self.commits = 0

    def cursor(self):
        conn = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, query, params=None):
                if query.startswith("INSERT"):
                    conn.recorded.add(params[0])

            def fetchall(self):
                return [(u,) for u in conn.recorded]

        return Cur()

    def commit(self):
        self.commits += 1


def _recipients(*ids):
    return [backfill.Recipient(i, f"{i}@example.com", "acme") for i in ids]


def test_sends_and_records_only_pending():
    conn = FakeConn(recorded={"a"})
    send = MagicMock(return_value=True)
    stats = backfill.run(_recipients("a", "b", "c"), conn, send, dry_run=False, sleep_s=0)
    assert [c.args[0] for c in send.call_args_list] == ["b@example.com", "c@example.com"]
    assert conn.recorded == {"a", "b", "c"}
    assert stats == {"eligible": 3, "already_sent": 1, "sent": 2}


def test_rerun_sends_nothing():
    conn = FakeConn()
    backfill.run(_recipients("a", "b"), conn, MagicMock(return_value=True), dry_run=False, sleep_s=0)
    send = MagicMock(return_value=True)
    stats = backfill.run(_recipients("a", "b"), conn, send, dry_run=False, sleep_s=0)
    send.assert_not_called()
    assert stats["sent"] == 0


def test_dry_run_sends_and_records_nothing():
    conn = FakeConn()
    send = MagicMock(return_value=True)
    backfill.run(_recipients("a"), conn, send, dry_run=True, sleep_s=0)
    send.assert_not_called()
    assert conn.recorded == set()


def test_failure_stops_and_is_not_recorded():
    conn = FakeConn()
    send = MagicMock(side_effect=[True, False, True])
    stats = backfill.run(_recipients("a", "b", "c"), conn, send, dry_run=False, sleep_s=0)
    assert conn.recorded == {"a"}
    assert stats["failed_user_id"] == "b"
    assert send.call_count == 2


def test_cutoff_requires_timezone():
    with pytest.raises(SystemExit):
        backfill.main(["--created-before", "2026-10-28T12:00:00"])
