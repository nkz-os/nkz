"""The probe must fail for a broken path and only for a broken path."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from telemetry_worker import path_check


def _now():
    return datetime(2026, 9, 17, 10, 30, 0, tzinfo=timezone.utc)


def test_the_reading_changes_between_runs():
    """A constant value is dropped by the delta filter and reads as a break."""
    a = path_check.build_entity("t1", _now())
    b = path_check.build_entity("t1", _now() + timedelta(minutes=10))
    assert a["batteryLevel"]["value"] != b["batteryLevel"]["value"]


def test_the_entity_id_is_stable():
    """One synthetic entity in the broker, not one per run."""
    a = path_check.build_entity("t1", _now())
    b = path_check.build_entity("t1", _now() + timedelta(days=3))
    assert a["id"] == b["id"] == path_check.entity_id_for("t1")
    assert path_check.SYNTHETIC_DEVICE in a["id"], "the row must be identifiable"


def test_the_entity_carries_a_numeric_property():
    """No measurement means no row, whatever the pipeline does."""
    entity = path_check.build_entity("t1", _now())
    assert entity["batteryLevel"]["type"] == "Property"
    assert isinstance(entity["batteryLevel"]["value"], (int, float))


def _cursor_returning(rows):
    cur = MagicMock()
    cur.fetchone.side_effect = rows
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cur)
    ctx.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = ctx
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn, cur


def test_a_row_from_this_run_passes():
    conn, cur = _cursor_returning([(1,)])
    with patch.object(path_check.psycopg2, "connect", return_value=conn):
        assert path_check.wait_for_row("dsn", "urn:x", _now(), timeout_s=1) is True
    # The window is part of the query: an old row must not answer for a new run.
    args = cur.execute.call_args[0]
    assert "observed_at >= %s" in args[0]
    assert args[1] == ("urn:x", _now())


def test_no_row_within_the_timeout_fails():
    conn, _ = _cursor_returning([None, None, None, None])
    with patch.object(path_check.psycopg2, "connect", return_value=conn), \
         patch.object(path_check.time, "sleep"):
        assert path_check.wait_for_row("dsn", "urn:x", _now(), timeout_s=0) is False


def test_a_broker_that_rejects_the_write_fails_immediately(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://x/y")
    monkeypatch.setattr(path_check, "_headers", lambda t: {})
    monkeypatch.setattr(
        path_check, "publish",
        MagicMock(side_effect=RuntimeError("broker refused")),
    )
    waited = MagicMock()
    monkeypatch.setattr(path_check, "wait_for_row", waited)
    assert path_check.main() == 1
    assert not waited.called, "no point waiting for a row that was never written"


def test_missing_dsn_is_reported_not_assumed_healthy(monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    published = MagicMock()
    monkeypatch.setattr(path_check, "publish", published)
    assert path_check.main() == 1
    assert not published.called


@pytest.mark.parametrize("found,expected", [(True, 0), (False, 1)])
def test_exit_code_reports_the_path(monkeypatch, found, expected):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://x/y")
    monkeypatch.setattr(path_check, "_headers", lambda t: {})
    monkeypatch.setattr(path_check, "publish", MagicMock())
    monkeypatch.setattr(path_check, "wait_for_row", MagicMock(return_value=found))
    assert path_check.main() == expected
