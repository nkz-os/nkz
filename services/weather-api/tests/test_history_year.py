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
    def __init__(self, year, grid_elev=450.0, hours=None):
        self.calls = []
        self.year, self.grid_elev, self.hours = year, grid_elev, hours

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params), timeout))
        if str(params["elevation"]) == "nan":
            return _resp(_body(self.year, self.grid_elev, 0.0, self.hours))
        return _resp(_body(self.year, float(params["elevation"]), 1.0, self.hours))


class MemCache:
    enabled = True

    def __init__(self):
        self.store, self.puts = {}, []

    def get(self, key):
        return self.store.get(key)

    def put(self, key, doc, closed):
        if closed and key in self.store:
            return False
        self.puts.append(key)
        self.store[key] = doc
        return True


@pytest.fixture(autouse=True)
def _archive_url(monkeypatch):
    monkeypatch.setattr(archive.settings, "openmeteo_archive_url", "http://om/v1")


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
    assert http.calls[0][1]["end_date"] == "2026-11-11"


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
    assert len(http.calls) == 2 and len(cache.puts) == 1
    assert cache.puts[0] == "weather-history/era5/v1/42.25_-1.50_580/2020.json.gz"
    # blend: coldest grid hour keeps grid T, hottest takes corrected (+1)
    assert doc["t"][0] == pytest.approx(10.0) and doc["t"][23] == pytest.approx(22.5)
    assert doc["td"][0] == pytest.approx(5.0)                       # corrected response
    assert doc["precip"][0] == pytest.approx(0.1)
    assert doc["rh"][0] is not None and 0 < doc["rh"][0] <= 100
    assert doc["provenance"]["t_c"]["altitude_method"] == "blend"
    again = hy.history_year(42.3, -1.4, 580.0, 2020, cache=cache, http_get=http, today=today)
    assert again == doc and len(http.calls) == 2 and len(cache.puts) == 1


def test_history_year_coastal_uses_corrected_and_says_so():
    http, cache = FakeHttp(2020, grid_elev=-3.0), MemCache()
    doc = hy.history_year(42.3, -1.4, 20.0, 2020, cache=cache, http_get=http, today=date(2026, 11, 11))
    assert doc["coastal"] is True
    assert doc["t"][0] == pytest.approx(11.0) and doc["t"][23] == pytest.approx(22.5)
    assert doc["provenance"]["t_c"]["altitude_method"] == "elevation_corrected_coastal"


def test_current_year_open_refreshed_when_stale_not_when_fresh():
    http, cache = FakeHttp(2026, hours=24 * 100), MemCache()
    d1 = date(2026, 4, 12)
    doc = hy.history_year(42.3, -1.4, 580.0, 2026, cache=cache, http_get=http, today=d1)
    assert doc["closed"] is False and doc["latest_available"] == "2026-04-09"
    assert len(http.calls) == 2
    hy.history_year(42.3, -1.4, 580.0, 2026, cache=cache, http_get=http, today=d1)
    assert len(http.calls) == 2                                      # same day: served from cache
    http2 = FakeHttp(2026, hours=24 * 101)
    doc2 = hy.history_year(42.3, -1.4, 580.0, 2026, cache=cache, http_get=http2, today=date(2026, 4, 13))
    assert len(http2.calls) == 2 and doc2["latest_available"] == "2026-04-10"
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


def test_snap_cell_ties_go_to_plus_infinity():
    assert hy.snap_cell(42.6, -1.6) == (42.5, -1.5)
    assert hy.snap_cell(42.125, -1.625) == (42.25, -1.5)
    assert hy.snap_cell(37.88, -1.64) == (38.0, -1.75)
    assert hy.snap_cell(51.3, 9.4) == (51.25, 9.5)


def test_returned_cell_mismatch_serves_but_does_not_cache(caplog):
    class Shifted(FakeHttp):
        def __call__(self, url, params=None, timeout=None):
            self.calls.append((url, dict(params), timeout))
            b = _body(self.year, 450.0, lat=42.0, lon=-1.0)   # not the snapped 42.25,-1.5
            return _resp(b)
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
    assert len(http.calls) == 2 and doc["cell_lat"] == 42.25 and doc["t"][0] != "stale"


def test_parcel_elevation_rounded_before_fetch_and_key():
    http, cache = FakeHttp(2020), MemCache()
    doc = hy.history_year(42.3, -1.4, 579.6, 2020, cache=cache, http_get=http, today=date(2026, 11, 11))
    assert http.calls[1][1]["elevation"] == 580.0 and doc["elev"] == 580.0
    assert cache.puts == ["weather-history/era5/v1/42.25_-1.50_580/2020.json.gz"]
