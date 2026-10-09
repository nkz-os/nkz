"""Totals over a parcel's closed days. A total no day contributes to is None."""
from typing import Any, Dict, List, Optional


def _sum(days: List[Dict[str, Any]], field: str):
    vals = [d[field] for d in days if d.get(field) is not None]
    return (round(sum(vals), 2) if vals else None), len(vals)


def compute_totals(
    days: List[Dict[str, Any]],
    base_temp: Optional[float],
    upper_cutoff: Optional[float],
) -> Dict[str, Any]:
    precip, n_precip = _sum(days, "precip_mm")
    et0, n_et0 = _sum(days, "et0_mm")
    out: Dict[str, Any] = {
        "precip_mm": precip,
        "et0_mm": et0,
        "days_counted": {"precip_mm": n_precip, "et0_mm": n_et0},
    }
    if base_temp is not None:
        gdd_vals = []
        for d in days:
            tmin, tmax = d.get("tmin_c"), d.get("tmax_c")
            if tmin is None or tmax is None:
                continue
            if upper_cutoff is not None:
                tmax = min(tmax, upper_cutoff)
            gdd_vals.append(max(0.0, (tmax + tmin) / 2.0 - base_temp))
        out["gdd"] = round(sum(gdd_vals), 2) if gdd_vals else None
        out["days_counted"]["gdd"] = len(gdd_vals)
    return out
