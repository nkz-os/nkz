"""Local-day aggregation: DST days, interval-end shift, gaps, ET0 wiring."""
import os, sys
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest

from app.services.history import aggregate

UTC = timezone.utc


def _doc(tz, start_local, hours, **cols):
    """Hourly doc from local midnight of start_local; cols are value or callable(i)."""
    z = ZoneInfo(tz)
    t0 = datetime(start_local.year, start_local.month, start_local.day, tzinfo=z).astimezone(UTC)
    times = [int((t0 + timedelta(hours=i)).timestamp()) for i in range(hours)]
    doc = {"year": start_local.year, "lat": 41.0, "lon": -1.0, "elev": 300.0, "tz": tz, "times": times}
    for name in ("t", "td", "rh", "precip", "rad", "wind10"):
        v = cols.get(name, 10.0)
        doc[name] = [v(i) if callable(v) else v for i in range(hours)]
    return doc


def _row(rows, d):
    return next(r for r in rows if r["date"] == d)


def test_expected_hours_dst_days():
    assert aggregate.expected_hours(date(2024, 3, 31), "Europe/Madrid") == 23
    assert aggregate.expected_hours(date(2024, 10, 27), "Europe/Madrid") == 25
    assert aggregate.expected_hours(date(2024, 6, 1), "Europe/Madrid") == 24
    assert aggregate.expected_hours(date(2024, 3, 31), "UTC") == 24


def test_dst_days_aggregate_with_23_and_25_hours():
    # 2024-03-30 .. 2024-04-01 (72 - 1 h) and 2024-10-26 .. 28 (+1 h)
    spring = _doc("Europe/Madrid", date(2024, 3, 30), 24 + 23 + 24)
    rows = aggregate.daily_from_hourly(spring)
    r = _row(rows, "2024-03-31")
    assert r["tmean_c"] == pytest.approx(10.0) and r["tmin_c"] == 10.0 and r["tmax_c"] == 10.0
    autumn = _doc("Europe/Madrid", date(2024, 10, 26), 24 + 25 + 24)
    r = _row(aggregate.daily_from_hourly(autumn), "2024-10-27")
    assert r["tmean_c"] == pytest.approx(10.0)
    # constant 100 W/m2 over the 25 h day: 25 * 100 * 3600 / 1e6 MJ
    autumn = _doc("Europe/Madrid", date(2024, 10, 26), 24 + 25 + 24 + 1, rad=100.0)
    r = _row(aggregate.daily_from_hourly(autumn), "2024-10-27")
    assert r["radiation_mj_m2"] == pytest.approx(25 * 0.36)


def test_dst_day_missing_one_hour_is_null():
    spring = _doc("Europe/Madrid", date(2024, 3, 30), 24 + 23 + 24, t=lambda i: None if i == 30 else 10.0)
    r = _row(aggregate.daily_from_hourly(spring), "2024-03-31")
    assert r["tmean_c"] is None and r["tmax_c"] is None


def test_interval_end_shift_for_precip_and_radiation():
    # stamp at local 00:00 of D belongs to D-1; stamp at 00:00 of D+1 belongs to D
    doc = _doc("UTC", date(2024, 1, 10), 49,
               precip=lambda i: 100.0 if i == 0 else (1.0 if i < 24 else (7.0 if i == 24 else 0.0)),
               rad=lambda i: 999.0 if i == 0 else 100.0)
    rows = aggregate.daily_from_hourly(doc)
    d = _row(rows, "2024-01-10")
    # D covers stamps i=1..24: 23 * 1.0 + 7.0
    assert d["precip_mm"] == pytest.approx(30.0)
    assert d["radiation_mj_m2"] == pytest.approx(24 * 0.36)


def test_missing_hour_nulls_only_that_variable():
    doc = _doc("UTC", date(2024, 1, 10), 49, t=lambda i: None if i == 5 else 8.0)
    d = _row(aggregate.daily_from_hourly(doc), "2024-01-10")
    assert d["tmin_c"] is None and d["tmean_c"] is None and d["et0_mm"] is None
    assert d["precip_mm"] is not None and d["wind2m_ms"] is not None


def test_year_boundary_needs_next_year_for_interval_end_vars():
    z = "UTC"
    a = _doc(z, date(2023, 12, 30), 48)            # ends 2023-12-31T23
    b = _doc(z, date(2024, 1, 1), 48)
    only_a = _row(aggregate.daily_from_hourly(a), "2023-12-31")
    assert only_a["tmean_c"] == pytest.approx(10.0) and only_a["precip_mm"] is None
    both = _row(aggregate.daily_from_hourly([a, b]), "2023-12-31")
    assert both["precip_mm"] == pytest.approx(24 * 10.0)


def test_wind_to_2m_vapour_pressure_rh_and_et0():
    doc = _doc("UTC", date(2024, 7, 5), 49, t=lambda i: 15.0 + (i % 24) / 2.0, td=8.0, rh=60.0,
               wind10=3.0, rad=lambda i: 800.0 if 6 <= i % 24 <= 18 else 0.0)
    d = _row(aggregate.daily_from_hourly(doc), "2024-07-05")
    assert d["wind2m_ms"] == pytest.approx(3.0 * 0.7478, abs=6e-3)
    assert d["vapour_pressure_kpa"] == pytest.approx(1.0720, abs=1e-3)
    assert d["rh_mean_pct"] == pytest.approx(60.0)
    assert d["tmax_c"] == pytest.approx(26.5) and d["tmin_c"] == pytest.approx(15.0)
    assert 3.0 < d["et0_mm"] < 9.0


def test_days_after_latest_available_are_absent():
    doc = _doc("UTC", date(2024, 1, 10), 24 * 4, )
    doc["latest_available"] = "2024-01-11"
    dates = [r["date"] for r in aggregate.daily_from_hourly(doc)]
    assert dates == ["2024-01-10", "2024-01-11"]


def test_hourly_rows_units_and_nulls():
    doc = _doc("UTC", date(2024, 1, 10), 3, t=lambda i: None if i == 1 else 5.0, wind10=10.0)
    rows = aggregate.hourly_rows(doc)
    assert len(rows) == 3
    assert rows[1]["t_c"] is None and rows[0]["t_c"] == 5.0
    assert rows[0]["wind2m_ms"] == pytest.approx(7.478, abs=1e-2)
    assert rows[0]["time"] == "2024-01-10T00:00:00Z"
