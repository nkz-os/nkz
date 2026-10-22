"""Pure geo helpers for parcel-alert matching (no I/O)."""

from typing import Optional, Tuple


def parcel_centroid(entity: dict) -> Optional[Tuple[float, float]]:
    """Return the (lon, lat) centroid of an AgriParcel's `location` GeoProperty.

    Tolerates both the full NGSI-LD shape ({"type":"GeoProperty","value":{...}})
    and the keyValues shape (the geometry directly under `location`).
    """
    loc = entity.get("location")
    if not isinstance(loc, dict):
        return None
    value = loc.get("value", loc)  # keyValues → geometry is `loc` itself
    if not isinstance(value, dict):
        return None
    gtype, coords = value.get("type"), value.get("coordinates")
    if gtype == "Point" and isinstance(coords, list) and len(coords) >= 2:
        return (float(coords[0]), float(coords[1]))
    if gtype in ("Polygon", "MultiPolygon") and isinstance(coords, list) and coords:
        ring = coords[0] if gtype == "Polygon" else coords[0][0]
        pts = [p for p in ring if isinstance(p, list) and len(p) >= 2]
        # Drop the closing vertex of a closed ring so it doesn't skew the mean.
        if len(pts) > 1 and pts[0][:2] == pts[-1][:2]:
            pts = pts[:-1]
        if not pts:
            return None
        n = len(pts)
        return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    return None


def active_alert_filter(now_iso: str) -> str:
    """Orion NGSI-LD `q` selecting non-expired alerts (validTo stored as ISO str)."""
    return f'validTo>"{now_iso}"'
