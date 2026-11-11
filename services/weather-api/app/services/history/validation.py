"""Validation statistics lookup from the bundled summary (never another region's numbers)."""
import json
import os
from functools import lru_cache
from typing import List, Union

SUMMARY_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "validation_summary.json")
UNVALIDATED = "unvalidated_region"
# Variables validated at hourly resolution; every other variable's rows are daily.
_HOURLY_VARS = frozenset({"t_c", "td_c", "rh_pct"})


@lru_cache(maxsize=1)
def load_summary() -> dict:
    with open(SUMMARY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _resolution_of(entry: dict) -> str:
    return entry.get("resolution") or ("hourly" if entry["variable"] in _HOURLY_VARS else "daily")


def validation_for(lat: float, lon: float, var: str, resolution: str) -> Union[List[dict], str]:
    """Entries of the regions whose bbox contains the point, for this variable/resolution.

    Each region is one network (AEMET / DWD, or SIAR), so a point covered by both returns
    both; each entry carries its period. No match -> "unvalidated_region".
    """
    summary = load_summary()
    inside = {
        r["id"]
        for r in summary["regions"]
        if r["bbox"][0] <= lat <= r["bbox"][1] and r["bbox"][2] <= lon <= r["bbox"][3]
    }
    found = [
        dict(e)
        for e in summary["entries"]
        if e["region"] in inside and e["variable"] == var and _resolution_of(e) == resolution
    ]
    return found or UNVALIDATED
