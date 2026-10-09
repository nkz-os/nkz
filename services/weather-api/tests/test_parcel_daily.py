"""GET /api/weather/parcel/{id}/daily — one record per calendar day, gaps flagged."""
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import require_auth
import app.routers.parcel_daily as pd

PID = "da36ccd2-85d2-4c76-b552-c5c835a987c1"


def _row(day, **m):
    y, mo, d = map(int, day.split("-"))
    return {"observed_at": datetime(y, mo, d, 23, 59, 59, tzinfo=timezone.utc), "measurements": m}


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []
        self.closed = False

    def cursor(self, **kw):
        cur = MagicMock()
        cur.execute.side_effect = lambda q, p=None: self.executed.append((q, p))
        cur.fetchall.return_value = self.rows
        return cur

    def close(self):
        self.closed = True


@pytest.fixture
def client(monkeypatch):
    app.dependency_overrides[require_auth] = lambda: "tenant-a"
    holder = {}

    def install(rows):
        conn = _Conn(rows)
        holder["conn"] = conn
        monkeypatch.setattr(pd, "get_db_connection", lambda tid: conn)
        return conn

    holder["install"] = install
    yield TestClient(app), holder
    app.dependency_overrides.pop(require_auth, None)


FULL = dict(tempMin=11.2, tempMax=24.5, precipitation=3.4, et0=3.9,
            solarRadiation=179.398, vapourPressure=1.23, windSpeed2m=3.7)


def test_one_record_per_day_with_missing_flagged_not_filled(client):
    c, h = client
    conn = h["install"]([_row("2026-10-01", **FULL), _row("2026-10-03", tempMin=5.0)])
    r = c.get(f"/api/weather/parcel/{PID}/daily?start=2026-10-01&end=2026-10-03")
    assert r.status_code == 200
    body = r.json()
    assert [d["date"] for d in body["days"]] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    d1, d2, d3 = body["days"]
    assert d1["tmin_c"] == 11.2 and d1["tmax_c"] == 24.5 and d1["precip_mm"] == 3.4
    assert d1["et0_mm"] == 3.9 and d1["vapour_pressure_kpa"] == 1.23 and d1["wind2m_ms"] == 3.7
    assert d1["radiation_mj_m2"] == pytest.approx(179.398 * 0.0864, abs=1e-3)
    assert all(d2[k] is None for k in d2 if k != "date")  # absent day: nothing invented
    assert d3["tmin_c"] == 5.0 and d3["tmax_c"] is None and d3["et0_mm"] is None
    assert body["missing_days"] == ["2026-10-02"]
    assert body["units"]["radiation_mj_m2"] == "MJ m-2 d-1"
    assert body["units"]["tmin_c"] == "degC"
    assert "source" in body
    assert conn.closed


def test_query_is_tenant_and_parcel_scoped_with_bare_id(client):
    c, h = client
    conn = h["install"]([])
    c.get(f"/api/weather/parcel/urn:ngsi-ld:AgriParcel:{PID}/daily?start=2026-10-01&end=2026-10-02")
    q, params = conn.executed[0]
    assert "tenant_id = %s" in q and "telemetry_events" in q
    assert params[0] == "tenant-a"
    assert any(isinstance(p, str) and p.endswith(f":parcel-{PID}-daily") for p in params)


def test_bare_and_urn_ids_hit_same_entity(client):
    c, h = client
    c1 = h["install"]([]); c.get(f"/api/weather/parcel/{PID}/daily?start=2026-10-01&end=2026-10-01")
    p1 = c1.executed[0][1]
    c2 = h["install"]([]); c.get(f"/api/weather/parcel/urn:ngsi-ld:AgriParcel:{PID}/daily?start=2026-10-01&end=2026-10-01")
    assert p1 == c2.executed[0][1]


def test_like_wildcards_in_id_are_escaped(client):
    c, h = client
    conn = h["install"]([])
    c.get("/api/weather/parcel/a%25b_c/daily?start=2026-10-01&end=2026-10-01")
    pat = [p for p in conn.executed[0][1] if isinstance(p, str) and "parcel-" in p][0]
    assert "a\\%b\\_c" in pat


def test_duplicate_rows_same_day_last_wins(client):
    c, h = client
    h["install"]([_row("2026-10-01", tempMin=1.0), _row("2026-10-01", tempMin=2.0)])
    d = c.get(f"/api/weather/parcel/{PID}/daily?start=2026-10-01&end=2026-10-01").json()["days"][0]
    assert d["tmin_c"] == 2.0


def test_non_numeric_value_is_null_not_coerced(client):
    c, h = client
    h["install"]([_row("2026-10-01", tempMin="n/a", tempMax=True, et0=2.0)])
    d = c.get(f"/api/weather/parcel/{PID}/daily?start=2026-10-01&end=2026-10-01").json()["days"][0]
    assert d["tmin_c"] is None and d["tmax_c"] is None and d["et0_mm"] == 2.0


def test_row_outside_range_ignored(client):
    c, h = client
    h["install"]([_row("2026-09-30", tempMin=9.0)])
    body = c.get(f"/api/weather/parcel/{PID}/daily?start=2026-10-01&end=2026-10-01").json()
    assert body["days"][0]["tmin_c"] is None and body["missing_days"] == ["2026-10-01"]


@pytest.mark.parametrize("qs", [
    "start=2026-10-05&end=2026-10-01",       # reversed
    "start=2026-13-01&end=2026-13-02",       # bad date
    "start=abc&end=2026-10-01",
    "start=2025-01-01&end=2026-10-01",       # > 400 days
    "start=2026-10-01",                      # missing end
])
def test_validation_errors(client, qs):
    c, h = client
    h["install"]([])
    r = c.get(f"/api/weather/parcel/{PID}/daily?{qs}")
    assert r.status_code in (400, 422)


def test_max_range_400_days_inclusive_ok(client):
    c, h = client
    h["install"]([])
    r = c.get(f"/api/weather/parcel/{PID}/daily?start=2025-01-01&end=2026-02-04")  # 400 days
    assert r.status_code == 200 and len(r.json()["days"]) == 400


def test_db_failure_is_503_not_empty_series(client, monkeypatch):
    c, h = client

    def boom(tid):
        raise RuntimeError("db down")

    monkeypatch.setattr(pd, "get_db_connection", boom)
    r = c.get(f"/api/weather/parcel/{PID}/daily?start=2026-10-01&end=2026-10-02")
    assert r.status_code == 503


def test_requires_tenant_header():
    app.dependency_overrides.pop(require_auth, None)
    r = TestClient(app).get(f"/api/weather/parcel/{PID}/daily?start=2026-10-01&end=2026-10-02")
    assert r.status_code == 401
