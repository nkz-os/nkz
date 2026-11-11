"""GET /api/weather/parcel/{id}/history (fake archive docs, no network)."""
import os
import sys
from datetime import date, datetime, timedelta, timezone

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
import app.routers.parcel_history as ph
from app.services.history import archive
from app.services.history.aggregate import latest_complete_day

UTC = timezone.utc
PID = "da36ccd2-85d2-4c76-b552-c5c835a987c1"
TODAY = date(2026, 11, 11)
ATTRIBUTION = "Contains modified Copernicus Climate Change Service information 2026; Open-Meteo (CC BY 4.0)"


def _doc(year, coastal=False, last_hour=None):
    """Synthetic blended hourly year; the current year stops at `last_hour` (UTC datetime, exclusive)."""
    t0 = datetime(year, 1, 1, tzinfo=UTC)
    end = datetime(year + 1, 1, 1, tzinfo=UTC)
    if last_hour is not None:
        end = last_hour
    n = int((end - t0).total_seconds() // 3600)
    times = [int((t0 + timedelta(hours=i)).timestamp()) for i in range(n)]
    doc = {
        "schema": 1, "year": year, "cell_lat": 42.25, "cell_lon": -1.5, "elev": 400.0,
        "grid_elevation": 0.0 if coastal else 350.0, "coastal": coastal, "tz": "UTC",
        "closed": last_hour is None, "fetched_on": TODAY.isoformat(), "latest_available": None,
        "provenance": {
            "t_c": {"source": "era5", "altitude_method": "elevation_corrected_coastal" if coastal else "blend"},
            "td_c": {"source": "era5", "altitude_method": "elevation_corrected"},
            "rh_pct": {"source": "era5", "altitude_method": "derived_from_t_and_td"},
            "precip_mm": {"source": "era5", "altitude_method": "none"},
            "radiation_w_m2": {"source": "era5", "altitude_method": "none"},
            "wind2m_ms": {"source": "era5", "altitude_method": "none"},
        },
        "times": times,
        "t": [10.0 + (i % 24) * 0.5 for i in range(n)],
        "td": [4.0] * n,
        "rh": [60.0] * n,
        "precip": [0.1] * n,
        "rad": [200.0] * n,
        "wind10": [3.0] * n,
    }
    latest = latest_complete_day(doc)
    doc["latest_available"] = latest.isoformat() if latest else None
    return doc


class Archive:
    """Stands in for history_year; the current year ends 2026-11-08 23:00 UTC (latest complete day: Nov 7)."""

    def __init__(self, coastal=False, advance=0.0, clock=None, fail=None):
        self.calls = []
        self.coastal = coastal
        self.advance = advance
        self.clock = clock
        self.fail = fail

    def __call__(self, lat, lon, elev, year, cache=None, http_get=None, timeout=60.0, today=None):
        self.calls.append(year)
        if self.clock is not None:
            self.clock[0] += self.advance
        if self.fail:
            raise archive.ArchiveError(self.fail)
        if year == TODAY.year:
            return _doc(year, self.coastal, last_hour=datetime(2026, 11, 9, 0, tzinfo=UTC))
        return _doc(year, self.coastal)


def _entity(lon=-1.5, lat=42.25, elevation=400.0):
    e = {"id": f"urn:ngsi-ld:AgriParcel:{PID}",
         "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [lon, lat]}}}
    if elevation is not None:
        e["elevation"] = {"type": "Property", "value": elevation}
    return e


@pytest.fixture
def env(monkeypatch):
    app.dependency_overrides[require_auth] = lambda: "tenant-a"
    ph._LATEST_CACHE.clear()
    clock = [0.0]
    arch = Archive(clock=clock)
    holder = {"arch": arch, "clock": clock, "entity": _entity()}
    monkeypatch.setattr(ph, "history_year", lambda *a, **k: holder["arch"](*a, **k))
    monkeypatch.setattr(ph, "_fetch_parcel_entity", lambda urn, tid: holder["entity"])
    monkeypatch.setattr(ph, "_today", lambda: TODAY)
    monkeypatch.setattr(ph, "_monotonic", lambda: clock[0])
    yield TestClient(app), holder
    app.dependency_overrides.pop(require_auth, None)


def _get(c, **q):
    q.setdefault("start", "2020-03-01")
    q.setdefault("end", "2020-03-05")
    return c.get(f"/api/weather/parcel/{PID}/history", params=q)


def test_unsigned_request_is_401():
    app.dependency_overrides.pop(require_auth, None)
    r = TestClient(app).get(f"/api/weather/parcel/{PID}/history?start=2020-01-01&end=2020-01-02")
    assert r.status_code == 401


@pytest.mark.parametrize("q", [
    {"start": "1990-12-31", "end": "1991-01-05"},
    {"start": "2020-03-05", "end": "2020-03-01"},
    {"start": "nope"},
    {"end": "2020-13-40"},
    {"vars": "tmax_c,bogus"},
    {"vars": "t_c"},                       # hourly var on a daily request
    {"resolution": "weekly"},
    {"resolution": "hourly", "start": "2020-01-01", "end": "2021-01-01"},   # 367 days
    {"resolution": "hourly", "vars": "tmax_c"},
    {"end": "2026-11-12"},                 # after today
    {"end": "2026-11-08"},                 # after latest_available (Nov 7)
])
def test_validation_errors_are_400(env, q):
    c, _ = env
    r = _get(c, **q)
    assert r.status_code == 400, r.text
    assert "error" in r.json()


def test_hourly_span_of_366_days_is_allowed(env):
    c, _ = env
    r = _get(c, resolution="hourly", start="2020-01-01", end="2020-12-31")
    assert r.status_code == 200


@pytest.mark.parametrize("elevation", [0, -3, None, float("nan"), float("inf")])
def test_bad_elevation_is_422(env, elevation):
    c, h = env
    h["entity"] = _entity(elevation=elevation)
    r = _get(c)
    assert r.status_code == 422
    assert "elevation" in r.json()["error"].lower()


def test_missing_parcel_is_404_and_no_location_is_422(env):
    c, h = env
    h["entity"] = None
    assert _get(c).status_code == 404
    h["entity"] = {"id": "x", "elevation": {"value": 300}}
    assert _get(c).status_code == 422


def test_daily_response_schema(env):
    c, h = env
    r = _get(c, vars="tmax_c,precip_mm,et0_mm,rh_mean_pct")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["parcel_id"] == f"urn:ngsi-ld:AgriParcel:{PID}"
    assert (b["start"], b["end"], b["resolution"]) == ("2020-03-01", "2020-03-05", "daily")
    assert b["latest_available"] == "2026-11-07"
    assert [d["date"] for d in b["days"]] == [f"2020-03-0{i}" for i in range(1, 6)]
    assert set(b["days"][0]) == {"date", "tmax_c", "precip_mm", "et0_mm", "rh_mean_pct"}
    assert b["days"][0]["precip_mm"] == pytest.approx(2.4)
    assert b["days"][0]["et0_mm"] is not None            # parcel lat/elev reach the ET0
    assert set(b["units"]) == {"tmax_c", "precip_mm", "et0_mm", "rh_mean_pct"}
    assert b["units"]["precip_mm"] == "mm"
    assert set(b["provenance"]) == set(b["units"])
    assert b["provenance"]["tmax_c"] == {"source": "era5", "altitude_method": "blend"}
    assert set(b["validation"]) == set(b["units"])
    assert b["attribution"] == ATTRIBUTION
    assert "incomplete_years" not in b
    assert "Retry-After" not in r.headers
    assert h["arch"].calls.count(2020) == 1


def test_default_vars_are_all_daily_vars(env):
    c, _ = env
    b = _get(c).json()
    assert set(b["days"][0]) == {"date", "tmin_c", "tmax_c", "tmean_c", "precip_mm", "et0_mm",
                                 "radiation_mj_m2", "vapour_pressure_kpa", "rh_mean_pct", "wind2m_ms"}


def test_hourly_response_schema(env):
    c, _ = env
    r = _get(c, resolution="hourly", start="2020-03-01", end="2020-03-01", vars="t_c,precip_mm")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["resolution"] == "hourly" and "days" not in b
    assert len(b["hours"]) == 24
    assert set(b["hours"][0]) == {"time", "t_c", "precip_mm"}
    assert b["hours"][0]["time"] == "2020-03-01T00:00:00Z"
    assert set(b["validation"]) == {"t_c", "precip_mm"}


def test_validation_inside_and_outside_a_validated_zone(env):
    c, h = env
    b = _get(c, vars="tmax_c").json()
    assert isinstance(b["validation"]["tmax_c"], list) and b["validation"]["tmax_c"]
    h["entity"] = _entity(lon=100.0, lat=10.0)
    b = _get(c, vars="tmax_c,precip_mm").json()
    assert b["validation"] == {"tmax_c": "unvalidated_region", "precip_mm": "unvalidated_region"}


def test_current_year_is_cut_at_latest_available(env):
    c, _ = env
    b = _get(c, vars="tmax_c", start="2026-11-05", end="2026-11-07").json()
    assert [d["date"] for d in b["days"]] == ["2026-11-05", "2026-11-06", "2026-11-07"]
    assert b["latest_available"] == "2026-11-07"
    assert _get(c, start="2026-11-05", end="2026-11-08").status_code == 400


def test_range_over_two_years_uses_adjacent_years_for_dec31(env):
    c, _ = env
    b = _get(c, vars="precip_mm", start="2020-12-31", end="2021-01-01").json()
    assert [d["precip_mm"] for d in b["days"]] == [pytest.approx(2.4)] * 2


def test_dec31_end_fetches_the_next_year_for_its_closing_stamp(env):
    c, h = env
    b = _get(c, vars="precip_mm", start="2020-12-30", end="2020-12-31").json()
    assert 2021 in h["arch"].calls
    assert b["days"][-1]["precip_mm"] == pytest.approx(2.4)


def test_coastal_parcel_reports_corrected_method(env):
    c, h = env
    h["arch"] = Archive(coastal=True, clock=h["clock"])
    b = _get(c, vars="tmax_c,tmean_c").json()
    assert "corrected" in b["provenance"]["tmax_c"]["altitude_method"]
    assert b["provenance"]["tmean_c"] == b["provenance"]["tmax_c"]
    hb = _get(c, resolution="hourly", vars="t_c", start="2020-03-01", end="2020-03-01").json()
    assert "corrected" in hb["provenance"]["t_c"]["altitude_method"]


def test_deadline_returns_complete_years_and_incomplete_list(env):
    c, h = env
    h["arch"] = Archive(advance=10.0, clock=h["clock"])
    r = _get(c, vars="tmax_c", start="2016-06-01", end="2020-06-30")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["incomplete_years"] == [2016, 2017, 2018]
    assert r.headers["Retry-After"] == "30"
    years = {d["date"][:4] for d in b["days"]}
    assert years == {"2019", "2020"}
    # 2019 is complete: every day from Jan 1 to Jun 30 would not be asked, but the days in range are all there
    assert b["days"][0]["date"] == "2019-01-01" or b["days"][0]["date"] >= "2016-06-01"
    assert all(d["tmax_c"] is not None for d in b["days"])


def test_deadline_hit_before_any_year_gives_all_incomplete(env):
    c, h = env
    h["arch"] = Archive(advance=30.0, clock=h["clock"])
    b = _get(c, vars="tmax_c", start="2019-01-01", end="2020-12-30")
    assert b.status_code in (200, 503)
    if b.status_code == 200:
        assert b.json()["days"] == [] and b.json()["incomplete_years"] == [2019, 2020]
        assert b.headers["Retry-After"] == "30"


def test_archive_error_is_generic_and_never_echoed(env, caplog):
    c, h = env
    h["arch"] = Archive(fail="boom lat=42.25 lon=-1.5", clock=h["clock"])
    with caplog.at_level("ERROR"):
        r = _get(c)
    assert r.status_code in (502, 503)
    assert "42.25" not in r.text and "boom" not in r.text and "-1.5" not in r.text
    logged = " ".join(rec.getMessage() for rec in caplog.records)
    assert PID in logged and "tenant-a" in logged


def test_latest_available_is_cached_between_requests(env):
    c, h = env
    _get(c)
    _get(c, start="2020-04-01", end="2020-04-02")
    assert h["arch"].calls.count(2026) == 1
    h["clock"][0] += 6 * 3600 + 1
    _get(c)
    assert h["arch"].calls.count(2026) == 2


def test_latest_falls_back_to_previous_year_early_in_january(env, monkeypatch):
    c, h = env
    monkeypatch.setattr(ph, "_today", lambda: date(2027, 1, 1))

    def arch(lat, lon, elev, year, **k):
        h["arch"].calls.append(year)
        if year == 2027:
            return _doc(2027, last_hour=datetime(2027, 1, 1, 6, tzinfo=UTC))
        return _doc(year)

    h["arch"] = arch
    h["arch"].calls = []
    b = _get(c, start="2026-12-30", end="2026-12-30", vars="tmax_c").json()
    assert b["latest_available"] == "2026-12-31"
