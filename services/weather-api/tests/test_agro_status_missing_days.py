"""A 3-day window without records must read as unknown, never as 0 mm."""
import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from app.services.agro_status import calculate_agro_status

_OBS = {"temp_avg": 12.0, "humidity_avg": 30, "wind_speed_ms": 2.0}


def test_no_daily_rows_gives_unknown_precip():
    out = calculate_agro_status(42.8, -1.6, {}, dict(_OBS), weather_3d=[])
    w = out["weather"]
    assert w["precipitation_3d"] is None
    assert w["eto_3d"] is None
    assert w["water_balance"] is None
    # Dry air alone must not be read as "no rain in 3 days".
    assert out["semaphores"]["workability"] != "too_dry"


def test_partial_days_sum_what_exists():
    days = [{"precip_mm": 2.0, "eto_mm": 1.5}, {"precip_mm": None, "eto_mm": 2.0}]
    w = calculate_agro_status(42.8, -1.6, {}, dict(_OBS), weather_3d=days)["weather"]
    assert w["precipitation_3d"] == 2.0
    assert w["eto_3d"] == 3.5
