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


# --- gdd_method (McMaster & Wilhelm 1997 Method 2; AquaCrop GDDmethod 3) ---

FROST_DAY = {"tmin_c": -4.0, "tmax_c": 8.0, "precip_mm": 0.0, "et0_mm": 0.5}
HOT_DAY = {"tmin_c": 18.0, "tmax_c": 34.0, "precip_mm": 0.0, "et0_mm": 6.0}
WARM_NIGHT = {"tmin_c": 28.0, "tmax_c": 36.0, "precip_mm": 0.0, "et0_mm": 7.0}


def test_default_method_is_legacy_and_unchanged():
    t = compute_totals([FROST_DAY], base_temp=0.0, upper_cutoff=26.0)
    assert t["gdd"] == 2.0  # max(0, (8 + -4)/2 - 0)
    assert compute_totals([FROST_DAY], 0.0, 26.0, gdd_method="legacy") == t


def test_m2_floors_tmin_and_tmax_at_base_before_averaging():
    # Tmin -4 -> 0; (8 + 0)/2 - 0 = 4.0 (legacy gives 2.0)
    assert compute_totals([FROST_DAY], 0.0, 26.0, gdd_method="m2")["gdd"] == 4.0
    # both below base: 0
    cold = {"tmin_c": -6.0, "tmax_c": -1.0}
    assert compute_totals([cold], 0.0, 26.0, gdd_method="m2")["gdd"] == 0.0


def test_m2_caps_tmin_and_tmax_at_upper():
    # Tmax 36 -> 26, Tmin 28 -> 26: (26 + 26)/2 - 0 = 26.0
    assert compute_totals([WARM_NIGHT], 0.0, 26.0, gdd_method="m2")["gdd"] == 26.0


def test_m2_without_upper_cutoff_has_no_cap():
    assert compute_totals([HOT_DAY], 0.0, None, gdd_method="m2")["gdd"] == 26.0


def test_aquacrop_m3_matches_library_formula():
    # AquaCrop compute_crop_calendar GDDmethod 3:
    # tmax.clip(Tbase, Tupp); tmin.clip(upper=Tupp); mean.clip(lower=Tbase) - Tbase
    # frost: Tmin stays -4 -> (8 + -4)/2 = 2.0
    assert compute_totals([FROST_DAY], 0.0, 26.0, gdd_method="aquacrop_m3")["gdd"] == 2.0
    # Tmax below base is raised to base, Tmin is not: (0 + -10)/2 = -5 -> floored to base -> 0
    cold = {"tmin_c": -10.0, "tmax_c": -2.0}
    assert compute_totals([cold], 0.0, 26.0, gdd_method="aquacrop_m3")["gdd"] == 0.0
    # Tmax 3 floored? no, 3 > 0 stays; Tmin -1 stays: (3 + -1)/2 = 1.0 (m2 would give 1.5)
    mild = {"tmin_c": -1.0, "tmax_c": 3.0}
    assert compute_totals([mild], 0.0, 26.0, gdd_method="aquacrop_m3")["gdd"] == 1.0
    assert compute_totals([mild], 0.0, 26.0, gdd_method="m2")["gdd"] == 1.5
    # upper: Tmax 36 -> 26, Tmin 28 -> 26: 26.0
    assert compute_totals([WARM_NIGHT], 0.0, 26.0, gdd_method="aquacrop_m3")["gdd"] == 26.0
    # BarleyGDD (Tbase 0, Tupp 15): Tmax 20 -> 15, Tmin 5: (15 + 5)/2 = 10.0
    barley = {"tmin_c": 5.0, "tmax_c": 20.0}
    assert compute_totals([barley], 0.0, 15.0, gdd_method="aquacrop_m3")["gdd"] == 10.0


def test_methods_keep_null_and_day_count_semantics():
    days = [FROST_DAY, {"tmin_c": None, "tmax_c": 10.0}]
    for m in ("legacy", "m2", "aquacrop_m3"):
        t = compute_totals(days, 0.0, 26.0, gdd_method=m)
        assert t["days_counted"]["gdd"] == 1
    assert compute_totals([{"tmin_c": None, "tmax_c": None}], 0.0, 26.0, gdd_method="m2")["gdd"] is None


def test_unknown_method_raises():
    import pytest
    with pytest.raises(ValueError):
        compute_totals([FROST_DAY], 0.0, 26.0, gdd_method="m9")
