"""Totals over closed days: null when nothing contributes, never 0."""
import os, sys
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from app.services.daily_totals import compute_totals


def test_sums_only_days_with_values():
    days = [
        {"tmin_c": 8.0, "tmax_c": 20.0, "precip_mm": 2.0, "et0_mm": 3.0},
        {"tmin_c": None, "tmax_c": 18.0, "precip_mm": None, "et0_mm": 2.5},
    ]
    t = compute_totals(days, base_temp=None, upper_cutoff=None)
    assert t["precip_mm"] == 2.0 and t["et0_mm"] == 5.5
    assert t["days_counted"] == {"precip_mm": 1, "et0_mm": 2}
    assert "gdd" not in t


def test_no_values_is_null_not_zero():
    t = compute_totals([{"tmin_c": None, "tmax_c": None, "precip_mm": None, "et0_mm": None}], 10.0, None)
    assert t["precip_mm"] is None and t["et0_mm"] is None and t["gdd"] is None
    assert t["days_counted"]["gdd"] == 0


def test_gdd_with_cutoff():
    days = [{"tmin_c": 10.0, "tmax_c": 34.0, "precip_mm": 0.0, "et0_mm": 1.0}]
    t = compute_totals(days, base_temp=10.0, upper_cutoff=30.0)
    assert t["gdd"] == 10.0  # (min(34,30)+10)/2 - 10
    assert t["precip_mm"] == 0.0  # a real 0 mm day stays 0
