"""Geometry helpers for entity-manager."""
import math
from typing import Any, Dict, List

# Spherical model on the WGS84 equatorial radius: within ~0.7 % of the ellipsoidal
# area at any latitude, with no extra dependency.
_EARTH_RADIUS_M = 6378137.0
_M2_PER_HA = 10000.0


def _ring_area_m2(ring: List[Any]) -> float:
    """Absolute spherical area of one lon/lat ring (degrees), in square metres."""
    if not isinstance(ring, list) or len(ring) < 3:
        return 0.0
    total = 0.0
    n = len(ring)
    for i in range(n):
        lon1, lat1 = math.radians(ring[i][0]), math.radians(ring[i][1])
        lon2, lat2 = math.radians(ring[(i + 1) % n][0]), math.radians(ring[(i + 1) % n][1])
        total += (lon2 - lon1) * (2.0 + math.sin(lat1) + math.sin(lat2))
    return abs(total) * _EARTH_RADIUS_M ** 2 / 2.0


def _polygon_area_m2(rings: List[Any]) -> float:
    """Exterior ring minus its holes."""
    if not isinstance(rings, list) or not rings:
        return 0.0
    area = _ring_area_m2(rings[0]) - sum(_ring_area_m2(hole) for hole in rings[1:])
    return max(area, 0.0)


def geometry_area_ha(geometry: Dict[str, Any]) -> float:
    """Geodesic area of a GeoJSON Polygon or MultiPolygon, in hectares (4 decimals).

    Holes are subtracted and every polygon of a MultiPolygon is summed. Any other
    geometry yields 0.0.
    """
    if not isinstance(geometry, dict):
        return 0.0
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if gtype == "Polygon":
        area_m2 = _polygon_area_m2(coords)
    elif gtype == "MultiPolygon":
        area_m2 = sum(_polygon_area_m2(poly) for poly in coords)
    else:
        return 0.0
    return round(area_m2 / _M2_PER_HA, 4)
