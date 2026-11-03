"""Read user-supplied geo files (KML, KMZ, GPX, GeoJSON) into GeoJSON features.

Coordinates are WGS84 longitude/latitude. Anything outside the valid range
(e.g. projected UTM metres) is skipped with a warning instead of being stored
in the wrong place. XML is parsed with defusedxml: DTDs and entities are
refused, so a crafted file cannot expand or fetch anything.
"""

from __future__ import annotations

import io
import json
import zipfile
import zlib
from dataclasses import dataclass, field
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

MAX_BYTES = 20 * 1024 * 1024
MAX_KMZ_UNCOMPRESSED = 50 * 1024 * 1024
MAX_FEATURES = 50_000


class GeoFileError(ValueError):
    """The file cannot be read as a supported geo file."""


@dataclass(frozen=True)
class GeoFileResult:
    features: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def read_geofile(data: bytes, filename: str) -> GeoFileResult:
    """Parse ``data`` according to the extension of ``filename``."""
    if len(data) > MAX_BYTES:
        raise GeoFileError(f"file larger than {MAX_BYTES} bytes")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "kmz":
        features, warnings = _read_kml(_kml_from_kmz(data))
    elif ext == "kml":
        features, warnings = _read_kml(data)
    elif ext == "gpx":
        features, warnings = _read_gpx(data)
    elif ext in ("geojson", "json"):
        features, warnings = _read_geojson(data)
    else:
        raise GeoFileError(f"unsupported file type: .{ext}")
    if len(features) > MAX_FEATURES:
        raise GeoFileError(f"more than {MAX_FEATURES} features")
    return _validate(features, warnings)


# --- helpers ---------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: Element, name: str) -> Element | None:
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _children(el: Element, name: str) -> list[Element]:
    return [c for c in el if _local(c.tag) == name]


def _text(el: Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def _parse_xml(data: bytes) -> Element:
    try:
        return fromstring(data, forbid_dtd=True)
    except (DefusedXmlException, ParseError) as exc:
        raise GeoFileError(f"invalid or unsafe XML: {exc.__class__.__name__}") from exc


def _kml_from_kmz(data: bytes) -> bytes:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise GeoFileError("not a KMZ (zip) archive") from exc
    kmls = [i for i in zf.infolist() if i.filename.lower().endswith(".kml")]
    if not kmls:
        raise GeoFileError("KMZ contains no .kml file")
    kmls.sort(key=lambda i: (i.filename.lower() != "doc.kml", i.filename))
    if kmls[0].file_size > MAX_KMZ_UNCOMPRESSED:
        raise GeoFileError("KMZ content too large")
    try:
        return zf.read(kmls[0])
    except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError, OSError) as exc:
        raise GeoFileError(f"cannot read KMZ content: {exc.__class__.__name__}") from exc


def _coords(text: str | None) -> list[list[float]]:
    out = []
    for tup in (text or "").split():
        parts = tup.split(",")
        if len(parts) < 2:
            raise GeoFileError(f"bad KML coordinate: {tup!r}")
        try:
            out.append([float(parts[0]), float(parts[1])])
        except ValueError as exc:
            raise GeoFileError(f"bad KML coordinate: {tup!r}") from exc
    return out


def _kml_polygon(poly: Element) -> list[list[list[float]]]:
    rings = []
    for boundary in ("outerBoundaryIs", "innerBoundaryIs"):
        for b in _children(poly, boundary):
            ring = _child(b, "LinearRing")
            if ring is not None:
                rings.append(_coords(_text(_child(ring, "coordinates"))))
    return rings


