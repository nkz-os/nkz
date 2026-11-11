"""One hourly year: probe the cells, cache get, else fetch (grid + corrected blended, or corrected alone for a coastal parcel), cache put."""
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, Optional, Tuple

import requests

from app.services.history import archive, blend, cache as cache_mod, fao56
from app.services.history.aggregate import latest_complete_day
from app.services.history.deadline import Deadline, DeadlineExceeded

logger = logging.getLogger(__name__)

CLOSE_MARGIN_DAYS = 100         # ERA5T preliminary data is replaced by final ERA5 after ~2-3 months
CELL_TOL_DEG = 0.01
_PROBE_MEMO_MAX = 4096
# decimals stored per variable (a year object stays small: it is shared by every tenant)
_ROUND = {"t": 2, "td": 2, "rh": 1, "precip": 2, "rad": 1, "wind10": 2}


@dataclass(frozen=True)
class CellInfo:
    """The cells the archive answers with for one parcel: the grid request and the elevation-corrected one."""
    grid_lat: float
    grid_lon: float
    grid_elevation: Optional[float]
    corr_lat: float
    corr_lon: float

    @property
    def coastal(self) -> bool:
        """Corrected request lands in another cell than the grid one (or the grid cell is sea):
        every variable then comes from the corrected response, with no blend."""
        return (not _close(self.grid_lat, self.grid_lon, self.corr_lat, self.corr_lon)
                or (self.grid_elevation is not None and self.grid_elevation <= 0))


# (exact lat, exact lon, elevation) -> CellInfo; exact so a cell midpoint never merges two parcels. Failures are never memoised.
_CELL_PROBES: Dict[Tuple[float, float, float], CellInfo] = {}


