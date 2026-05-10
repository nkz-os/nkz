"""
Weather data processors
"""

from .metrics_calculator import MetricsCalculator
from .data_transformer import DataTransformer

# Shared spatial downscaler, located at /app/common/weather_utils/ in the container.
# /app/common is on sys.path (set in main.py).
from weather_utils.spatial_downscaler import (
    downscale_for_parcel,
    extract_parcel_terrain,
    interpolate_idw,
    correct_temperature_altitude,
    correct_solar_radiation_aspect,
    recalculate_delta_t,
)

__all__ = [
    "MetricsCalculator",
    "DataTransformer",
    "downscale_for_parcel",
    "extract_parcel_terrain",
    "interpolate_idw",
    "correct_temperature_altitude",
    "correct_solar_radiation_aspect",
    "recalculate_delta_t",
]
