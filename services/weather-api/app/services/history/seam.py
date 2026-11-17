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
UNVALIDATED = "unvalidated_region"
NO_SEAM_VARIABLE = "variable_without_seam"
DAILY_ONLY = "seam_is_daily"


@lru_cache(maxsize=1)
def load_seam() -> Optional[dict]:
    try:
        with open(SEAM_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        logger.error("seam data unavailable, /history seam block disabled: %s", type(exc).__name__)
        return None


def _entry(doc: dict, region: str, var: str) -> Optional[dict]:
    cells = (doc.get("regions", {}).get(region) or {}).get(var)
    if cells is None:
        return None
    season_cells = {s: cells[s] for s in SEASONS}
    season_cells["annual"] = cells["all"]
    return {"region": region, "season_cells": season_cells, "method": doc["method"], "window": doc["window"]}


def seam_for(lat: float, lon: float, var: str, resolution: str) -> Tuple[Optional[List[dict]], Optional[str]]:
    """(entries, None) or (None, reason). One entry per matching region; same bbox rule as validation_for."""
    doc = load_seam()
    if doc is None:
        return None, "seam_data_unavailable"
    if resolution != "daily":
        return None, DAILY_ONLY
    inside = [
        r["id"]
        for r in load_summary()["regions"]
        if r["bbox"][0] <= lat <= r["bbox"][1] and r["bbox"][2] <= lon <= r["bbox"][3]
    ]
    if not inside:
        return None, UNVALIDATED
    found: List[dict] = []
    for region in inside:
        if region in doc.get("not_judged_regions", {}):
            found.append({"region": region, "season_cells": None, "reason": doc["not_judged_regions"][region]})
            continue
        e = _entry(doc, region, var)
        if e is not None:
            found.append(e)
    if not found:
        return None, NO_SEAM_VARIABLE
    if all(e["season_cells"] is None for e in found):
        return None, NOT_JUDGED
    return found, None
