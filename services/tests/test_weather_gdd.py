"""Tests for GET /api/weather/gdd endpoint."""

import sys
import os
import importlib.util
from datetime import date, timedelta
from unittest.mock import patch
import pytest

# Required by timeseries-reader/app.py at module level (CI may have empty POSTGRES_URL)
os.environ["POSTGRES_URL"] = os.environ.get("POSTGRES_URL") or "postgresql://test:test@localhost:5432/test"

# Step 1: Patch auth_middleware BEFORE timeseries-reader app.py is imported
_common_dir = os.path.join(os.path.dirname(__file__), "..", "common")
if _common_dir not in sys.path:
    sys.path.insert(0, _common_dir)

try:
    import auth_middleware
except ImportError:
    auth_middleware = None  # type: ignore

if auth_middleware is None:
    pytest.skip("auth_middleware requires flask", allow_module_level=True)

auth_middleware.require_auth = lambda f: f

# Step 2: Add timeseries-reader to path so app.py can import gdd_response module
_ts_dir = os.path.join(os.path.dirname(__file__), "..", "timeseries-reader")
if _ts_dir not in sys.path:
    sys.path.insert(0, _ts_dir)

# Load timeseries-reader app.py directly by file path
# Using importlib avoids namespace collisions with weather-api/app/__init__.py
_ts_app_path = os.path.join(_ts_dir, "app.py")
_spec = importlib.util.spec_from_file_location("ts_reader_app", _ts_app_path)
ts_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts_app)
flask_app = ts_app.app  # module-level functions like get_db_connection


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


class FakeCursor:
    """Cursor mock that handles multi-query flows (KNN: station query + daily query).

    - KNN path (lat/lon provided): execute_count tracks:
        1 = set_config, 2 = station query, 3 = daily query
      fetchone called after #2 -> returns station_row
      fetchall called after #3 -> returns daily_rows
    - Non-KNN path (no lat/lon): execute_count tracks:
        1 = set_config, 2 = daily query
      fetchall called after #2 -> returns daily_rows (no fetchone calls)
    """

    def __init__(self, rows, station_row=None):
        self._rows = rows
        self._station_row = station_row or {"station_id": "test_kn_station"}
        self._execute_count = 0

    def execute(self, query, params=None):
        self._execute_count += 1

    def fetchone(self):
        # KNN path: 2nd execute is station query -> return station_id
        if self._execute_count == 2 and self._station_row:
            return self._station_row
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows, station_row=None):
        self._rows = rows
        self._station_row = station_row

    def cursor(self, cursor_factory=None):
        return FakeCursor(self._rows, self._station_row)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ── Validation tests (no DB mocking needed) ──────────────────────────────


def test_gdd_requires_season_start(client):
    resp = client.get("/api/weather/gdd", headers={"X-Tenant-ID": "test"})
    assert resp.status_code == 400


def test_gdd_requires_base_temp(client):
    resp = client.get(
        "/api/weather/gdd?season_start=2026-03-01",
        headers={"X-Tenant-ID": "test"},
    )
    assert resp.status_code == 400


def test_gdd_invalid_date(client):
    resp = client.get(
        "/api/weather/gdd?season_start=not-a-date&base_temp=10",
        headers={"X-Tenant-ID": "test"},
    )
    assert resp.status_code == 400


def test_gdd_future_date(client):
    resp = client.get(
        "/api/weather/gdd?season_start=2099-01-01&base_temp=10",
        headers={"X-Tenant-ID": "test"},
    )
    assert resp.status_code == 400


def test_gdd_invalid_base_temp(client):
    resp = client.get(
        "/api/weather/gdd?season_start=2026-03-01&base_temp=abc",
        headers={"X-Tenant-ID": "test"},
    )
    assert resp.status_code == 400


def test_gdd_invalid_upper_cutoff(client):
    resp = client.get(
        "/api/weather/gdd?season_start=2026-03-01&base_temp=10&upper_cutoff=abc",
        headers={"X-Tenant-ID": "test"},
    )
    assert resp.status_code == 400


# ── Computation tests (DB mocked) ────────────────────────────────────────


def test_gdd_happy_path(monkeypatch, client):
    """90 days of Tmin=10, Tmax=20 -> GDD=5/day -> 450 total (base 10)."""
    rows = []
    for d in range(90):
        day = date(2026, 3, 1) + timedelta(days=d)
        rows.append({
            "obs_date": day,
            "tmin": 10.0,
            "tmax": 20.0,
        })

    monkeypatch.setattr(
        ts_app, "get_db_connection", lambda: FakeConn(rows)
    )
    resp = client.get(
        "/api/weather/gdd?season_start=2026-03-01&base_temp=10&lat=42.0&lon=-1.0",
        headers={"X-Tenant-ID": "test"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["gdd_total"] == pytest.approx(450.0, rel=0.01)
    assert data["days_count"] == 90
    assert data["mean_daily_gdd"] == pytest.approx(5.0, rel=0.01)


def test_gdd_with_upper_cutoff(monkeypatch, client):
    """10 days Tmax=40 (heat wave), shows capping effect."""
    rows = []
    for d in range(10):
        day = date(2026, 3, 1) + timedelta(days=d)
        rows.append({
            "obs_date": day,
            "tmin": 15.0,
            "tmax": 40.0,
        })

    monkeypatch.setattr(
        ts_app, "get_db_connection", lambda: FakeConn(rows)
    )

    # upper_cutoff=50 (effectively no cap since Tmax=40):
    # avg=(15+40)/2=27.5, GDD=17.5/day -> 175
    resp_high = client.get(
        "/api/weather/gdd?season_start=2026-03-01&base_temp=10&upper_cutoff=50",
        headers={"X-Tenant-ID": "test"},
    )
    assert resp_high.status_code == 200
    assert resp_high.get_json()["gdd_total"] == pytest.approx(175.0, rel=0.01)

    # upper_cutoff=30 (capped): capped Tmax=30, avg=22.5, GDD=12.5/day -> 125
    resp_cut = client.get(
        "/api/weather/gdd?season_start=2026-03-01&base_temp=10&upper_cutoff=30",
        headers={"X-Tenant-ID": "test"},
    )
    assert resp_cut.status_code == 200
    assert resp_cut.get_json()["gdd_total"] == pytest.approx(125.0, rel=0.01)


def test_gdd_no_rows(monkeypatch, client):
    """No weather data -> zero GDD, zero days."""
    monkeypatch.setattr(
        ts_app, "get_db_connection", lambda: FakeConn([])
    )
    resp = client.get(
        "/api/weather/gdd?season_start=2026-03-01&base_temp=10",
        headers={"X-Tenant-ID": "test"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["gdd_total"] == 0.0
    assert data["days_count"] == 0
    assert data["mean_daily_gdd"] == 0.0


def test_gdd_negative_base_temp(monkeypatch, client):
    """Base temp below zero: GDD calculation still works."""
    rows = []
    for d in range(5):
        day = date(2026, 3, 1) + timedelta(days=d)
        rows.append({
            "obs_date": day,
            "tmin": -2.0,
            "tmax": 5.0,
        })

    monkeypatch.setattr(
        ts_app, "get_db_connection", lambda: FakeConn(rows)
    )
    resp = client.get(
        "/api/weather/gdd?season_start=2026-03-01&base_temp=-5&lat=42.0&lon=-1.0",
        headers={"X-Tenant-ID": "test"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    # avg=1.5, GDD=1.5-(-5)=6.5/day * 5 => 32.5
    assert data["gdd_total"] == pytest.approx(32.5, rel=0.01)
    assert data["days_count"] == 5