def _close(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> bool:
    return abs(lat_a - lat_b) <= CELL_TOL_DEG and abs(lon_a - lon_b) <= CELL_TOL_DEG


def peek_cell(lat: float, lon: float, elev: float) -> Optional[CellInfo]:
    return _CELL_PROBES.get((float(lat), float(lon), float(round(elev))))


def resolve_cell(lat: float, lon: float, elev: float, http_get: Callable = requests.get,
                 deadline: Optional[Deadline] = None, timeout=archive.DEFAULT_TIMEOUT) -> CellInfo:
    """Learn the grid cell and the corrected cell with two 1-day probes; memoised per process."""
    elev = float(round(elev))
    memo_key = (float(lat), float(lon), elev)
    hit = _CELL_PROBES.get(memo_key)
    if hit is not None:
        return hit
    grid = archive.probe_cell(lat, lon, elev, "grid", http_get, deadline.timeout() if deadline else timeout)
    corr = archive.probe_cell(lat, lon, elev, "corrected", http_get, deadline.timeout() if deadline else timeout)
    info = CellInfo(grid["lat"], grid["lon"], grid["grid_elevation"], corr["lat"], corr["lon"])
    if len(_CELL_PROBES) >= _PROBE_MEMO_MAX:
        _CELL_PROBES.clear()
    _CELL_PROBES[memo_key] = info
    return info


def _same_cell(doc: dict, cell_lat: float, cell_lon: float) -> bool:
    try:
        return _close(doc["cell_lat"], doc["cell_lon"], cell_lat, cell_lon)
    except (KeyError, TypeError):
        return False


def _is_closed(year: int, latest: Optional[date], today: date) -> bool:
    end = date(year, 12, 31)
    return latest is not None and latest >= end and end < today - timedelta(days=CLOSE_MARGIN_DAYS)


def _rounded(values: list, name: str) -> list:
    nd = _ROUND[name]
    return [None if v is None else round(v, nd) for v in values]


def history_year(
    lat: float,
    lon: float,
    elev: float,
    year: int,
    cache=None,
    http_get: Callable = requests.get,
    timeout=archive.DEFAULT_TIMEOUT,
    today: Optional[date] = None,
    deadline: Optional[Deadline] = None,
) -> dict:
    """Hourly year (JSON-able dict): times (UTC epoch), t, td, rh, precip, rad, wind10.

    The cache key is the corrected cell (learned by memoised 1-day probes). A parcel whose
    corrected cell is not its grid cell is "coastal": every variable comes from the corrected
    response and it has its own key. Closed years are served from the cache forever; an open
    (current / recent) year only on the day it was fetched, then it is refetched. A cold year
    is not started when the deadline cannot cover it (DeadlineExceeded).
    """
    if elev is None:
        raise ValueError("parcel elevation is required")
    elev = float(round(elev))   # one elevation for the fetch, the cache key and the document
    today = today or datetime.now(timezone.utc).date()
    cache = cache if cache is not None else cache_mod.get_cache()
    cell = resolve_cell(lat, lon, elev, http_get, deadline, timeout)
    coastal = cell.coastal
    cell_lat, cell_lon = cell.corr_lat, cell.corr_lon
    key = cache_mod.cache_key(cell_lat, cell_lon, elev, year, coastal)

    doc = cache.get(key)
    if (doc and _same_cell(doc, cell_lat, cell_lon) and bool(doc.get("coastal")) == coastal
            and (doc.get("closed") or doc.get("fetched_on") == today.isoformat())):
        return doc

    if deadline is not None and not deadline.can_start_cold():
        raise DeadlineExceeded(f"year {year}")

    def fetch(variant: str) -> dict:
        if deadline is None:
            return archive.fetch_year(lat, lon, elev, year, variant, http_get, timeout, today)
        started = deadline.now()
        out = archive.fetch_year(lat, lon, elev, year, variant, http_get, deadline.timeout(), today)
        deadline.record(deadline.now() - started)
        return out

    corr = fetch("corrected")
    tz = corr["tz"]
    times = corr["times"]
    cell_ok = _close(corr["lat"], corr["lon"], cell_lat, cell_lon)
    if coastal:
        t = [v if v is not None and not math.isnan(v) else None for v in corr["t"]]
        precip, rad, wind10 = corr["precip"], corr["rad"], corr["wind10"]
        method = "elevation_corrected_coastal"
        rest = method
    else:
        grid = fetch("grid")
        cell_ok = cell_ok and _close(grid["lat"], grid["lon"], cell_lat, cell_lon)
        gi = {ts: i for i, ts in enumerate(grid["times"])}
        pick = lambda name: [grid[name][gi[ts]] if ts in gi else None for ts in times]  # noqa: E731
        t = blend.blend_temperature(times, pick("t"), corr["t"], tz, sea=False)
        precip, rad, wind10 = pick("precip"), pick("rad"), pick("wind10")
        method, rest = "blend", "none"
    if not cell_ok:
        logger.warning("ERA5 cell of the full fetch differs from the probed cell: served, not cached")
    td = corr["td"]
    rh = [None if (a is None or b is None) else fao56.rh_pct(a, b) for a, b in zip(t, td)]
    doc = {
        "schema": 1,
        "year": year,
        "cell_lat": cell_lat,
        "cell_lon": cell_lon,
        "elev": elev,
        "grid_elevation": cell.grid_elevation,
        "coastal": coastal,
        "tz": tz,
        "closed": False,
        "fetched_on": today.isoformat(),
        "latest_available": None,
        "provenance": {
            "t_c": {"source": "era5", "altitude_method": method},
            "td_c": {"source": "era5", "altitude_method": "elevation_corrected_coastal" if coastal else "elevation_corrected"},
            "rh_pct": {"source": "era5", "altitude_method": "derived_from_t_and_td"},
            "precip_mm": {"source": "era5", "altitude_method": rest},
            "radiation_w_m2": {"source": "era5", "altitude_method": rest},
            "wind2m_ms": {"source": "era5", "altitude_method": rest},
        },
        "times": times,
        "t": _rounded(t, "t"),
        "td": _rounded(td, "td"),
        "rh": _rounded(rh, "rh"),
        "precip": _rounded(precip, "precip"),
        "rad": _rounded(rad, "rad"),
        "wind10": _rounded(wind10, "wind10"),
    }
    latest = latest_complete_day(doc)
    closed = _is_closed(year, latest, today)
    doc["latest_available"] = latest.isoformat() if latest else None
    doc["closed"] = closed
    if cell_ok:
        cache.put(key, doc, closed)
    return doc