def _kml_geometry(pm: Element) -> dict | None:
    for el in pm:
        kind = _local(el.tag)
        if kind == "Point":
            pts = _coords(_text(_child(el, "coordinates")))
            return {"type": "Point", "coordinates": pts[0]} if pts else None
        if kind == "Polygon":
            return {"type": "Polygon", "coordinates": _kml_polygon(el)}
        if kind == "MultiGeometry":
            polys = [_kml_polygon(p) for p in _children(el, "Polygon")]
            if polys:
                return {"type": "MultiPolygon", "coordinates": polys}
            pts = _children(el, "Point")
            if pts:
                c = _coords(_text(_child(pts[0], "coordinates")))
                return {"type": "Point", "coordinates": c[0]} if c else None
    return None


def _kml_properties(pm: Element) -> dict:
    props: dict = {"name": _text(_child(pm, "name"))}
    ext = _child(pm, "ExtendedData")
    if ext is not None:
        for d in _children(ext, "Data"):
            if d.get("name"):
                props[d.get("name")] = _text(_child(d, "value"))
        for sd in ext.iter():
            if _local(sd.tag) == "SimpleData" and sd.get("name"):
                props[sd.get("name")] = _text(sd)
    return props


def _read_kml(data: bytes) -> tuple[list[dict], list[str]]:
    root = _parse_xml(data)
    features, warnings = [], []
    for i, pm in enumerate(el for el in root.iter() if _local(el.tag) == "Placemark"):
        geom = _kml_geometry(pm)
        props = _kml_properties(pm)
        if geom is None:
            warnings.append(f"placemark {props.get('name') or i + 1}: no point or polygon, skipped")
            continue
        features.append({"type": "Feature", "geometry": geom, "properties": props})
    return features, warnings


def _read_gpx(data: bytes) -> tuple[list[dict], list[str]]:
    root = _parse_xml(data)
    features, warnings = [], []
    for wpt in (el for el in root.iter() if _local(el.tag) == "wpt"):
        try:
            lon, lat = float(wpt.get("lon")), float(wpt.get("lat"))
        except (TypeError, ValueError) as exc:
            raise GeoFileError("waypoint without valid lat/lon") from exc
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"name": _text(_child(wpt, "name"))},
        })
    skipped = sum(1 for el in root.iter() if _local(el.tag) in ("trk", "rte"))
    if skipped:
        warnings.append(f"{skipped} track(s)/route(s) skipped: only waypoints are read")
    return features, warnings


def _read_geojson(data: bytes) -> tuple[list[dict], list[str]]:
    try:
        doc = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise GeoFileError("invalid GeoJSON") from exc
    if isinstance(doc, dict) and doc.get("type") == "FeatureCollection":
        raw = doc.get("features") or []
    elif isinstance(doc, dict) and doc.get("type") == "Feature":
        raw = [doc]
    elif isinstance(doc, dict) and "coordinates" in doc:
        raw = [{"type": "Feature", "geometry": doc, "properties": {}}]
    else:
        raise GeoFileError("GeoJSON is not a FeatureCollection, Feature or geometry")
    features = [
        {"type": "Feature", "geometry": f.get("geometry"), "properties": dict(f.get("properties") or {})}
        for f in raw if isinstance(f, dict) and isinstance(f.get("geometry"), dict)
    ]
    return features, []


def _positions(coords):
    if isinstance(coords, (list, tuple)) and coords and isinstance(coords[0], (int, float)):
        yield coords
    elif isinstance(coords, (list, tuple)):
        for c in coords:
            yield from _positions(c)


def _in_range(geometry: dict) -> bool:
    pts = list(_positions(geometry.get("coordinates")))
    return bool(pts) and all(
        len(p) >= 2 and -180 <= p[0] <= 180 and -90 <= p[1] <= 90 for p in pts
    )


def _validate(features: list[dict], warnings: list[str]) -> GeoFileResult:
    kept = []
    for i, f in enumerate(features):
        if _in_range(f["geometry"]):
            kept.append(f)
        else:
            label = f["properties"].get("name") or f["properties"].get("plantCode") or f"#{i + 1}"
            warnings.append(f"feature {label}: coordinates are not WGS84 lon/lat degrees, skipped")
    return GeoFileResult(features=kept, warnings=warnings)
