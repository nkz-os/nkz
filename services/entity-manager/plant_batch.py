"""Individual plants (AgriPlant) of a parcel: validation, codes and entities.

Pure functions, no network. Shared by the entity wizard (manual placement, rows,
grids), file imports (GPX/KML/KMZ/GeoJSON/CSV) and the mobile app, through the
plants endpoints. Ids are built here so every caller gets the same identity:
``urn:ngsi-ld:AgriPlant:{tenant}:{parcelShort}:{plantCode}``; re-sending a code
updates that plant.

Distances use a local equirectangular projection in metres around the parcel:
accurate to a few centimetres over a parcel, which is all a GPS tolerance needs.
"""
from __future__ import annotations

import base64
import binascii
import csv
import io
import math
import re
import unicodedata
from datetime import date

from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.ops import transform

GPS_TOLERANCE_M = 10.0     # outside the parcel by at most this: warning (consumer GPS error)
MIN_SPACING_M = 1.0        # two plants closer than this: warning
SAME_SPOT_M = 0.5          # an uncoded point this close to an existing plant is that plant again
MOVED_M = 2.0              # an update that moves a plant farther than this: warning
MAX_PLANTS = 5000          # per request
PLANT_STATUS = ("alive", "dead", "removed", "replaced")
_NUMERIC = ("height", "trunkPerimeter", "trunkPerimeterHeight", "canopyDiameter")
_TEXT = ("variety",)
_INT = ("rowNumber", "positionInRow")
_CODE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_PREFIX = re.compile(r"^[A-Za-z0-9._-]{0,16}$")
FILE_FORMATS = ("gpx", "kml", "kmz", "geojson", "csv")
_CROP_CONCEPT_KEYS = ("agroVocConcept", "eppoCode")


class FileError(ValueError):
    """The file cannot be read."""


class ParcelGeometryError(ValueError):
    """The parcel has no polygon to place plants in."""


class TooManyPlants(ValueError):
    """More plants than one request may carry."""


def _parcel_short(urn: str) -> str:
    return urn.rsplit(":", 1)[-1]


def plant_id(tenant: str, parcel_urn: str, code: str) -> str:
    return f"urn:ngsi-ld:AgriPlant:{tenant}:{_parcel_short(parcel_urn)}:{code}"


def valid_prefix(prefix: str) -> bool:
    return isinstance(prefix, str) and bool(_PREFIX.match(prefix))


def _unwrap(v):
    if isinstance(v, dict):
        v = v.get("value", v.get("object"))
    if isinstance(v, dict) and "@value" in v:
        v = v["@value"]
    return v


def _value(entity: dict | None, attr: str):
    return _unwrap((entity or {}).get(attr))


def _value_any(entity: dict | None, names: tuple[str, ...]):
    """First of several term names; also accepts expanded IRIs ending with the name.
    Terms sharing an IRI come back compacted under either name (e.g. refAgriCrop)."""
    entity = entity or {}
    for n in names:
        if n in entity:
            return _unwrap(entity[n])
    for k, v in entity.items():
        if any(k.endswith("/" + n) for n in names):
            return _unwrap(v)
    return None


def crop_ref(parcel: dict) -> str | None:
    ref = _value_any(parcel, ("hasAgriCrop", "refAgriCrop"))
    if isinstance(ref, list):
        ref = ref[0] if ref else None
    return ref if isinstance(ref, str) else None


def _projector(lat0: float):
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_540.0
    return lambda x, y, z=None: (x * kx, y * ky)


def _valid_point(item: dict) -> tuple[float, float] | None:
    lat, lon = item.get("lat"), item.get("lon")
    if isinstance(lat, bool) or isinstance(lon, bool) or not isinstance(lat, (int, float)) \
            or not isinstance(lon, (int, float)):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return float(lat), float(lon)


def _attr_errors(item: dict) -> str | None:
    """First invalid optional field, or None."""
    status = item.get("plantStatus")
    if status is not None and status not in PLANT_STATUS:
        return "plantStatus"
    planted = item.get("datePlanted")
    if planted is not None:
        try:
            date.fromisoformat(str(planted))
        except ValueError:
            return "datePlanted"
    for f in _NUMERIC:
        v = item.get(f)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
            return f
    for f in _INT:
        v = item.get(f)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 1):
            return f
    code = item.get("plantCode")
    if code is not None and (not isinstance(code, str) or not _CODE.match(code)):
        return "plantCode"
    return None


