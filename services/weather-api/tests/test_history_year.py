"""fetch_year parameters and history_year orchestration (fake HTTP, fake cache)."""
import os, sys
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest

from app.services.history import archive, year as hy
from app.services.history.deadline import Deadline, DeadlineExceeded
from app.services.history import cache as hc

UTC = timezone.utc


def _epoch(y, m, d, h=0):
    return int(datetime(y, m, d, h, tzinfo=UTC).timestamp())


def _body(year, elevation, offset=0.0, hours=None, lat=42.25, lon=-1.5, tz="UTC"):
    t0 = datetime(year, 1, 1, tzinfo=UTC)
    n = hours if hours is not None else (366 if year % 4 == 0 else 365) * 24
    times = [int((t0 + timedelta(hours=i)).timestamp()) for i in range(n)]
    return {
        "latitude": lat, "longitude": lon, "elevation": elevation, "timezone": tz,
        "hourly": {
            "time": times,
            "temperature_2m": [10.0 + (i % 24) * 0.5 + offset for i in range(n)],
            "dew_point_2m": [4.0 + offset for _ in range(n)],
            "precipitation": [0.1] * n,
            "shortwave_radiation": [200.0] * n,
            "wind_speed_10m": [3.0] * n,
        },
    }


def _resp(body, status=200):
    r = MagicMock(); r.status_code = status; r.json.return_value = body
    return r


class FakeHttp:
    """Grid cell (42.25,-1.5) unless `corr_cell` says the corrected request lands elsewhere."""

    def __init__(self, year, grid_elev=450.0, hours=None, corr_cell=None, grid_cell=(42.25, -1.5)):
        self.calls = []
        self.year, self.grid_elev, self.hours = year, grid_elev, hours
        self.grid_cell, self.corr_cell = grid_cell, corr_cell or grid_cell

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params), timeout))
        probe = params["start_date"] == params["end_date"]
        hours = 24 if probe else self.hours
        year = int(params["start_date"][:4]) if probe else self.year
        if str(params["elevation"]) == "nan":
            return _resp(_body(year, self.grid_elev, 0.0, hours, *self.grid_cell))
        return _resp(_body(year, float(params["elevation"]), 1.0, hours, *self.corr_cell))

    @property
    def full(self):
        return [c for c in self.calls if c[1]["start_date"] != c[1]["end_date"]]

    @property
    def probes(self):
        return [c for c in self.calls if c[1]["start_date"] == c[1]["end_date"]]


class MemCache:
    enabled = True

    def __init__(self):
        self.store, self.puts = {}, []

    def get(self, key):
        return self.store.get(key)

    def put(self, key, doc, closed):
        if key in self.store and self.store[key].get("closed"):      # the stored flag decides
            return False
        self.puts.append(key)
        self.store[key] = {**doc, "closed": closed}
        return True


@pytest.fixture(autouse=True)
def _archive_url(monkeypatch):
    monkeypatch.setattr(archive.settings, "openmeteo_archive_url", "http://om/v1")
    hy._CELL_PROBES.clear()


def test_fetch_params_grid_and_corrected():
    http = FakeHttp(2020)
    g = archive.fetch_year(42.3, -1.4, 580.0, 2020, "grid", http_get=http, today=date(2026, 11, 11))
    c = archive.fetch_year(42.3, -1.4, 580.0, 2020, "corrected", http_get=http, today=date(2026, 11, 11))
    (gu, gp, gt), (cu, cp, _) = http.calls
    assert gu == "http://om/v1/archive" == cu
    for p in (gp, cp):
        assert p["models"] == "era5" and p["wind_speed_unit"] == "ms"
        assert p["timeformat"] == "unixtime" and p["timezone"] == "auto"
        assert p["start_date"] == "2020-01-01" and p["end_date"] == "2020-12-31"
        assert p["hourly"] == "temperature_2m,dew_point_2m,precipitation,shortwave_radiation,wind_speed_10m"
    assert gp["elevation"] == "nan" and cp["elevation"] == 580.0
    assert gt and gt > 0
    assert g["grid_elevation"] == 450.0 and g["lat"] == 42.25 and g["tz"] == "UTC"
    assert len(g["times"]) == len(g["t"]) == 366 * 24


def test_fetch_current_year_ends_today():
    http = FakeHttp(2026, hours=24)
    archive.fetch_year(42.3, -1.4, 580.0, 2026, "grid", http_get=http, today=date(2026, 11, 11))
    assert http.full[0][1]["end_date"] == "2026-11-11"


