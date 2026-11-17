"""Frost-risk Tmin: a conservative minimum for frost warnings, served beside ``tmin_c``.

Two fields, each calibrated per region and per source against station observations
(see ``data/frost_margins.json``):

* ``tmin_frost_risk_c``       event: observed Tmin <= 0 degC
* ``tmin_hard_frost_risk_c``  event: observed Tmin <= -2 degC

Value: ``tmin_c - m`` when ``tmin_c <= T_c``, otherwise ``tmin_c`` (warm nights untouched).
It is ``None`` (never a guess) when the region is not calibrated or failed its gate, the
parcel is off the mainland or in a coastal cell, the Tmin method changed since the fit, the
margins file is missing, or ``tmin_c`` is missing. ``tmin_c`` itself is never altered.
"""
import json
import logging
import math
import os
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

from app.services.history.method_version import TMIN_METHOD_VERSION

logger = logging.getLogger(__name__)

MARGINS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "frost_margins.json")
FIELDS = ("tmin_frost_risk_c", "tmin_hard_frost_risk_c")
SOURCES = ("history", "daily_closed")
SCHEMA_VERSION = 2

REASON_ISLAND = "sin validar (isla/fuera de península)"
REASON_COASTAL = "sin validar (celda costera)"
REASON_REGION = "sin validar (región no calibrada)"
REASON_NO_MARGINS = "sin validar (márgenes no disponibles)"
REASON_METHOD = "method_changed"
REASON_NONE_VALIDATED = "sin validar (ninguna región validada)"

# Off-mainland boxes (name, lat_min, lat_max, lon_min, lon_max), degrees. Copied as data from the
# calibration harness (validation/frostrisk/fit.py OFF_MAINLAND_BOXES): the same boxes excluded the
# stations from the fit, so the same boxes exclude the parcels here.
OFF_MAINLAND_BOXES = (
    ("canary_islands", 27.4, 29.6, -18.5, -13.2),
    ("balearic_islands", 38.5, 40.2, 1.0, 4.7),
    ("ceuta", 35.8, 36.0, -5.5, -5.2),
    ("melilla", 35.2, 35.4, -3.0, -2.8),
)

# Calibration assigned every station to the nearest P1 zone centroid of its country; the same
# rule picks the margin region of a parcel. Centroids copied as data from
# validation/frostrisk/regions.json ("centroids"): (lat, lon, country).
_CENTROIDS = {
    "east_coast": (38.732131, -0.655617, "ES"),
    "ebro_upper": (42.372291, -1.986425, "ES"),
    "interior_south": (38.173703, -3.885804, "ES"),
    "meseta_north": (41.851049, -5.191728, "ES"),
    "germany_east": (51.73582, 12.91062, "DE"),
    "germany_south": (49.150067, 10.974333, "DE"),
}

# ASSUMPTION: no country geometry exists in the repo or the calibration (stations carried their
# network's country). These are COARSE mainland outlines (lon, lat), drawn slightly inside the
# borders so that a border-line parcel gets null rather than another country's margin. Only
# Spain and Germany have P1 zones; any other point (Portugal, France, islands...) is not calibrated.
_COUNTRY_OUTLINES = {
    "ES": ((-9.2, 43.1), (-8.0, 43.7), (-4.0, 43.45), (-1.85, 43.3), (-1.4, 42.9), (0.7, 42.75),
           (3.1, 42.35), (3.2, 41.8), (2.2, 41.3), (0.9, 41.0), (0.2, 40.2), (-0.3, 39.4),
           (-0.1, 38.8), (-0.7, 37.9), (-1.6, 37.5), (-2.2, 36.75), (-4.5, 36.55), (-5.3, 36.15),
           (-6.3, 36.55), (-7.35, 37.2), (-7.3, 38.0), (-6.95, 38.25), (-7.1, 39.1), (-7.1, 39.65),
           (-6.9, 40.2), (-6.75, 41.0), (-6.25, 41.5), (-6.55, 41.9), (-8.1, 41.85), (-8.8, 41.95),
           (-9.1, 42.3)),
    "DE": ((7.1, 53.4), (8.1, 53.55), (8.7, 53.9), (8.7, 54.8), (9.8, 54.75), (11.0, 54.25),
           (13.4, 54.45), (14.1, 53.9), (14.2, 52.8), (14.8, 51.9), (14.7, 51.0), (14.2, 50.8),
           (12.2, 50.3), (12.6, 49.5), (13.7, 48.65), (13.0, 47.8), (12.0, 47.65), (10.4, 47.45),
           (9.5, 47.65), (7.6, 47.65), (7.65, 48.0), (8.2, 48.95), (7.0, 49.2), (6.4, 49.55),
           (6.3, 50.2), (6.1, 50.65), (6.05, 51.0), (6.2, 51.9), (7.0, 52.2), (7.1, 52.9)),
}


