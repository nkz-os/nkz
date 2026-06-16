"""Pure geometry validation for AgriParcel. No Flask, no DB."""
from typing import Any, Dict


class GeometryError(ValueError):
    """Raised when a parcel geometry is invalid."""


def _validate_ring(ring: Any) -> None:
    if not isinstance(ring, list) or len(ring) < 4:
        raise GeometryError("Polygon ring needs at least 4 positions (closed, >=3 distinct)")
    if ring[0] != ring[-1]:
        raise GeometryError("Polygon ring must be closed (first == last position)")
    for pos in ring:
        if (not isinstance(pos, list) or len(pos) < 2
                or not all(isinstance(c, (int, float)) for c in pos[:2])):
            raise GeometryError("Each position must be [lon, lat]")


def validate_parcel_geometry(geometry: Dict[str, Any]) -> None:
    """Validate a GeoJSON Polygon/MultiPolygon for an AgriParcel. Raises GeometryError."""
    if not isinstance(geometry, dict) or "type" not in geometry or "coordinates" not in geometry:
        raise GeometryError("Geometry must be a dict with 'type' and 'coordinates'")
    gtype = geometry["type"]
    coords = geometry["coordinates"]
    if gtype == "Polygon":
        if not isinstance(coords, list) or not coords:
            raise GeometryError("Polygon coordinates must be a non-empty array of rings")
        for ring in coords:
            _validate_ring(ring)
    elif gtype == "MultiPolygon":
        if not isinstance(coords, list) or not coords:
            raise GeometryError("MultiPolygon coordinates must be a non-empty array of polygons")
        for poly in coords:
            for ring in poly:
                _validate_ring(ring)
    else:
        raise GeometryError(f"Unsupported geometry type {gtype!r}; expected Polygon/MultiPolygon")