def test_fetch_errors_raise():
    bad = lambda *a, **k: _resp({}, 500)
    with pytest.raises(archive.ArchiveError):
        archive.fetch_year(42.3, -1.4, 580.0, 2020, "grid", http_get=bad, today=date(2026, 11, 11))
    with pytest.raises(ValueError):
        archive.fetch_year(42.3, -1.4, 580.0, 2020, "bogus", http_get=bad, today=date(2026, 11, 11))


def test_fetch_without_archive_url_raises(monkeypatch):
    monkeypatch.setattr(archive.settings, "openmeteo_archive_url", "")
    with pytest.raises(archive.ArchiveError):
        archive.fetch_year(42.3, -1.4, 580.0, 2020, "grid", http_get=MagicMock(), today=date(2026, 11, 11))


def test_history_year_blends_caches_closed_year_once():
    http, cache = FakeHttp(2020), MemCache()
    today = date(2026, 11, 11)
    doc = hy.history_year(42.3, -1.4, 580.0, 2020, cache=cache, http_get=http, today=today)
    assert doc["closed"] is True and doc["year"] == 2020
    assert len(http.full) == 2 and len(http.probes) == 2 and len(cache.puts) == 1
    assert cache.puts[0] == "weather-history/era5/v1/42.25_-1.50_580/2020.json.gz"
    # blend: coldest grid hour keeps grid T, hottest takes corrected (+1)
    assert doc["t"][0] == pytest.approx(10.0) and doc["t"][23] == pytest.approx(22.5)
    assert doc["td"][0] == pytest.approx(5.0)                       # corrected response
    assert doc["precip"][0] == pytest.approx(0.1)
    assert doc["rh"][0] is not None and 0 < doc["rh"][0] <= 100
    assert doc["provenance"]["t_c"]["altitude_method"] == "blend"
    again = hy.history_year(42.3, -1.4, 580.0, 2020, cache=cache, http_get=http, today=today)
    assert again == doc and len(http.full) == 2 and len(http.probes) == 2 and len(cache.puts) == 1   # probes memoised


def test_history_year_coastal_uses_corrected_and_says_so():
    http, cache = FakeHttp(2020, grid_elev=-3.0, corr_cell=(42.0, -1.75)), MemCache()
    doc = hy.history_year(42.3, -1.4, 20.0, 2020, cache=cache, http_get=http, today=date(2026, 11, 11))
    assert doc["coastal"] is True
    assert doc["t"][0] == pytest.approx(11.0) and doc["t"][23] == pytest.approx(22.5)
    assert doc["provenance"]["t_c"]["altitude_method"] == "elevation_corrected_coastal"
    assert len(http.full) == 1 and http.full[0][1]["elevation"] == 20.0         # no grid fetch, no blend


def test_current_year_open_refreshed_when_stale_not_when_fresh():
    http, cache = FakeHttp(2026, hours=24 * 100), MemCache()
    d1 = date(2026, 4, 12)
    doc = hy.history_year(42.3, -1.4, 580.0, 2026, cache=cache, http_get=http, today=d1)
    assert doc["closed"] is False and doc["latest_available"] == "2026-04-09"
    assert len(http.full) == 2
    hy.history_year(42.3, -1.4, 580.0, 2026, cache=cache, http_get=http, today=d1)
    assert len(http.full) == 2                                      # same day: served from cache
    http2 = FakeHttp(2026, hours=24 * 101)
    doc2 = hy.history_year(42.3, -1.4, 580.0, 2026, cache=cache, http_get=http2, today=date(2026, 4, 13))
    assert len(http2.full) == 2 and doc2["latest_available"] == "2026-04-10"
    assert len(cache.puts) == 2


def test_year_ending_within_the_lag_is_not_closed():
    # data stops 2025-12-30: Dec 31 is not before latest-1, so not closed
    http, cache = FakeHttp(2025, hours=24 * 364), MemCache()
    doc = hy.history_year(42.3, -1.4, 580.0, 2025, cache=cache, http_get=http, today=date(2026, 1, 4))
    assert doc["latest_available"] == "2025-12-29" and doc["closed"] is False


def test_future_year_rejected():
    with pytest.raises(ValueError):
        hy.history_year(42.3, -1.4, 580.0, 2027, cache=MemCache(), http_get=FakeHttp(2027), today=date(2026, 11, 11))


