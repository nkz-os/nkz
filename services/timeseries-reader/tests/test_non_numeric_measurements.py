"""Aligned telemetry queries must not cast text measurements to double precision.

payload.measurements carries text keys next to numeric readings (e.g.
sourceConfidence="OPEN-METEO"). An unguarded ::double precision aborts the whole
multi-series query; the guarded cast yields NULL for non-numeric values.
"""
import os
import re
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

if "auth_middleware" not in sys.modules:
    _auth_stub = MagicMock()
    _auth_stub.require_auth = lambda f: f
    _auth_stub.inject_fiware_headers = lambda headers, tenant=None: dict(headers or {})
    _auth_stub.internal_error = lambda e: ({"error": "internal"}, 500)
    sys.modules["auth_middleware"] = _auth_stub

import pytest  # noqa: E402

import app  # noqa: E402

START = datetime(2026, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _capture_conn():
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def _main_query(cursor):
    # First execute() is the RLS set_config; the second is the aligned query.
    sql, params = cursor.execute.call_args_list[-1].args
    return sql, params


def _assert_guarded(sql, params):
    assert "::double precision" in sql
    assert "NULLIF(trim(" not in sql, "unguarded cast still present"
    assert app._MEASUREMENT_AS_FLOAT_SQL in sql
    assert sql.count("%s") == len(params)


def test_telemetry_align_guards_cast_and_binds_key_twice():
    conn, cursor = _capture_conn()
    app._execute_telemetry_align_query(
        conn, "t1", START, END, 100,
        [("dev-1", "temperature"), ("dev-1", "sourceConfidence")],
        bucket_interval_override="1 hour",
    )
    sql, params = _main_query(cursor)
    _assert_guarded(sql, params)
    assert params.count("sourceConfidence") == 2


def test_unified_align_guards_cast_and_binds_key_twice(monkeypatch):
    monkeypatch.setattr(app, "TIMESERIES_STATS_ENGINE", "v1")
    conn, cursor = _capture_conn()
    app._execute_v2_align_unified_sql(
        conn, "t1", START, END, 100,
        [
            {"kind": "telemetry", "candidates": ["dev-1"], "attr": "temperature"},
            {"kind": "telemetry", "candidates": ["dev-1"], "attr": "sourceConfidence"},
        ],
        bucket_interval_override="1 hour",
    )
    sql, params = _main_query(cursor)
    _assert_guarded(sql, params)
    assert params.count("sourceConfidence") == 2


@pytest.mark.parametrize(
    "value,numeric",
    [
        ("12.5", True), ("-3", True), ("+0.25", True), (".5", True), ("7.", True),
        ("1e-3", True), ("2.5E+4", True), (" 4 ", True),
        ("OPEN-METEO", False), ("31013", True), ("", False), ("NaN", False),
        ("1.2.3", False), ("e5", False), ("-", False),
    ],
)
def test_numeric_text_pattern(value, numeric):
    """Same POSIX regex Postgres evaluates; Python re agrees on this subset."""
    assert bool(re.match(app._NUMERIC_TEXT_PATTERN, value)) is numeric
