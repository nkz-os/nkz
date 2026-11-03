"""GIS utilities for Nekazari module backends."""

from nkz_platform_sdk.gis.geofiles import GeoFileError, GeoFileResult, read_geofile
from nkz_platform_sdk.gis.terrain import aspect_degrees, slope_degrees

__all__ = [
    "GeoFileError",
    "GeoFileResult",
    "aspect_degrees",
    "read_geofile",
    "slope_degrees",
]
