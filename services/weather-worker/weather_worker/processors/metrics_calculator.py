"""
Metrics Calculator - Calculate derived metrics (GDD, ET₀, Delta T, etc.)
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class MetricsCalculator:
    """Calculate derived metrics for predictive models"""

    def __init__(self, base_temp: float = 10.0):
        """
        Initialize metrics calculator

        Args:
            base_temp: Base temperature for GDD calculation (default: 10°C)
        """
        self.base_temp = base_temp

    def calculate_gdd(
        self,
        temp_max: Optional[float],
        temp_min: Optional[float],
        temp_avg: Optional[float] = None,
    ) -> Optional[float]:
        """
        Calculate Growing Degree Days (GDD)

        Formula: GDD = max(0, (T_max + T_min) / 2 - T_base)
        If T_max or T_min is missing, use T_avg if available

        Args:
            temp_max: Maximum temperature (°C)
            temp_min: Minimum temperature (°C)
            temp_avg: Average temperature (°C) - used as fallback

        Returns:
            GDD value or None if insufficient data
        """
        try:
            if temp_max is not None and temp_min is not None:
                avg_temp = (temp_max + temp_min) / 2.0
            elif temp_avg is not None:
                avg_temp = temp_avg
            else:
                return None

            gdd = max(0.0, avg_temp - self.base_temp)
            return round(gdd, 2)

        except Exception as e:
            logger.warning(f"Error calculating GDD: {e}")
            return None

    def calculate_accumulated_gdd(
        self, current_gdd: Optional[float], previous_accumulated: Optional[float] = None
    ) -> Optional[float]:
        """
        Calculate accumulated GDD

        Args:
            current_gdd: Current day GDD
            previous_accumulated: Previous accumulated GDD

        Returns:
            Accumulated GDD
        """
        if current_gdd is None:
            return previous_accumulated

        if previous_accumulated is None:
            return current_gdd

        return round(previous_accumulated + current_gdd, 2)

    def calculate_delta_t(
        self, temp_celsius: Optional[float], relative_humidity_percent: Optional[float]
    ) -> Optional[float]:
        """
        Calculate Delta T (temperature difference between dry and wet bulb).

        Delegates to unified psychrometrics module (common.weather_utils.psychrometrics)
        — single source of truth for all Magnus-formula calculations.

        Delta T is critical for spraying conditions:
        - Optimal: 2-8°C (good conditions for spraying)
        - Caution: 8-10°C or < 2°C (marginal conditions)
        - Not suitable: > 10°C (high evaporation risk, drift)

        Args:
            temp_celsius: Dry bulb temperature (°C)
            relative_humidity_percent: Relative humidity (%)

        Returns:
            Delta T value in °C or None if insufficient data
        """
        try:
            if temp_celsius is None or relative_humidity_percent is None:
                return None

            from weather_utils.psychrometrics import calculate_delta_t

            return calculate_delta_t(temp_celsius, relative_humidity_percent) or None

        except Exception as e:
            logger.warning(f"Error calculating Delta T: {e}")
            return None

    def calculate_water_balance(self, precipitation_mm: float, et0_mm: float) -> float:
        """
        Calculate water balance (Precipitation - ET₀)

        Used for irrigation needs assessment:
        - Positive: Soil has water (satisfied)
        - Negative: Water deficit (needs irrigation)
        - Threshold: -5mm indicates urgent irrigation need

        Args:
            precipitation_mm: Precipitation in mm
            et0_mm: Reference evapotranspiration in mm

        Returns:
            Water balance in mm
        """
        try:
            precip = precipitation_mm if precipitation_mm is not None else 0.0
            et0 = et0_mm if et0_mm is not None else 0.0

            balance = precip - et0
            return round(balance, 2)

        except Exception as e:
            logger.warning(f"Error calculating water balance: {e}")
            return 0.0

    def enrich_observation(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich observation with calculated metrics

        Args:
            observation: Weather observation dictionary

        Returns:
            Enriched observation with calculated metrics
        """
        try:
            # Calculate GDD if temperature data available
            gdd = self.calculate_gdd(
                temp_max=observation.get("temp_max"),
                temp_min=observation.get("temp_min"),
                temp_avg=observation.get("temp_avg"),
            )

            if gdd is not None:
                observation["gdd"] = gdd

            # Calculate Delta T if temperature and humidity available
            delta_t = self.calculate_delta_t(
                temp_celsius=observation.get("temp_avg"),
                relative_humidity_percent=observation.get("humidity_avg"),
            )

            if delta_t is not None:
                observation["delta_t"] = delta_t

            # Calculate water balance if precipitation and ET₀ available
            water_balance = self.calculate_water_balance(
                precipitation_mm=observation.get("precip_mm", 0.0),
                et0_mm=observation.get("eto_mm", 0.0),
            )

            observation["water_balance"] = water_balance

            # ET₀ should come from provider (Open-Meteo provides it)
            # If not available, we could calculate it using FAO Penman-Monteith
            # but that requires more variables (solar radiation, wind, etc.)
            # For now, we rely on provider to supply ET₀

            return observation

        except Exception as e:
            logger.error(f"Error enriching observation: {e}")
            return observation
