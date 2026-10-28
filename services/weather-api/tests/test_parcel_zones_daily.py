"""GET /api/weather/parcel/{id}/zones/daily — per-zone closed-day series."""
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
import app.routers.parcel_zones_daily as pz

PID = "p1"


def _row(zone, day, hour=12, **m):
    y, mo, d = map(int, day.split("-"))
    return {"zone": zone, "observed_at": datetime(y, mo, d, hour, tzinfo=timezone.utc),
            "measurements": {"tMin": 1.0, "tMax": 9.0, "eto": 2.0, **m}}


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
    app.dependency_overrides[require_auth] = lambda: "acme"
    holder = {}

    def install(rows):
        conn = _Conn(rows)
        holder["conn"] = conn
        monkeypatch.setattr(pz, "get_db_connection", lambda tid: conn)
        return conn

    monkeypatch.setattr(pz, "_fetch_parcel_entity", lambda urn, tid: {"id": urn})
    holder["install"] = install
    yield TestClient(app), holder
    app.dependency_overrides.pop(require_auth, None)


def _get(c, qs="start=2026-10-01&end=2026-10-03", pid=PID):
    return c.get(f"/api/weather/parcel/{pid}/zones/daily?{qs}")


def test_two_zones_three_days_ascending(client):
    c, h = client
    rows = [_row(z, d) for z in ("z2", "z1") for d in ("2026-10-03", "2026-10-01", "2026-10-02")]
    conn = h["install"](rows)
    r = _get(c)
    assert r.status_code == 200
    body = r.json()
    assert body["parcel_id"] == "urn:ngsi-ld:AgriParcel:p1"
    assert body["days"] == 3
    assert set(body["zones"]) == {"z1", "z2"}
    for z in ("z1", "z2"):
        assert [d["date"] for d in body["zones"][z]] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert body["zones"]["z1"][0] == {"date": "2026-10-01", "tMin": 1.0, "tMax": 9.0, "eto": 2.0}
    assert "truncated" not in body
    assert conn.closed


def test_same_day_latest_observation_wins(client):
    c, h = client
    h["install"]([_row("z1", "2026-10-01", 6, tMin=1.0), _row("z1", "2026-10-01", 18, tMin=2.5)])
    z = _get(c, "start=2026-10-01&end=2026-10-01").json()["zones"]["z1"]
    assert len(z) == 1 and z[0]["tMin"] == 2.5


def test_row_without_tmin_is_absent(client):
    c, h = client
    twi = _row("z9", "2026-10-01")
    twi["measurements"] = {"twiClass": 3}
    h["install"]([twi, _row("z1", "2026-10-01")])
    body = _get(c, "start=2026-10-01&end=2026-10-01").json()
    assert set(body["zones"]) == {"z1"}


def test_non_numeric_value_is_null(client):
    c, h = client
    h["install"]([_row("z1", "2026-10-01", tMax="n/a", eto=True)])
    d = _get(c, "start=2026-10-01&end=2026-10-01").json()["zones"]["z1"][0]
    assert d["tMax"] is None and d["eto"] is None and d["tMin"] == 1.0


def test_range_over_400_days_is_clipped_to_last_400(client):
    c, h = client
    conn = h["install"]([])
    r = _get(c, "start=2025-01-01&end=2026-02-05")  # 401 days inclusive
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True and body["zones"] == {} and body["days"] == 0
    lo, hi = conn.executed[0][1][-2:]
    assert lo == datetime(2025, 1, 2, tzinfo=timezone.utc)
    assert hi == datetime(2026, 2, 6, tzinfo=timezone.utc)


def test_400_days_not_truncated(client):
    c, h = client
    h["install"]([])
    assert "truncated" not in _get(c, "start=2025-01-01&end=2026-02-04").json()


@pytest.mark.parametrize("qs", [
    "start=2026-10-05&end=2026-10-01",
    "start=2026-13-01&end=2026-13-02",
    "start=abc&end=2026-10-01",
])
def test_invalid_dates_400(client, qs):
    c, h = client
    h["install"]([])
    assert _get(c, qs).status_code == 400


def test_unknown_parcel_404(client, monkeypatch):
    c, h = client
    h["install"]([])
    monkeypatch.setattr(pz, "_fetch_parcel_entity", lambda urn, tid: None)
    assert _get(c).status_code == 404


def test_requires_auth():
    app.dependency_overrides.pop(require_auth, None)
    assert TestClient(app).get(f"/api/weather/parcel/{PID}/zones/daily?start=2026-10-01&end=2026-10-02").status_code == 401


def test_query_is_scoped_bound_and_escaped(client):
    c, h = client
    conn = h["install"]([])
    _get(c, pid="urn:ngsi-ld:AgriParcel:a%25b_c")
    q, params = conn.executed[0]
    assert "tenant_id = %s" in q and "DISTINCT ON" in q and "AgriParcelZone" in q
    assert params[0] == "acme"
    assert "urn:ngsi-ld:AgriParcelZone:acme:a\\%b\\_c:%" in params


def test_bare_and_urn_ids_same_query(client):
    c, h = client
    c1 = h["install"]([]); _get(c)
    p1 = c1.executed[0][1]
    c2 = h["install"]([]); _get(c, pid="urn:ngsi-ld:AgriParcel:p1")
    assert p1 == c2.executed[0][1]
