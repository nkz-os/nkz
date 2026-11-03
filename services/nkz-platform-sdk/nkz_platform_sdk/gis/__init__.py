"""GIS utilities for Nekazari module backends."""

from nkz_platform_sdk.gis.geofiles import GeoFileError, GeoFileResult, read_geofile
from nkz_platform_sdk.gis.terrain import aspect_degrees, slope_degrees
from nkz_platform_sdk.gis.zones import (
    ZoneRef,
    Zoning,
    default_zoning,
    fetch_zonings,
    group_zonings,
    is_automatic,
    point_in_polygon,
    zone_of,
)

__all__ = [
    "GeoFileError",
    "GeoFileResult",
    "ZoneRef",
    "Zoning",
    "aspect_degrees",
    "default_zoning",
    "fetch_zonings",
    "group_zonings",
    "is_automatic",
    "point_in_polygon",
    "read_geofile",
    "slope_degrees",
    "zone_of",
]