def _entity(tenant: str, parcel_urn: str, code: str, lat: float, lon: float, item: dict,
            crop: dict | None) -> dict:
    e: dict = {
        "id": plant_id(tenant, parcel_urn, code),
        "type": "AgriPlant",
        "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [lon, lat]}},
        "hasAgriParcel": {"type": "Relationship", "object": parcel_urn},
        "plantCode": {"type": "Property", "value": code},
    }
    for f in _TEXT + _NUMERIC + _INT + ("plantStatus",):
        if item.get(f) is not None:
            e[f] = {"type": "Property", "value": item[f]}
    if item.get("datePlanted"):
        e["datePlanted"] = {"type": "Property", "value": str(item["datePlanted"])}  # SDM: a date string
    if crop:
        e["hasAgriCrop"] = {"type": "Relationship", "object": crop["id"]}
        concept = _value_any(crop, _CROP_CONCEPT_KEYS)
        if isinstance(concept, str) and re.match(r"^https?://", concept):  # SDM: format uri
            e["agroVocConcept"] = {"type": "Property", "value": concept}
    return e


class _Grid:
    """Points in metres hashed into 1 m cells: neighbour lookups without an O(n²) scan."""

    def __init__(self):
        self.cells: dict[tuple[int, int], list[tuple[str, Point]]] = {}

    def add(self, code: str, p: Point) -> None:
        self.cells.setdefault((int(p.x // 1), int(p.y // 1)), []).append((code, p))

    def nearest(self, p: Point, within: float, skip: str | None = None) -> tuple[str, float] | None:
        cx, cy = int(p.x // 1), int(p.y // 1)
        r = int(math.ceil(within))
        best = None
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                for code, q in self.cells.get((cx + dx, cy + dy), ()):
                    if code == skip:
                        continue
                    d = p.distance(q)
                    if d < within and (best is None or d < best[1]):
                        best = (code, d)
        return best


def _parcel_area(parcel: dict):
    loc = _value(parcel, "location")
    try:
        geom = shape(loc) if isinstance(loc, dict) else None
    except Exception:
        geom = None
    if not isinstance(geom, (Polygon, MultiPolygon)) or geom.is_empty:
        raise ParcelGeometryError("parcel has no polygon")
    return geom


def _next_free(prefix: str, taken: set[str]) -> str:
    n = 0
    for c in taken:
        if c.startswith(prefix) and c[len(prefix):].isdigit():
            n = max(n, int(c[len(prefix):]))
    return f"{prefix}{n + 1}"


def prepare_plants(tenant: str, parcel: dict, items: list[dict], existing: dict[str, dict],
                   crop: dict | None, code_prefix: str = "") -> dict:
    """Validate plants for one parcel. ``existing``: AgriPlant entities of the parcel by code.
    Codes assigned here (no ``plantCode`` given) are flagged ``auto``."""
    if len(items) > MAX_PLANTS:
        raise TooManyPlants(f"at most {MAX_PLANTS} plants per request")
    parcel_urn = parcel["id"]
    geom = _parcel_area(parcel)
    to_m = _projector(geom.centroid.y)
    parcel_m = transform(to_m, geom)

    existing_grid = _Grid()
    existing_pts: dict[str, Point] = {}
    for code, ent in existing.items():
        loc = _value(ent, "location")
        if isinstance(loc, dict) and loc.get("type") == "Point" and len(loc.get("coordinates") or []) >= 2:
            pt = Point(to_m(*loc["coordinates"][:2]))
            existing_grid.add(code, pt)
            existing_pts[code] = pt

    accepted: list[dict] = []
    rejected: list[dict] = []
    warnings: list[dict] = []
    taken = set(existing)
    request_grid = _Grid()
    seen: set[str] = set()

    for i, item in enumerate(items):
        code = item.get("plantCode")
        point = _valid_point(item)
        base = {"index": i, **({"plantCode": code} if code else {})}
        if point is None:
            rejected.append({**base, "reason": "invalid_point"})
            continue
        bad = _attr_errors(item)
        if bad:
            rejected.append({**base, "reason": "invalid_value", "field": bad})
            continue
        lat, lon = point
        pm = Point(to_m(lon, lat))
        # distance to the boundary: also right for a point inside a hole of the parcel
        outside = 0.0 if parcel_m.covers(pm) else parcel_m.boundary.distance(pm)
        if outside > GPS_TOLERANCE_M:
            rejected.append({**base, "reason": "outside_parcel", "distance_m": round(outside, 1)})
            continue
        auto = not code
        if auto:
            same = existing_grid.nearest(pm, SAME_SPOT_M)
            if same:
                # the same tree again (re-imported uncoded file, double tap): never a second plant
                rejected.append({"index": i, "reason": "duplicate_location", "other": same[0]})
                continue
            row, pos = item.get("rowNumber"), item.get("positionInRow")
            code = f"{code_prefix}{row}-{pos}" if row and pos else _next_free(code_prefix, taken | seen)
        if code in seen:
            rejected.append({"index": i, "plantCode": code, "reason": "duplicate_code"})
            continue
        if outside > 0:
            warnings.append({"index": i, "plantCode": code, "code": "near_boundary",
                             "distance_m": round(outside, 1)})
        near = request_grid.nearest(pm, MIN_SPACING_M) or existing_grid.nearest(pm, MIN_SPACING_M, skip=code)
        if near:
            warnings.append({"index": i, "plantCode": code, "code": "too_close",
                             "other": near[0], "distance_m": round(near[1], 2)})
        action = "update" if code in existing else "create"
        if action == "update":
            warnings.append({"index": i, "plantCode": code, "code": "exists"})
            old = existing_pts.get(code)
            if old is not None and old.distance(pm) > MOVED_M:
                warnings.append({"index": i, "plantCode": code, "code": "moved",
                                 "distance_m": round(old.distance(pm), 1)})
        seen.add(code)
        request_grid.add(code, pm)
        out = {"index": i, "id": plant_id(tenant, parcel_urn, code), "plantCode": code,
               "action": action, "auto": auto,
               "entity": _entity(tenant, parcel_urn, code, lat, lon, item, crop)}
        for f in _INT:
            if item.get(f) is not None:
                out[f] = item[f]
        accepted.append(out)
    return {"accepted": accepted, "rejected": rejected, "warnings": warnings}


# ---------------------------------------------------------------- files

def slug_code(name: str) -> str | None:
    """Deterministic plant code from a placemark/waypoint name ("Olivo 2" -> "Olivo-2")."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-.")
    s = re.sub(r"-{2,}", "-", s)[:64].rstrip("-.")
    return s or None


def _num(text: str) -> float:
    return float(text.replace(",", "."))  # decimal comma (Spanish spreadsheets)


def _csv_items(text: str) -> tuple[list[dict], list[dict]]:
    text = text.lstrip("﻿")
    header = text.split("\n", 1)[0]
    delimiter = ";" if header.count(";") > header.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    fields = {f.strip() for f in (reader.fieldnames or [])}
    if not {"lat", "lon"} <= fields:
        raise FileError("CSV needs lat and lon columns")
    if "plantCode" not in fields:
        raise FileError("CSV needs a plantCode column")
    items, rejected = [], []
    for i, raw in enumerate(reader):
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        code = row.get("plantCode") or None
        try:
            item: dict = {"lat": _num(row["lat"]), "lon": _num(row["lon"])}
        except (KeyError, ValueError):
            rejected.append({"index": i, "plantCode": code, "reason": "invalid_point"})
            continue
        if _valid_point(item) is None:
            rejected.append({"index": i, "plantCode": code, "reason": "invalid_point"})
            continue
        if code:
            item = {"plantCode": code, **item}
        for f in _TEXT + ("plantStatus", "datePlanted"):
            if row.get(f):
                item[f] = row[f]
        for f in _INT:
            if row.get(f):
                try:
                    item[f] = int(row[f])
                except ValueError:
                    item[f] = row[f]  # reported by prepare_plants as invalid_value
        for f in _NUMERIC:
            if row.get(f):
                try:
                    item[f] = _num(row[f])
                except ValueError:
                    item[f] = row[f]
        items.append(item)
    return items, rejected


def items_from_file(fmt: str, content: str) -> tuple[list[dict], list[dict]]:
    """Plant items and rejected entries from a file (CSV as text, others base64)."""
    if fmt not in FILE_FORMATS:
        raise FileError(f"unsupported format: {fmt}")
    if fmt == "csv":
        return _csv_items(content)
    from nkz_platform_sdk.gis import GeoFileError, read_geofile
    try:
        data = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FileError("content is not valid base64") from exc
    try:
        result = read_geofile(data, f"plants.{fmt}")
    except GeoFileError as exc:
        raise FileError(str(exc)) from exc
    items, rejected = [], []
    for i, f in enumerate(result.features):
        g = f.get("geometry") or {}
        props = f.get("properties") or {}
        raw = props.get("plantCode") or props.get("name")
        code = slug_code(raw) if raw is not None and str(raw).strip() else None
        if g.get("type") != "Point":
            rejected.append({"index": i, "plantCode": code, "reason": "invalid_point"})
            continue
        lon, lat = g["coordinates"][:2]
        item = {"lat": lat, "lon": lon}
        if code:
            item["plantCode"] = code
        items.append(item)
    for w in result.warnings:  # placemarks/features the reader left out
        rejected.append({"index": None, "reason": "invalid_point", "detail": w})
    return items, rejected
