"""
Shared weather utilities — used by both weather-api (on-the-fly correction)
and weather-worker (batch ingestion).

Spatial downscaler corrects municipality-level weather for individual parcels
using altitude, aspect, slope, and multi-station interpolation.

Psychrometrics provides unified Magnus-formula calculations (Delta-T, dew point,
wet bulb) — single source of truth for all modules.
"""

from .psychrometrics import (
    calculate_delta_t,
    dew_point_temperature,
    saturation_vapor_pressure,
    wet_bulb_temperature,
    MAGNUS_A,
    MAGNUS_B,
)
from .spatial_downscaler import (
    correct_temperature_altitude,
    correct_solar_radiation_aspect,
    downscale_for_parcel,
    extract_parcel_terrain,
    interpolate_idw,
    recalculate_delta_t,
)

__all__ = [
    "correct_temperature_altitude",
    "correct_solar_radiation_aspect",
    "downscale_for_parcel",
    "extract_parcel_terrain",
    "interpolate_idw",
    "recalculate_delta_t",
    "calculate_delta_t",
    "dew_point_temperature",
    "saturation_vapor_pressure",
    "wet_bulb_temperature",
    "MAGNUS_A",
    "MAGNUS_B",
]
