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
from datetime import date

from shapely.geometry import Point, shape
from shapely.ops import transform

GPS_TOLERANCE_M = 10.0     # outside the parcel by at most this: warning (consumer GPS error)
MIN_SPACING_M = 1.0        # two plants closer than this: warning
PLANT_STATUS = ("alive", "dead", "removed", "replaced")
_NUMERIC = ("height", "trunkPerimeter", "trunkPerimeterHeight", "canopyDiameter")
_TEXT = ("variety",)
_INT = ("rowNumber", "positionInRow")
_CODE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
FILE_FORMATS = ("gpx", "kml", "kmz", "geojson", "csv")


def _parcel_short(urn: str) -> str:
    return urn.rsplit(":", 1)[-1]


def plant_id(tenant: str, parcel_urn: str, code: str) -> str:
    return f"urn:ngsi-ld:AgriPlant:{tenant}:{_parcel_short(parcel_urn)}:{code}"


def _value(entity: dict | None, attr: str):
    v = (entity or {}).get(attr)
    if isinstance(v, dict):
        v = v.get("value", v.get("object"))
    return v


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


def _next_free(taken: set[str]) -> str:
    numbers = [int(c) for c in taken if c.isdigit()]
    return str((max(numbers) if numbers else 0) + 1)


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
        e["datePlanted"] = {"type": "Property", "value": {"@type": "Date", "@value": str(item["datePlanted"])}}
    if crop:
        e["hasAgriCrop"] = {"type": "Relationship", "object": crop["id"]}
        concept = _value(crop, "agroVocConcept")
        if concept:
            e["agroVocConcept"] = {"type": "Property", "value": concept}
    return e


def prepare_plants(tenant: str, parcel: dict, items: list[dict], existing: dict[str, dict],
                   crop: dict | None) -> dict:
    """Validate plants for one parcel. ``existing``: AgriPlant entities of the parcel by code."""
    parcel_urn = parcel["id"]
    geom = shape(_value(parcel, "location"))
    lat0 = geom.centroid.y
    to_m = _projector(lat0)
    parcel_m = transform(to_m, geom)

    accepted: list[dict] = []
    rejected: list[dict] = []
    warnings: list[dict] = []
    taken = set(existing)
    seen: dict[str, tuple[int, Point]] = {}

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
        outside = 0.0 if parcel_m.covers(pm) else parcel_m.exterior.distance(pm)
        if outside > GPS_TOLERANCE_M:
            rejected.append({**base, "reason": "outside_parcel", "distance_m": round(outside, 1)})
            continue
        if not code:
            row, pos = item.get("rowNumber"), item.get("positionInRow")
            code = f"{row}-{pos}" if row and pos else _next_free(taken | set(seen))
        if code in seen:
            rejected.append({"index": i, "plantCode": code, "reason": "duplicate_code"})
            continue
        if outside > 0:
            warnings.append({"index": i, "plantCode": code, "code": "near_boundary",
                             "distance_m": round(outside, 1)})
        for other, (_j, opm) in seen.items():
            d = pm.distance(opm)
            if d < MIN_SPACING_M:
                warnings.append({"index": i, "plantCode": code, "code": "too_close",
                                 "other": other, "distance_m": round(d, 2)})
                break
        action = "update" if code in existing else "create"
        if action == "update":
            warnings.append({"index": i, "plantCode": code, "code": "exists"})
        seen[code] = (i, pm)
        out = {"index": i, "id": plant_id(tenant, parcel_urn, code), "plantCode": code,
               "action": action, "entity": _entity(tenant, parcel_urn, code, lat, lon, item, crop)}
        for f in _INT:
            if item.get(f) is not None:
                out[f] = item[f]
        accepted.append(out)
    return {"accepted": accepted, "rejected": rejected, "warnings": warnings}


# ---------------------------------------------------------------- files

class FileError(ValueError):
    """The file cannot be read."""


def _csv_items(text: str) -> tuple[list[dict], list[dict]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames or not {"lat", "lon"} <= {f.strip() for f in reader.fieldnames}:
        raise FileError("CSV needs lat and lon columns")
    items, rejected = [], []
    for i, raw in enumerate(reader):
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        try:
            item: dict = {"lat": float(row["lat"]), "lon": float(row["lon"])}
        except (KeyError, ValueError):
            rejected.append({"index": i, "plantCode": row.get("plantCode") or None, "reason": "invalid_point"})
            continue
        if _valid_point(item) is None:
            rejected.append({"index": i, "plantCode": row.get("plantCode") or None, "reason": "invalid_point"})
            continue
        if row.get("plantCode"):
            item = {"plantCode": row["plantCode"], **item}
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
                    item[f] = float(row[f])
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
        code = props.get("plantCode") or props.get("name")
        code = str(code).strip() if code is not None and str(code).strip() else None
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
