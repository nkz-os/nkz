"""Validation statistics lookup from the bundled summary (never another region's numbers)."""
import json
import os
from functools import lru_cache
from typing import List, Union

SUMMARY_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "validation_summary.json")
UNVALIDATED = "unvalidated_region"
# Variables validated at hourly resolution; every other variable's rows are daily.
_HOURLY_VARS = frozenset({"t_c", "td_c", "rh_pct"})
# Altitude method actually served per variable (see year.py provenance).
_SERVED_METHOD = {
    "t_c": "blend", "tmin_c": "blend", "tmax_c": "blend", "tmean_c": "blend",
    "td_c": "om_elevation", "rh_pct": "om_elevation", "rh_mean_pct": "om_elevation",
    "vapour_pressure_kpa": "om_elevation",
    "precip_mm": "none", "radiation_mj_m2": "none", "radiation_w_m2": "none", "wind2m_ms": "none",
    "et0_mm": "none",
}
# Served from blended T but validated on another T treatment (rh rows: corrected T; ET0 rows: grid T).
_T_METHOD_DIFFERS = frozenset({"rh_pct", "rh_mean_pct", "et0_mm"})


@lru_cache(maxsize=1)
def load_summary() -> dict:
    with open(SUMMARY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _resolution_of(entry: dict) -> str:
    return entry.get("resolution") or ("hourly" if entry["variable"] in _HOURLY_VARS else "daily")


def validation_for(lat: float, lon: float, var: str, resolution: str, coastal: bool = False) -> Union[List[dict], str]:
    """Entries of the regions whose bbox contains the point, for this variable/resolution.

    Each region is one network (AEMET / DWD, or SIAR), so a point covered by both returns
    both; each entry carries its period. No match -> "unvalidated_region". A coastal parcel is
    served from the elevation-corrected response for every variable: only om_elevation rows apply.
    """
    summary = load_summary()
    inside = {
        r["id"]
        for r in summary["regions"]
        if r["bbox"][0] <= lat <= r["bbox"][1] and r["bbox"][2] <= lon <= r["bbox"][3]
    }
    method = "om_elevation" if coastal else _SERVED_METHOD.get(var)
    found = [
        dict(e)
        for e in summary["entries"]
        if e["region"] in inside and e["variable"] == var and _resolution_of(e) == resolution
        and (method is None or e["altitude_method"] == method)
    ]
    if var in _T_METHOD_DIFFERS:
        for e in found:
            e["t_method_differs"] = True
    return found or UNVALIDATED
