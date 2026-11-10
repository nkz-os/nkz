"""Totals over a parcel's closed days. A total no day contributes to is None."""
from typing import Any, Dict, List, Optional

# Daily growing degree-day methods. A stage threshold is only valid with the
# method it was calibrated with, so callers name the method explicitly.
#   legacy      max(0, (min(Tmax, upper) + Tmin)/2 - base); the original formula.
#   m2          McMaster & Wilhelm (1997) Method 2: Tmax and Tmin are each set
#               to base when below it and to upper when above it, then averaged.
#   aquacrop_m3 AquaCrop GDDmethod 3: Tmax clipped to [base, upper], Tmin capped
#               at upper only, mean floored at base.
GDD_METHODS = ("legacy", "m2", "aquacrop_m3")


def _clip(v: float, lo: Optional[float], hi: Optional[float]) -> float:
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v


def daily_gdd(tmin: float, tmax: float, base: float, upper: Optional[float], method: str) -> float:
    if method == "legacy":
        return max(0.0, (_clip(tmax, None, upper) + tmin) / 2.0 - base)
    if method == "m2":
        return (_clip(tmax, base, upper) + _clip(tmin, base, upper)) / 2.0 - base
    if method == "aquacrop_m3":
        mean = (_clip(tmax, base, upper) + _clip(tmin, None, upper)) / 2.0
        return max(mean, base) - base
    raise ValueError(f"unknown gdd_method: {method}")


def _sum(days: List[Dict[str, Any]], field: str):
    vals = [d[field] for d in days if d.get(field) is not None]
    return (round(sum(vals), 2) if vals else None), len(vals)


def compute_totals(
    days: List[Dict[str, Any]],
    base_temp: Optional[float],
    upper_cutoff: Optional[float],
    gdd_method: str = "legacy",
) -> Dict[str, Any]:
    if gdd_method not in GDD_METHODS:
        raise ValueError(f"unknown gdd_method: {gdd_method}")
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
            gdd_vals.append(daily_gdd(tmin, tmax, base_temp, upper_cutoff, gdd_method))
        out["gdd"] = round(sum(gdd_vals), 2) if gdd_vals else None
        out["days_counted"]["gdd"] = len(gdd_vals)
    return out
