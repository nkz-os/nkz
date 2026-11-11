"""Hourly altitude blend (same cases as the validation harness)."""
import os, sys
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest

from app.services.history import blend

UTC = timezone.utc
MAD = ZoneInfo("Europe/Madrid")


def _ts(y, m, d, h=0):
    return int(datetime(y, m, d, h, tzinfo=UTC).timestamp())


def _day(y, m, d):
    return [_ts(y, m, d, h) for h in range(24)]


def test_weights_coldest_grid_hottest_corrected():
    times = _day(2024, 1, 10)
    grid = [float(h) for h in range(24)]
    corr = [float(h) + 2.0 for h in range(24)]
    out = blend.blend_temperature(times, grid, corr, "UTC")
    assert out[0] == pytest.approx(0.0)
    assert out[23] == pytest.approx(25.0)
    assert out[12] == pytest.approx(12 + 12 / 23 * 2.0)


def test_flat_day_is_midpoint():
    out = blend.blend_temperature(_day(2024, 1, 10), [5.0] * 24, [7.0] * 24, "UTC")
    assert all(v == pytest.approx(6.0) for v in out)


def test_sea_cell_uses_corrected_everywhere():
    times = _day(2024, 1, 10)
    grid = [float(h) for h in range(24)]
    corr = [float(h) + 2.0 for h in range(24)]
    assert blend.blend_temperature(times, grid, corr, "UTC", sea=True) == corr


def test_missing_hour_stays_none():
    times = _day(2024, 1, 10)
    grid = [float(h) for h in range(24)]
    corr = [float(h) for h in range(24)]
    corr[5] = None
    grid[6] = None
    out = blend.blend_temperature(times, grid, corr, "UTC")
    assert out[5] is None and out[6] is None
    assert out[7] == pytest.approx(7.0)


def test_each_day_has_its_own_cycle():
    times = _day(2024, 1, 10) + _day(2024, 1, 11)
    grid = [0.0 if h < 12 else 10.0 for h in range(24)] + [100.0 if h < 12 else 110.0 for h in range(24)]
    corr = [v + 1.0 for v in grid]
    out = blend.blend_temperature(times, grid, corr, "UTC")
    assert out[0] == pytest.approx(0.0)
    assert out[24] == pytest.approx(100.0)
    assert out[44] == pytest.approx(111.0)


def test_local_day_on_dst_change_day():
    # 2024-03-31 Europe/Madrid has 23 h: UTC 03-30T23 .. 03-31T21
    t0 = datetime(2024, 3, 30, 23, tzinfo=UTC)
    prev = [t0 - timedelta(hours=i) for i in (3, 2, 1)]
    day = [t0 + timedelta(hours=i) for i in range(23)]
    nxt = [t0 + timedelta(hours=23 + i) for i in range(3)]
    times = [int(x.timestamp()) for x in prev + day + nxt]
    grid = [-50.0] * 3 + [float(i) for i in range(23)] + [80.0] * 3
    corr = [v + 1.0 for v in grid]
    out = blend.blend_temperature(times, grid, corr, "Europe/Madrid")
    assert out[3] == pytest.approx(0.0)
    assert out[3 + 22] == pytest.approx(23.0)
    assert out[3 + 11] == pytest.approx(11 + 11 / 22)
    # a UTC-day split would not give 0.0 at the first local hour
    assert blend.blend_temperature(times, grid, corr, "UTC")[3] != pytest.approx(0.0)
