"""Hourly altitude blend of the grid-cell and elevation-corrected temperature series.

Per parcel-local civil day: w = (T_grid - min_day) / (max_day - min_day) of the grid series
(0.5 on a flat day); T = T_grid + w * (T_corr - T_grid). The coldest hour keeps the grid
value, the hottest takes the corrected one. min/max use the hours where the grid has a value.
An hour missing in either series stays None. sea=True (grid-cell elevation <= 0): the
corrected series at every hour, the grid cell is a sea cell.
"""
import math
from typing import List, Optional, Sequence

from app.services.history.aggregate import local_date


def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def blend_temperature(
    times: Sequence[int],
    t_grid: Sequence[Optional[float]],
    t_corr: Sequence[Optional[float]],
    tz: str,
    sea: bool = False,
) -> List[Optional[float]]:
    if sea:
        return [v if _ok(v) else None for v in t_corr]
    days = [local_date(ts, tz) for ts in times]
    lo: dict = {}
    hi: dict = {}
    for d, v in zip(days, t_grid):
        if not _ok(v):
            continue
        lo[d] = v if d not in lo else min(lo[d], v)
        hi[d] = v if d not in hi else max(hi[d], v)
    out: List[Optional[float]] = []
    for d, tg, tc in zip(days, t_grid, t_corr):
        if not (_ok(tg) and _ok(tc)):
            out.append(None)
            continue
        span = hi[d] - lo[d]
        w = 0.5 if span == 0 else (tg - lo[d]) / span
        out.append(tg + w * (tc - tg))
    return out
