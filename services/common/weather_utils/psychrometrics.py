"""
Psychrometrics — unified Magnus-formula calculations for agrometeorology.

Single source of truth for:
  - Dew point temperature
  - Wet bulb temperature
  - Delta-T (dry bulb - wet bulb depression)

All weather modules (weather-api, weather-worker, spatial downscaler, risk-worker)
MUST import from here. No other Magnus implementations should exist.

Constants from WMO standard (Alduchov & Eskridge 1996):
  a = 17.67, b = 243.5 °C  (over water, -40 to +50 °C range)
"""

import math
import logging

logger = logging.getLogger(__name__)

# Magnus coefficients — WMO standard for water surface
MAGNUS_A = 17.67
MAGNUS_B = 243.5

# Wet bulb approximation factor (empirical, Stull 2011)
WET_BULB_FACTOR = 0.4


def saturation_vapor_pressure(temp_celsius: float) -> float:
    """
    Saturation vapor pressure (hPa) using the Magnus formula.

    e_sat = 6.112 * exp((a * T) / (T + b))

    Args:
        temp_celsius: Temperature in °C

    Returns:
        Saturation vapor pressure in hPa
    """
    return 6.112 * math.exp((MAGNUS_A * temp_celsius) / (temp_celsius + MAGNUS_B))


def dew_point_temperature(
    temp_celsius: float, relative_humidity_percent: float
) -> float:
    """
    Calculate dew point temperature from dry bulb temperature and RH.

    T_dew = (b * ln(e / 6.112)) / (a - ln(e / 6.112))
    where e = e_sat * RH/100

    Args:
        temp_celsius: Dry bulb temperature in °C
        relative_humidity_percent: Relative humidity (0-100)

    Returns:
        Dew point temperature in °C

    Raises:
        ValueError: If RH is out of valid range
    """
    if relative_humidity_percent < 0 or relative_humidity_percent > 100:
        raise ValueError(
            f"Relative humidity must be 0-100%, got {relative_humidity_percent}"
        )

    e_sat = saturation_vapor_pressure(temp_celsius)
    e_act = e_sat * (relative_humidity_percent / 100.0)

    # Guard against log(0) or near-zero vapor pressure
    vapor_ratio = max(e_act / 6.112, 0.001)

    return (MAGNUS_B * math.log(vapor_ratio)) / (MAGNUS_A - math.log(vapor_ratio))


def wet_bulb_temperature(temp_celsius: float, dew_point_celsius: float) -> float:
    """
    Approximate wet bulb temperature from dry bulb and dew point.

    T_wet ≈ T - (T - T_dew) * 0.4   (Stull 2011 empirical formula)

    Args:
        temp_celsius: Dry bulb temperature in °C
        dew_point_celsius: Dew point temperature in °C

    Returns:
        Wet bulb temperature in °C (approximate)
    """
    return temp_celsius - (temp_celsius - dew_point_celsius) * WET_BULB_FACTOR


def calculate_delta_t(temp_celsius: float, relative_humidity_percent: float) -> float:
    """
    Calculate Delta-T (wet bulb depression) for spraying condition assessment.

    Delta-T = T_dry - T_wet

    Interpretation for agricultural spraying:
      - 2-8 °C  → optimal (droplets evaporate slowly, good retention)
      - 8-10 °C  → caution (fast evaporation, marginal)
      - < 2 °C   → caution (high humidity, droplets may not evaporate)
      - > 10 °C  → not suitable (extreme evaporation, drift risk)
      - < 0 °C   → not suitable (frost risk)

    Args:
        temp_celsius: Dry bulb temperature in °C
        relative_humidity_percent: Relative humidity (0-100)

    Returns:
        Delta-T in °C, or 0.0 if calculation fails
    """
    try:
        if temp_celsius is None or relative_humidity_percent is None:
            return 0.0

        dew_point = dew_point_temperature(temp_celsius, relative_humidity_percent)
        wet_bulb = wet_bulb_temperature(temp_celsius, dew_point)
        return round(temp_celsius - wet_bulb, 2)

    except (ValueError, OverflowError, ZeroDivisionError) as exc:
        logger.debug(f"Delta-T calculation failed: {exc}")
        return 0.0