def test_close_only_after_100_days():
    # ERA5T preliminary data is replaced by final ERA5 after ~2-3 months
    for today, closed in ((date(2026, 1, 20), False), (date(2026, 4, 5), False), (date(2026, 4, 12), True)):
        doc = hy.history_year(42.3, -1.4, 580.0, 2025, cache=MemCache(), http_get=FakeHttp(2025), today=today)
        assert doc["latest_available"] == "2025-12-31"
        assert doc["closed"] is closed, today


def test_full_fetch_in_another_cell_than_the_probe_serves_but_does_not_cache(caplog):
    class Shifted(FakeHttp):
        def __call__(self, url, params=None, timeout=None):
            if params["start_date"] != params["end_date"]:
                self.calls.append((url, dict(params), timeout))
                return _resp(_body(self.year, 450.0, lat=42.0, lon=-1.0))     # not the probed cell
            return super().__call__(url, params, timeout)
    cache = MemCache()
    import logging
    with caplog.at_level(logging.WARNING):
        doc = hy.history_year(42.3, -1.4, 580.0, 2020, cache=cache, http_get=Shifted(2020), today=date(2026, 11, 11))
    assert doc["year"] == 2020 and cache.puts == []
    assert any("cell" in r.message for r in caplog.records)


def test_cache_hit_with_wrong_cell_is_a_miss():
    http, cache = FakeHttp(2020), MemCache()
    key = "weather-history/era5/v1/42.25_-1.50_580/2020.json.gz"
    cache.store[key] = {"closed": True, "year": 2020, "cell_lat": 40.0, "cell_lon": 3.0, "t": ["stale"]}
    doc = hy.history_year(42.3, -1.4, 580.0, 2020, cache=cache, http_get=http, today=date(2026, 11, 11))
    assert len(http.full) == 2 and doc["cell_lat"] == 42.25 and doc["t"][0] != "stale"


def test_parcel_elevation_rounded_before_fetch_and_key():
    http, cache = FakeHttp(2020), MemCache()
    doc = hy.history_year(42.3, -1.4, 579.6, 2020, cache=cache, http_get=http, today=date(2026, 11, 11))
    assert http.full[0][1]["elevation"] == 580.0 and doc["elev"] == 580.0
    assert cache.puts == ["weather-history/era5/v1/42.25_-1.50_580/2020.json.gz"]


# ---- final fix wave ----
TODAY = date(2026, 11, 11)


def test_probe_params_one_day_for_grid_and_corrected():
    http = FakeHttp(2020)
    info = hy.resolve_cell(42.3, -1.4, 580.0, http)
    assert len(http.probes) == 2 and len(http.full) == 0
    (_, gp, _), (_, cp, _) = sorted(http.probes, key=lambda c: str(c[1]["elevation"]) != "nan")
    assert gp["elevation"] == "nan" and cp["elevation"] == 580.0
    assert gp["start_date"] == gp["end_date"] == cp["start_date"] and gp["models"] == "era5"
    assert info.coastal is False and (info.corr_lat, info.corr_lon) == (42.25, -1.5)
    hy.resolve_cell(42.30004, -1.40004, 580.0, http)                      # same 4 decimals: memoised
    assert len(http.probes) == 2
    hy.resolve_cell(42.3, -1.4, 600.0, http)                              # other elevation: new probe
    assert len(http.probes) == 4


def test_coastal_collision_each_parcel_gets_its_own_key_and_corrected_data():
    # Two parcels share the grid cell (42.25,-1.5); their corrected cells differ.
    cache = MemCache()
    a = FakeHttp(2020, grid_elev=-3.0, grid_cell=(42.25, -1.5), corr_cell=(42.0, -1.75))
    b = FakeHttp(2020, grid_elev=-3.0, grid_cell=(42.25, -1.5), corr_cell=(42.25, -1.25))
    da = hy.history_year(42.3, -1.4, 20.0, 2020, cache=cache, http_get=a, today=TODAY)
    hy._CELL_PROBES.clear()
    db = hy.history_year(42.3, -1.3, 20.0, 2020, cache=cache, http_get=b, today=TODAY)
    assert cache.puts == ["weather-history/era5/v1/42.00_-1.75_20_c/2020.json.gz",
                          "weather-history/era5/v1/42.25_-1.25_20_c/2020.json.gz"]
    assert (da["cell_lat"], da["cell_lon"]) == (42.0, -1.75) and (db["cell_lat"], db["cell_lon"]) == (42.25, -1.25)
    for d in (da, db):
        assert d["coastal"] is True
        for var in ("t_c", "td_c", "precip_mm", "radiation_w_m2", "wind2m_ms"):
            assert d["provenance"][var]["altitude_method"] == "elevation_corrected_coastal", var
        assert d["precip"][0] == pytest.approx(0.1)
    # precipitation and wind come from the corrected response, not the grid one
    class Marked(FakeHttp):
        def __call__(self, url, params=None, timeout=None):
            r = super().__call__(url, params, timeout)
            body = r.json.return_value
            if str(params["elevation"]) != "nan":
                body["hourly"]["precipitation"] = [0.7] * len(body["hourly"]["time"])
                body["hourly"]["wind_speed_10m"] = [9.0] * len(body["hourly"]["time"])
            return r
    hy._CELL_PROBES.clear()
    dm = hy.history_year(42.3, -1.4, 20.0, 2020, cache=MemCache(),
                         http_get=Marked(2020, grid_elev=-3.0, corr_cell=(42.0, -1.75)), today=TODAY)
    assert dm["precip"][0] == pytest.approx(0.7) and dm["wind10"][0] == pytest.approx(9.0)


