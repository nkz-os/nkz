"""The row limit applies after the attribute filter, not to every row of the device."""
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

if "auth_middleware" not in sys.modules:
    _auth_stub = MagicMock()
    _auth_stub.require_auth = lambda f: f
    _auth_stub.inject_fiware_headers = lambda headers, tenant=None: dict(headers or {})
    _auth_stub.internal_error = lambda e: ({"error": "internal"}, 500)
    sys.modules["auth_middleware"] = _auth_stub

import app  # noqa: E402

START = datetime(2026, 10, 20)
END = datetime(2026, 10, 21)


def _run(attrs):
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchall.return_value = []
    app._fetch_telemetry_rows(conn, "tenant-a", ["dev-1"], START, END, 10, attrs=attrs)
    sql, params = cur.execute.call_args_list[-1].args
    return " ".join(sql.split()), params


def test_requested_attributes_filter_rows_before_the_limit():
    sql, params = _run(["waterConsumption"])
    assert "payload->'measurements' ?| %s::text[]" in sql
    assert "payload->'raw_measurements' ?| %s::text[]" in sql
    assert sql.index("?|") < sql.index("LIMIT")
    assert params[-3:] == (["waterConsumption"], ["waterConsumption"], 10)


def test_without_attributes_every_row_counts():
    sql, params = _run(None)
    assert "?|" not in sql
    assert params[-1] == 10


@patch("app._fetch_telemetry_rows")
@patch("app.get_db_connection")
@patch("app.plan_timeseries_read")
def test_v2_route_passes_the_attributes(plan, db_conn, fetch):
    plan.return_value = {"mode": "telemetry", "device_candidates": ["dev-1"]}
    db_conn.return_value.__enter__.return_value = MagicMock()
    fetch.return_value = []
    with app.app.test_client() as c:
        r = c.get(
            "/api/timeseries/v2/entities/urn:ngsi-ld:Device:dev-1/data"
            "?time_from=2026-10-20T00:00:00Z&time_to=2026-10-21T00:00:00Z&attrs=waterConsumption",
            headers={"X-Tenant-ID": "tenant-a"},
        )
    assert r.status_code == 200, r.get_data(as_text=True)
    assert fetch.call_args.kwargs["attrs"] == ["waterConsumption"]
