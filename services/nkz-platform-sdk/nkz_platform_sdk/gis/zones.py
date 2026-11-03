"""Zone membership for samples: zonings of a parcel and point-in-polygon.

A parcel can carry several zonings at once (one per run: NDVI clustering,
terrain, a manually drawn experiment, ...). Samples keep their own coordinates;
which zone they fall in is computed here at read time, for whichever zoning the
caller chooses. The default is the most recent automatic zoning: a manual
zoning is only used when it is chosen explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ZONE_TYPE = "AgriManagementZone"
_PAGE = 100
_MAX_PAGES = 100
_PREFIXES = ("", "nkz:", "https://nkz-os.org/ns/", "https://uri.etsi.org/ngsi-ld/default-context/")


@dataclass(frozen=True)
class ZoneRef:
    id: str
    label: str | None
    rank: int | None
    geometry: dict


@dataclass(frozen=True)
class Zoning:
    run_id: str
    method: str | None
    date: str | None
    zones: tuple[ZoneRef, ...]


def _unwrap(attr: Any) -> Any:
    if isinstance(attr, dict):
        if "object" in attr:
            return attr["object"]
        if "value" in attr:
            attr = attr["value"]
    if isinstance(attr, dict) and "@value" in attr:
        return attr["@value"]
    return attr


def _get(entity: dict, name: str) -> Any:
    """Read an attribute whether it arrives compacted, prefixed or as a full IRI."""
    for prefix in _PREFIXES:
        if prefix + name in entity:
            return _unwrap(entity[prefix + name])
    return None


def is_automatic(method: str | None) -> bool:
    """Every zoning method is automatic except ``manual``; no method (legacy) is automatic."""
    return method != "manual"


def group_zonings(entities: list[dict]) -> list[Zoning]:
    """Group zone entities by run, most recent first (undated runs last)."""
    runs: dict[str, dict] = {}
    for e in entities:
        method = _get(e, "zoningMethod")
        run_id = _get(e, "zoningRunId") or f"legacy:{method or 'none'}"
        rank = _get(e, "zoneRank")
        if rank is None and isinstance(_get(e, "zoneId"), (int, float)):
            rank = int(_get(e, "zoneId")) + 1  # vegetation numbers its clusters from 0
        zone = ZoneRef(
            id=e.get("id", ""),
            label=_get(e, "zoneLabel") or _get(e, "zoneName"),
            rank=int(rank) if isinstance(rank, (int, float)) else None,
            geometry=_get(e, "location") or {},
        )
        date = _get(e, "dateZoned") or e.get("modifiedAt")
        run = runs.setdefault(str(run_id), {"method": method, "date": date, "zones": []})
        run["zones"].append(zone)
        if date and (run["date"] is None or str(date) > str(run["date"])):
            run["date"] = date
    zonings = [
        Zoning(run_id=rid, method=r["method"], date=r["date"], zones=tuple(r["zones"]))
        for rid, r in runs.items()
    ]
    dated = sorted((z for z in zonings if z.date), key=lambda z: str(z.date), reverse=True)
    return dated + [z for z in zonings if not z.date]


def default_zoning(zonings: list[Zoning]) -> Zoning | None:
    """The most recent automatic zoning, or None."""
    for z in zonings:
        if is_automatic(z.method):
            return z
    return None


def _on_segment(px: float, py: float, a: list, b: list) -> bool:
    (ax, ay), (bx, by) = a[:2], b[:2]
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > 1e-12:
        return False
    return min(ax, bx) - 1e-12 <= px <= max(ax, bx) + 1e-12 and min(ay, by) - 1e-12 <= py <= max(ay, by) + 1e-12


def _on_ring(px: float, py: float, ring: list) -> bool:
    return any(_on_segment(px, py, ring[i], ring[i + 1]) for i in range(len(ring) - 1))


def _in_ring(px: float, py: float, ring: list) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][:2]
        xj, yj = ring[j][:2]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_polygon(px: float, py: float, rings: list) -> bool:
    if not rings:
        return False
    if any(_on_ring(px, py, r) for r in rings):
        return True
    return _in_ring(px, py, rings[0]) and not any(_in_ring(px, py, h) for h in rings[1:])


def point_in_polygon(lon: float, lat: float, geometry: dict) -> bool:
    """True if the point is inside (or on the border of) a Polygon/MultiPolygon; holes are outside."""
    kind = (geometry or {}).get("type")
    coords = (geometry or {}).get("coordinates") or []
    if kind == "Polygon":
        return _in_polygon(lon, lat, coords)
    if kind == "MultiPolygon":
        return any(_in_polygon(lon, lat, poly) for poly in coords)
    return False


def zone_of(lon: float, lat: float, zoning: Zoning) -> ZoneRef | None:
    """The zone of ``zoning`` containing the point; on shared borders the lowest rank wins."""
    hits = [z for z in zoning.zones if point_in_polygon(lon, lat, z.geometry)]
    if not hits:
        return None
    hits.sort(key=lambda z: (z.rank is None, z.rank if z.rank is not None else 0, z.id))
    return hits[0]


async def fetch_zonings(client: Any, parcel_id: str) -> list[Zoning]:
    """All zonings of a parcel from Orion (``client`` is an ``OrionClient``)."""
    entities: list[dict] = []
    for page in range(_MAX_PAGES):
        batch = await client.query_entities(
            type=ZONE_TYPE,
            q=f'hasAgriParcel=="{parcel_id}"',
            limit=_PAGE,
            offset=page * _PAGE,
            options="sysAttrs",
        )
        entities.extend(batch)
        if len(batch) < _PAGE:
            break
    return group_zonings(entities)
