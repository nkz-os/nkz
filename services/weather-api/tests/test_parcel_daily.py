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


from datetime import date as _date, timedelta as _td
from unittest.mock import patch as _patch


def test_fill_none_keeps_contract_and_adds_source(client):
    tc, h = client
    h["install"]([_row("2026-06-01", tempMin=10.0, tempMax=20.0, precipitation=1.0, et0=2.0)])
    j = tc.get(f"/api/weather/parcel/{PID}/daily?start=2026-06-01&end=2026-06-02").json()
    assert j["missing_days"] == ["2026-06-02"]
    assert [d["source"] for d in j["days"]] == ["parcel_weather", None]
    assert j["missing_reasons"] == {"2026-06-02": "no_data"}
    assert j["totals"]["precip_mm"] == 1.0 and "gdd" not in j["totals"]


def _fill_env(loc=(-2.1, 42.6), elevation=580.0, today=_date(2026, 10, 9)):
    entity = {"id": f"urn:ngsi-ld:AgriParcel:{PID}"}
    if elevation is not None:
        entity["elevation"] = {"type": "Property", "value": elevation}
    return [
        _patch.object(pd, "_fetch_parcel_entity", return_value=entity),
        _patch.object(pd, "_resolve_parcel_location", return_value=loc),
        _patch.object(pd, "_today", return_value=today),
    ]


def test_fill_sources_by_window_and_today_not_closed(client):
    tc, h = client
    h["install"]([_row("2026-10-07", tempMin=9.0, tempMax=19.0, precipitation=0.0, et0=1.0)])
    calls = []

    def fake_fetch(kind, lat, lon, elev, d0, d1, http_get=None, timeout=None):
        calls.append((kind, d0, d1, elev))
        return {d.isoformat(): {"tmin_c": 5.0, "tmax_c": 15.0, "precip_mm": 0.5, "et0_mm": 1.0,
                                "radiation_mj_m2": 10.0, "vapour_pressure_kpa": None, "wind2m_ms": 1.0}
                for d in [d0 + _td(days=i) for i in range((d1 - d0).days + 1)]}

    ps = _fill_env()
    with ps[0], ps[1], ps[2], _patch.object(pd, "fetch_open_meteo_daily", side_effect=fake_fetch):
        j = tc.get(f"/api/weather/parcel/{PID}/daily?start=2026-07-07&end=2026-10-09"
                   f"&fill=open_meteo&base_temp=10").json()
    src = {d["date"]: d["source"] for d in j["days"]}
    assert src["2026-07-07"] == "reanalysis" and src["2026-07-09"] == "model_analysis"
    assert src["2026-10-07"] == "parcel_weather" and src["2026-10-09"] is None
    assert j["missing_reasons"] == {"2026-10-09": "not_closed"}
    assert j["missing_days"] == ["2026-10-09"]
    assert {c[0] for c in calls} == {"reanalysis", "model_analysis"} and all(c[3] == 580.0 for c in calls)
    assert j["totals"]["gdd"] is not None


def test_fill_never_fills_today(client):
    tc, h = client
    h["install"]([])
    ps = _fill_env()
    with ps[0], ps[1], ps[2], _patch.object(pd, "fetch_open_meteo_daily", return_value={}) as f:
        j = tc.get(f"/api/weather/parcel/{PID}/daily?start=2026-10-09&end=2026-10-10&fill=open_meteo").json()
    f.assert_not_called()
    assert j["missing_reasons"] == {"2026-10-09": "not_closed", "2026-10-10": "not_closed"}


def test_fill_archive_failure_is_200(client):
    tc, h = client
    h["install"]([])
    ps = _fill_env()
    with ps[0], ps[1], ps[2], _patch.object(pd, "fetch_open_meteo_daily", return_value={}):
        r = tc.get(f"/api/weather/parcel/{PID}/daily?start=2026-01-01&end=2026-01-02&fill=open_meteo")
    assert r.status_code == 200
    assert r.json()["missing_reasons"] == {"2026-01-01": "fill_unavailable", "2026-01-02": "fill_unavailable"}
    assert r.json()["totals"]["precip_mm"] is None


def test_fill_without_location(client):
    tc, h = client
    h["install"]([])
    ps = _fill_env(loc=None)
    with ps[0], ps[1], ps[2], _patch.object(pd, "fetch_open_meteo_daily") as f:
        j = tc.get(f"/api/weather/parcel/{PID}/daily?start=2026-10-01&end=2026-10-01&fill=open_meteo").json()
    f.assert_not_called()
    assert j["missing_reasons"] == {"2026-10-01": "fill_unavailable"}


def test_invalid_fill_value_is_400(client):
    tc, h = client
    h["install"]([])
    r = tc.get(f"/api/weather/parcel/{PID}/daily?start=2026-10-01&end=2026-10-01&fill=yes")
    assert r.status_code == 400


def test_scattered_gaps_make_one_request_per_kind(client):
    tc, h = client
    # Series on every other day inside the model window -> many gaps.
    rows = [_row(f"2026-09-{d:02d}", tempMin=9.0, tempMax=19.0) for d in range(1, 30, 2)]
    h["install"](rows)
    calls = []

    def fake_fetch(kind, lat, lon, elev, d0, d1, http_get=None, timeout=None):
        calls.append((kind, d0, d1))
        return {(d0 + _td(days=i)).isoformat(): {"tmin_c": 1.0, "tmax_c": 2.0, "precip_mm": None, "et0_mm": None,
                                                  "radiation_mj_m2": None, "vapour_pressure_kpa": None, "wind2m_ms": None}
                for i in range((d1 - d0).days + 1)}

    ps = _fill_env()
    with ps[0], ps[1], ps[2], _patch.object(pd, "fetch_open_meteo_daily", side_effect=fake_fetch):
        j = tc.get(f"/api/weather/parcel/{PID}/daily?start=2026-09-01&end=2026-09-30&fill=open_meteo").json()
    assert len(calls) == 1
    by = {d["date"]: d for d in j["days"]}
    assert by["2026-09-01"]["source"] == "parcel_weather" and by["2026-09-01"]["tmin_c"] == 9.0
    assert by["2026-09-02"]["source"] == "model_analysis"


def test_fill_stops_at_deadline(client):
    tc, h = client
    h["install"]([])
    clock = {"t": 0.0}

    def slow_fetch(kind, lat, lon, elev, d0, d1, http_get=None, timeout=None):
        clock["t"] += 100.0  # the first request exhausts the budget
        return {}

    ps = _fill_env()
    with ps[0], ps[1], ps[2], _patch.object(pd, "_monotonic", side_effect=lambda: clock["t"]), \
         _patch.object(pd, "fetch_open_meteo_daily", side_effect=slow_fetch) as f:
        r = tc.get(f"/api/weather/parcel/{PID}/daily?start=2026-01-01&end=2026-10-01&fill=open_meteo")
    assert r.status_code == 200 and f.call_count == 1
    assert set(r.json()["missing_reasons"].values()) == {"fill_unavailable"}


def test_bad_parcel_geometry_means_no_fill(client):
    tc, h = client
    h["install"]([])
    ps = _fill_env()
    with ps[0], _patch.object(pd, "_resolve_parcel_location", side_effect=ZeroDivisionError), ps[2], \
         _patch.object(pd, "fetch_open_meteo_daily") as f:
        r = tc.get(f"/api/weather/parcel/{PID}/daily?start=2026-10-01&end=2026-10-01&fill=open_meteo")
    assert r.status_code == 200 and not f.called
    assert r.json()["missing_reasons"] == {"2026-10-01": "fill_unavailable"}