def test_coastal_and_non_coastal_never_share_a_key():
    # grid cell Y differs from corrected cell X; a plain parcel in X must not read that object
    cache = MemCache()
    plain = FakeHttp(2020, grid_cell=(42.25, -1.5))
    hy.history_year(42.3, -1.4, 580.0, 2020, cache=cache, http_get=plain, today=TODAY)
    hy._CELL_PROBES.clear()
    coastal = FakeHttp(2020, grid_cell=(42.5, -1.5), corr_cell=(42.25, -1.5), grid_elev=-2.0)
    hy.history_year(42.3, -1.0, 580.0, 2020, cache=cache, http_get=coastal, today=TODAY)
    assert len(set(cache.puts)) == 2 and len(coastal.full) == 1


def test_cell_mismatch_without_nonpositive_grid_elevation_is_still_coastal():
    http = FakeHttp(2020, grid_elev=300.0, grid_cell=(42.5, -1.5), corr_cell=(42.25, -1.5))
    assert hy.resolve_cell(42.3, -1.4, 580.0, http).coastal is True


def test_stored_values_are_rounded():
    http, cache = FakeHttp(2020), MemCache()
    http_body = http.__call__

    def odd(url, params=None, timeout=None):
        r = http_body(url, params, timeout)
        h = r.json.return_value["hourly"]
        n = len(h["time"])
        h["temperature_2m"] = [10.123456 + i * 1e-4 for i in range(n)]
        h["dew_point_2m"] = [4.987654] * n
        h["precipitation"] = [0.123456] * n
        h["shortwave_radiation"] = [200.456] * n
        h["wind_speed_10m"] = [3.14159] * n
        return r
    doc = hy.history_year(42.3, -1.4, 580.0, 2020, cache=cache, http_get=odd, today=TODAY)
    assert doc["td"][0] == 4.99 and doc["precip"][0] == 0.12 and doc["rad"][0] == 200.5 and doc["wind10"][0] == 3.14
    assert doc["t"][0] == round(doc["t"][0], 2)
    assert doc["rh"][0] == round(doc["rh"][0], 1)


def test_year_object_is_under_250_kb_gzipped():
    import gzip, json, random
    rnd = random.Random(7)
    n = 8760

    def odd(url, params=None, timeout=None):
        t0 = datetime(2021, 1, 1, tzinfo=UTC)
        times = [int((t0 + timedelta(hours=i)).timestamp()) for i in range(n)]
        walk, out = 12.0, []
        for _ in range(n):
            walk += rnd.uniform(-1, 1)
            out.append(walk)
        hourly = {"time": times, "temperature_2m": out,
                  "dew_point_2m": [v - rnd.uniform(0, 9) for v in out],
                  "precipitation": [rnd.choice([0, 0, 0, rnd.uniform(0, 5)]) for _ in range(n)],
                  "shortwave_radiation": [rnd.uniform(0, 900) for _ in range(n)],
                  "wind_speed_10m": [rnd.uniform(0, 12) for _ in range(n)]}
        return _resp({"latitude": 42.25, "longitude": -1.5, "elevation": 450.0 if str(params["elevation"]) == "nan"
                      else float(params["elevation"]), "timezone": "UTC", "hourly": hourly})
    cache = MemCache()
    doc = hy.history_year(42.3, -1.4, 580.0, 2021, cache=cache, http_get=odd, today=TODAY)
    size = len(gzip.compress(json.dumps(doc, separators=(",", ":")).encode()))
    assert size < 250_000, size


