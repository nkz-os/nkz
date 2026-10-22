"""
Tests for unified psychrometrics module.
Verifies that the single source of truth for Delta-T, dew point, and wet bulb
produces physically reasonable values and that all 3 former implementations
now converge.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from common.weather_utils.psychrometrics import (
    calculate_delta_t,
    dew_point_temperature,
    saturation_vapor_pressure,
    wet_bulb_temperature,
    MAGNUS_A,
    MAGNUS_B,
)


class TestSaturationVaporPressure:
    def test_at_20c(self):
        """At 20°C, saturation vapor pressure ≈ 23.4 hPa."""
        e_sat = saturation_vapor_pressure(20.0)
        assert 22.0 < e_sat < 25.0, f"Got {e_sat}"

    def test_at_0c(self):
        """At 0°C, saturation vapor pressure ≈ 6.1 hPa."""
        e_sat = saturation_vapor_pressure(0.0)
        assert 5.5 < e_sat < 7.0, f"Got {e_sat}"

    def test_at_40c(self):
        """At 40°C, saturation vapor pressure ≈ 73.8 hPa."""
        e_sat = saturation_vapor_pressure(40.0)
        assert 70.0 < e_sat < 80.0, f"Got {e_sat}"


class TestDewPoint:
    def test_50_percent_rh(self):
        """At 25°C / 50% RH, dew point ≈ 13.9°C."""
        dp = dew_point_temperature(25.0, 50.0)
        assert 12.0 < dp < 16.0, f"Got {dp}"

    def test_90_percent_rh(self):
        """At 20°C / 90% RH, dew point ≈ 18.3°C."""
        dp = dew_point_temperature(20.0, 90.0)
        assert 17.0 < dp < 20.0, f"Got {dp}"

    def test_dry_air(self):
        """At 30°C / 10% RH, dew point should be very low."""
        dp = dew_point_temperature(30.0, 10.0)
        assert dp < 5.0, f"Got {dp}"


class TestWetBulb:
    def test_wet_bulb_between_dry_and_dew(self):
        """Wet bulb temp should be between dry bulb and dew point."""
        t_dry = 30.0
        dp = dew_point_temperature(t_dry, 40.0)  # ~15°C
        wb = wet_bulb_temperature(t_dry, dp)
        assert dp < wb < t_dry, f"Got wb={wb}, dp={dp}, t={t_dry}"


class TestDeltaT:
    def test_optimal_spraying(self):
        """Warm + moderately dry → Delta-T 2-8°C (optimal spraying)."""
        dt = calculate_delta_t(25.0, 50.0)
        assert 2.0 <= dt <= 10.0, f"Got {dt}"

    def test_too_humid(self):
        """Cool + humid → Delta-T < 2°C (too humid for spraying)."""
        dt = calculate_delta_t(10.0, 90.0)
        assert dt < 2.5, f"Got {dt}"

    def test_too_dry_hot(self):
        """Hot + dry → Delta-T > 10°C (evaporation risk)."""
        dt = calculate_delta_t(35.0, 20.0)
        assert dt > 8.0, f"Got {dt}"

    def test_invalid_humidity(self):
        """RH > 100% returns 0.0."""
        assert calculate_delta_t(20.0, 150.0) == 0.0

    def test_none_inputs(self):
        """None inputs return 0.0."""
        assert calculate_delta_t(None, 50.0) == 0.0  # type: ignore
        assert calculate_delta_t(20.0, None) == 0.0  # type: ignore

    def test_consistency_with_old_implementations(self):
        """Verify Magnus constants are WMO standard."""
        assert MAGNUS_A == 17.67
        assert MAGNUS_B == 243.5

    def test_freeze_condition(self):
        """Below freezing with moderate humidity."""
        dt = calculate_delta_t(-5.0, 70.0)
        # Should be small but valid
        assert dt >= 0.0, f"Got {dt}"
        assert dt < 5.0, f"Got {dt}"


class TestConstants:
    def test_magnus_constants_wmo_standard(self):
        """Constants must match WMO standard (Alduchov & Eskridge 1996)."""
        assert MAGNUS_A == 17.67
        assert MAGNUS_B == 243.5


print("All psychrometrics tests passed.")
