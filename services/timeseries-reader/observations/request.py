"""Request parsing and validation for the observation read API (contract 2.1)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.parser import isoparse

RESOLUTIONS = frozenset({"raw", "PT15M", "PT1H", "P1D"})
MODES = frozenset({"series", "latest"})
TIERS = frozenset({"all", "observed"})
SUB_CONTEXT_NUMERIC = frozenset({"depthFrom", "depthTo", "height"})
SUB_CONTEXT_KEYS = SUB_CONTEXT_NUMERIC | {"organ"}


class RequestError(Exception):
    """A client error with the HTTP status to answer (400 / 404 / 413 / 422)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class BestRequest:
    feature: Optional[str]
    geometry: Optional[dict]
    property: str
    start: datetime
    end: datetime
    mode: str
    resolution: str
    tz: str
    medium: Optional[str]
    sub_context: dict = field(default_factory=dict)
    tiers: str = "all"
    allow_medium_fallback: bool = False


def iso_z(dt: datetime) -> str:
    """UTC ISO-8601 with a trailing Z, e.g. 2026-11-01T00:00:00Z."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(body: Dict[str, Any], key: str) -> datetime:
    raw = body.get(key)
    if not isinstance(raw, str) or not raw:
        raise RequestError(400, f"{key} is required (ISO-8601)")
    try:
        dt = isoparse(raw)
    except (ValueError, OverflowError):
        raise RequestError(400, f"{key} is not a valid ISO-8601 datetime")
    if dt.tzinfo is None:
        raise RequestError(400, f"{key} must carry a timezone (use Z for UTC)")
    return dt.astimezone(timezone.utc)


def _num(v: Any) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(float(v))  # float() overflows on huge ints
    except (OverflowError, ValueError, TypeError):
        return False


def _position_ok(pos: Any) -> bool:
    if not isinstance(pos, (list, tuple)) or len(pos) not in (2, 3):
        return False
    if not all(_num(c) for c in pos):
        return False
    return -180 <= pos[0] <= 180 and -90 <= pos[1] <= 90


def _polygon_ok(rings: Any) -> bool:
    if not isinstance(rings, list) or not rings:
        return False
    for ring in rings:
        if not isinstance(ring, list) or len(ring) < 4:
            return False
        if not all(_position_ok(p) for p in ring):
            return False
        if list(ring[0]) != list(ring[-1]):
            return False
    return True


def _geometry(geo: Any) -> dict:
    if not isinstance(geo, dict) or geo.get("type") not in ("Polygon", "MultiPolygon"):
        raise RequestError(400, "geometry must be a GeoJSON Polygon or MultiPolygon")
    coords = geo.get("coordinates")
    if geo["type"] == "Polygon":
        ok = _polygon_ok(coords)
    else:
        ok = isinstance(coords, list) and bool(coords) and all(_polygon_ok(p) for p in coords)
    if not ok:
        raise RequestError(400, "invalid geometry")
    return geo


def _sub_context(raw: Any) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RequestError(400, "subContext must be an object")
    for k, v in raw.items():
        if k not in SUB_CONTEXT_KEYS:
            raise RequestError(400, f"unknown subContext key: {k}")
        if k in SUB_CONTEXT_NUMERIC:
            if not _num(v):
                raise RequestError(400, f"subContext.{k} must be a finite number")
        elif not isinstance(v, str) or not v:
            raise RequestError(400, f"subContext.{k} must be a non-empty string")
    return dict(raw)


def parse_best(body: Dict[str, Any]) -> BestRequest:
    if not isinstance(body, dict):
        raise RequestError(400, "request body must be a JSON object")
    feature = body.get("feature") or None
    geometry = body.get("geometry") or None
    if (feature is None) == (geometry is None):
        raise RequestError(400, "exactly one of feature or geometry is required")
    if feature is not None and not isinstance(feature, str):
        raise RequestError(400, "feature must be a URN string")
    if geometry is not None:
        geometry = _geometry(geometry)

    prop = body.get("property")
    if not isinstance(prop, str) or not prop:
        raise RequestError(400, "property is required")

    start, end = _parse_dt(body, "from"), _parse_dt(body, "to")
    if start >= end:
        raise RequestError(400, "from must be before to")

    mode = body.get("mode") or "series"
    if mode not in MODES:
        raise RequestError(400, "unknown mode")
    resolution = body.get("resolution") or "PT1H"
    if resolution not in RESOLUTIONS:
        raise RequestError(400, "unknown resolution")
    tz = body.get("tz") or "UTC"
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        raise RequestError(400, "unknown tz")
    tiers = body.get("tiers") or "all"
    if tiers not in TIERS:
        raise RequestError(400, "unknown tiers")
    medium = body.get("medium") or None
    if medium is not None and not isinstance(medium, str):
        raise RequestError(400, "medium must be a string")
    fallback = body.get("allowMediumFallback", False)
    if fallback is None:
        fallback = False
    if not isinstance(fallback, bool):
        raise RequestError(400, "allowMediumFallback must be a boolean")

    return BestRequest(
        feature=feature, geometry=geometry, property=prop, start=start, end=end,
        mode=mode, resolution=resolution, tz=tz, medium=medium,
        sub_context=_sub_context(body.get("subContext")), tiers=tiers,
        allow_medium_fallback=fallback,
    )