# --- deadline (R3) ---
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_deadline_object_rules():
    clk = Clock()
    d = Deadline(20.0, clk)
    assert d.remaining() == 20.0 and d.estimate == 4.0
    assert d.timeout() == (3, 10.0)
    clk.t = 19.5
    assert d.timeout() == (3, 1)                                        # floor of one second
    clk.t = 12.0
    assert d.can_start_cold() is True                                    # 8 >= 2 * 4
    clk.t = 12.5
    assert d.can_start_cold() is False
    d.record(1.5)
    assert d.estimate == 1.5 and d.can_start_cold() is True              # 7.5 >= 3


def test_no_cold_year_starts_when_remaining_is_below_twice_the_estimate():
    clk = Clock()
    d = Deadline(20.0, clk)
    clk.t = 13.0                                                         # 7 s left < 2 * 4
    http, cache = FakeHttp(2020), MemCache()
    with pytest.raises(DeadlineExceeded):
        hy.history_year(42.3, -1.4, 580.0, 2020, cache=cache, http_get=http, today=TODAY, deadline=d)
    assert http.full == [] and cache.puts == []
    # a cached year is served even with no time left
    clk.t = 0.0
    hy.history_year(42.3, -1.4, 580.0, 2020, cache=cache, http_get=http, today=TODAY, deadline=d)
    clk.t = 19.0
    assert hy.history_year(42.3, -1.4, 580.0, 2020, cache=cache, http_get=http, today=TODAY, deadline=d)["year"] == 2020


def test_fetches_use_the_deadline_timeout_and_measure_the_estimate():
    clk = Clock()
    d = Deadline(20.0, clk)

    class Slow(FakeHttp):
        def __call__(self, url, params=None, timeout=None):
            if params["start_date"] != params["end_date"]:
                clk.t += 2.5
            return super().__call__(url, params, timeout)
    http = Slow(2020)
    hy.history_year(42.3, -1.4, 580.0, 2020, cache=MemCache(), http_get=http, today=TODAY, deadline=d)
    assert d.estimate == pytest.approx(2.5)
    assert all(isinstance(c[2], tuple) and c[2][0] == 3 for c in http.full)
    assert http.full[0][2][1] == pytest.approx(10.0) and http.full[1][2][1] == pytest.approx(8.75)


def test_open_year_becomes_closed_and_the_object_upgrades():
    cache = MemCache()
    http = FakeHttp(2025, hours=24 * 364)
    d1 = hy.history_year(42.3, -1.4, 580.0, 2025, cache=cache, http_get=http, today=date(2026, 1, 4))
    assert d1["closed"] is False
    http2 = FakeHttp(2025)
    d2 = hy.history_year(42.3, -1.4, 580.0, 2025, cache=cache, http_get=http2, today=date(2026, 4, 12))
    assert d2["closed"] is True and len(http2.full) == 2
    key = cache.puts[-1]
    assert cache.store[key]["closed"] is True and len(cache.puts) == 2


def test_probe_failure_is_an_archive_error_and_is_not_memoised():
    def bad(url, params=None, timeout=None):
        return _resp({}, 500)
    with pytest.raises(archive.ArchiveError):
        hy.history_year(42.3, -1.4, 580.0, 2020, cache=MemCache(), http_get=bad, today=TODAY)
    assert hy._CELL_PROBES == {}


def test_archive_errors_never_log_or_carry_the_coordinates(caplog):
    import requests

    def boom(url, params=None, timeout=None):
        raise requests.ConnectionError(f"HTTPSConnectionPool: /archive?latitude={params['latitude']}&longitude={params['longitude']}")
    with pytest.raises(archive.ArchiveError) as ei:
        archive.fetch_year(42.3456, -1.4321, 580.0, 2020, "grid", http_get=boom, today=TODAY)
    assert "42.3456" not in str(ei.value) and "1.4321" not in str(ei.value)
    assert "ConnectionError" in str(ei.value)

    def http500(url, params=None, timeout=None):
        return _resp({}, 503)
    with pytest.raises(archive.ArchiveError) as ei:
        archive.fetch_year(42.3456, -1.4321, 580.0, 2020, "corrected", http_get=http500, today=TODAY)
    assert "503" in str(ei.value) and "42.3456" not in str(ei.value)

    def junk(url, params=None, timeout=None):
        return _resp({"hourly": {"time": [1]}, "latitude": "42.3456"})
    with pytest.raises(archive.ArchiveError) as ei:
        archive.fetch_year(42.3456, -1.4321, 580.0, 2020, "grid", http_get=junk, today=TODAY)
    assert "42.3456" not in str(ei.value)
