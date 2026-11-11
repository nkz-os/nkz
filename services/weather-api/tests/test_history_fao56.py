"""FAO-56 helpers for the history core (stdlib only)."""
import os, sys
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import math
import pytest

from app.services.history import fao56


def test_example_18_et0_matches_fao56():
    # FAO-56 Example 18: Brussels, 6 July
    et0 = fao56.et0_fao56_daily(
        tmax=21.5, tmin=12.3, ea=1.409, u2=2.078, rs_mj=22.07,
        lat_deg=50.80, elev_m=100.0, doy=187,
    )
    assert et0 == pytest.approx(3.88, abs=0.01)


def test_vapour_pressure_from_dewpoint_eq14():
    assert fao56.vapour_pressure_kpa(15.0) == pytest.approx(1.7044, abs=1e-3)


def test_rh_from_t_and_dewpoint_capped_at_100():
    assert fao56.rh_pct(20.0, 20.0) == pytest.approx(100.0)
    assert fao56.rh_pct(10.0, 12.0) == 100.0
    assert fao56.rh_pct(25.0, 10.0) == pytest.approx(100 * 1.2279 / 3.1680, abs=0.2)


def test_wind_10m_to_2m_factor():
    assert fao56.wind_to_2m(10.0) == pytest.approx(10 * 4.87 / math.log(67.8 * 10 - 5.42))
    assert fao56.wind_to_2m(10.0) == pytest.approx(7.478, abs=1e-2)


def test_et0_never_negative():
    assert fao56.et0_fao56_daily(-5.0, -15.0, 0.2, 0.5, 0.5, 50.0, 800.0, 20) >= 0.0


def test_et0_none_without_extraterrestrial_radiation():
    assert fao56.et0_fao56_daily(-18.0, -25.0, 0.1, 2.0, 0.0, 85.0, 10.0, 355) is None
