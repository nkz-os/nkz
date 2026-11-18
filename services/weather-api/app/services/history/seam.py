"""Measured seam between stored /daily closed days and /history (ERA5), from the bundled public summary.

Measurement only: no correction is applied anywhere. Regions are resolved exactly as `validation` does
(bbox containment against validation_summary.json), so a point covered by AEMET and SIAR regions gets
one entry per region. A missing or unreadable data file yields no seam block (None), never an error.
"""
import json
import logging
import os
from functools import lru_cache
from typing import List, Optional, Tuple

from app.services.history.validation import load_summary

logger = logging.getLogger(__name__)

SEAM_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "seam_public.json")
SEASONS = ("DJF", "MAM", "JJA", "SON")
NOT_JUDGED = "sin_juzgar_region"
UNVALIDATED = "region_no_validada"
NO_SEAM_VARIABLE = "variable_sin_medida"
DAILY_ONLY = "solo_diario"
DATA_UNAVAILABLE = "datos_no_disponibles"


@lru_cache(maxsize=1)
def load_seam() -> Optional[dict]:
    try:
        with open(SEAM_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        logger.error("seam data unavailable, /history seam block disabled: %s", type(exc).__name__)
        return None


def _cells(doc: dict, region: str, var: str) -> Optional[dict]:
    cells = (doc.get("regions", {}).get(region) or {}).get(var)
    if cells is None:
        return None
    out = {s: cells[s] for s in SEASONS}
    out["annual"] = cells["all"]
    return out


def _none(status: str, reason: str) -> dict:
    return {"status": status, "reason": reason, "region": None, "season_cells": None, "method": None, "window": None}


def seam_for(lat: float, lon: float, var: str, resolution: str) -> dict:
    """Always an object {status, reason, region, season_cells, method, window}.

    status: medido | sin_juzgar | no_disponible. Regions use the same bbox rule as validation_for; where
    several regions match (AEMET + SIAR), `region` is the list of their ids and `season_cells` is keyed by
    region id. Nothing measured -> region/season_cells/method/window are null.
    """
    doc = load_seam()
    if doc is None:
        return _none("no_disponible", DATA_UNAVAILABLE)
    if resolution != "daily":
        return _none("no_disponible", DAILY_ONLY)
    inside = [
        r["id"]
        for r in load_summary()["regions"]
        if r["bbox"][0] <= lat <= r["bbox"][1] and r["bbox"][2] <= lon <= r["bbox"][3]
    ]
    if not inside:
        return _none("no_disponible", UNVALIDATED)
    judged = [r for r in inside if r not in doc.get("not_judged_regions", {})]
    if not judged:
        return _none("sin_juzgar", NOT_JUDGED)
    found = {r: c for r in judged if (c := _cells(doc, r, var)) is not None}
    if not found:
        return _none("no_disponible", NO_SEAM_VARIABLE)
    return {"status": "medido", "reason": None, "region": list(found), "season_cells": found,
            "method": doc["method"], "window": doc["window"]}