@lru_cache(maxsize=1)
def load_margins() -> Optional[dict]:
    """The margins file, or None when it is missing or unusable (the fields are then null)."""
    try:
        with open(MARGINS_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.error("frost margins unavailable: %s: %s", type(exc).__name__, exc)
        return None
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        logger.error("frost margins unavailable: unsupported schema")
        return None
    return doc


def any_validated(source: str, field: str) -> bool:
    """True if at least one region passed its gate for this source and field."""
    margins = load_margins()
    if margins is None:
        return False
    return any(
        (r.get(source, {}).get(field) or {}).get("status") == "validated"
        for r in margins.get("regions", {}).values()
    )


def off_mainland(lat: float, lon: float) -> Optional[str]:
    for name, la0, la1, lo0, lo1 in OFF_MAINLAND_BOXES:
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            return name
    return None


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _inside(lat: float, lon: float, outline) -> bool:
    """Ray casting on (lon, lat) vertices."""
    inside = False
    n = len(outline)
    for i in range(n):
        x1, y1 = outline[i]
        x2, y2 = outline[(i + 1) % n]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def country_of(lat: float, lon: float) -> Optional[str]:
    for country, outline in _COUNTRY_OUTLINES.items():
        if _inside(lat, lon, outline):
            return country
    return None


def margin_region(lat: float, lon: float) -> Optional[str]:
    """Margin region of a point: the nearest P1 zone centroid of its country (the calibration
    rule), or None when the point is in no calibrated country."""
    country = country_of(lat, lon)
    if country is None:
        return None
    return min(
        (r for r, c in _CENTROIDS.items() if c[2] == country),
        key=lambda r: _km(lat, lon, _CENTROIDS[r][0], _CENTROIDS[r][1]),
    )


def _null(field: str, source: str, status: str, reason: str, **extra: Any) -> Tuple[None, Dict[str, Any]]:
    prov = {"field": field, "source": source, "status": status, "reason": reason}
    prov.update(extra)
    return None, prov


def frost_risk_context(lat: float, lon: float) -> Tuple[Optional[str], Optional[str]]:
    """(margin region, off-mainland box) of a parcel."""
    box = off_mainland(lat, lon)
    return (None if box else margin_region(lat, lon)), box


def frost_risk_tmin(
    tmin_c: Optional[float],
    region: Optional[str],
    source: str,
    method_version: Optional[str],
    field: str = "tmin_frost_risk_c",
    *,
    off_mainland_box: Optional[str] = None,
    coastal: Optional[bool] = False,
) -> Tuple[Optional[float], Dict[str, Any]]:
    """(value or None, provenance) of one frost-risk field for one day.

    ``method_version`` is the Tmin method weather-api serves for ``source`` now; ``coastal`` is the
    history coastal rule (True: coastal; None: cell not determined, treated as not valid).
    The provenance does not depend on ``tmin_c``.
    """
    if field not in FIELDS or source not in SOURCES:
        raise ValueError(f"unknown field/source: {field}/{source}")
    margins = load_margins()
    if margins is None:
        return _null(field, source, "sin_validar", REASON_NO_MARGINS)
    if margins.get("method_version", {}).get(source) != method_version:
        return _null(field, source, "sin_validar", REASON_METHOD)
    if not any_validated(source, field):
        return _null(field, source, "sin_validar", REASON_NONE_VALIDATED)
    if off_mainland_box:
        return _null(field, source, "sin_validar", REASON_ISLAND)
    if region is None:
        return _null(field, source, "sin_validar", REASON_REGION)
    if coastal is None or coastal:
        return _null(field, source, "sin_validar", REASON_COASTAL, region=region)
    entry = margins.get("regions", {}).get(region, {}).get(source, {}).get(field)
    if not entry:
        return _null(field, source, "sin_validar", REASON_REGION, region=region)
    if entry.get("status") != "validated":
        return _null(field, source, entry.get("status") or "sin_validar",
                     entry.get("reason") or "sin validar", region=region,
                     threshold_c=entry.get("threshold_c"))
    m, t_c = entry["m"], entry["T_c"]
    prov = {
        "field": field, "source": source, "status": "validado", "region": region,
        "threshold_c": entry["threshold_c"], "m": m, "T_c": t_c, "form": entry.get("form"),
        "pod": entry.get("pod"), "pod_ci": entry.get("pod_ci"), "far": entry.get("far"),
        "n_frost": entry.get("n_frost"), "n_stations": entry.get("n_stations"),
        "n_clusters": entry.get("n_clusters"), "networks": entry.get("networks"),
        "label": entry.get("label"), "method_version": method_version,
    }
    if tmin_c is None or (isinstance(tmin_c, float) and math.isnan(tmin_c)):
        return None, prov
    return (round(tmin_c - m, 2) if tmin_c <= t_c else tmin_c), prov
