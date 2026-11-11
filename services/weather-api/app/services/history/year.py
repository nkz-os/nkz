"""One blended hourly year: cache get, else fetch grid + corrected, blend, cache put."""
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

import requests

from app.services.history import archive, blend, cache as cache_mod, fao56
from app.services.history.aggregate import latest_complete_day

logger = logging.getLogger(__name__)

CELL_DEG = 0.25                 # ERA5 grid spacing
CLOSE_MARGIN_DAYS = 100         # ERA5T preliminary data is replaced by final ERA5 after ~2-3 months


def snap_cell(lat: float, lon: float) -> tuple:
    """Nearest 0.25 deg multiple, ties toward +infinity (what the archive does), not banker's rounding."""
    return (math.floor(lat / CELL_DEG + 0.5) * CELL_DEG, math.floor(lon / CELL_DEG + 0.5) * CELL_DEG)


def _same_cell(doc: dict, cell_lat: float, cell_lon: float) -> bool:
    try:
        return abs(doc["cell_lat"] - cell_lat) <= 0.01 and abs(doc["cell_lon"] - cell_lon) <= 0.01
    except (KeyError, TypeError):
        return False


def _is_closed(year: int, latest: Optional[date], today: date) -> bool:
    end = date(year, 12, 31)
    return latest is not None and latest >= end and end < today - timedelta(days=CLOSE_MARGIN_DAYS)


def history_year(
    lat: float,
    lon: float,
    elev: float,
    year: int,
    cache=None,
    http_get: Callable = requests.get,
    timeout: float = archive.DEFAULT_TIMEOUT,
    today: Optional[date] = None,
) -> dict:
    """Blended hourly year (JSON-able dict): times (UTC epoch), t, td, rh, precip, rad, wind10.

    Closed years are served from the cache forever. An open (current / recent) year is
    served from the cache only on the day it was fetched, then refetched.
    """
    if elev is None:
        raise ValueError("parcel elevation is required")
    elev = float(round(elev))   # one elevation for the fetch, the cache key and the document
    today = today or datetime.now(timezone.utc).date()
    cache = cache if cache is not None else cache_mod.get_cache()
    cell_lat, cell_lon = snap_cell(lat, lon)
    key = cache_mod.cache_key(cell_lat, cell_lon, elev, year)

    doc = cache.get(key)
    if doc and _same_cell(doc, cell_lat, cell_lon) and (doc.get("closed") or doc.get("fetched_on") == today.isoformat()):
        return doc

    grid = archive.fetch_year(lat, lon, elev, year, "grid", http_get, timeout, today)
    corr = archive.fetch_year(lat, lon, elev, year, "corrected", http_get, timeout, today)
    cell_ok = abs(grid["lat"] - cell_lat) <= 0.01 and abs(grid["lon"] - cell_lon) <= 0.01
    if not cell_ok:
        logger.warning("ERA5 cell centre %s,%s differs from the snapped %s,%s: served, not cached",
                       grid["lat"], grid["lon"], cell_lat, cell_lon)
    tz = corr["tz"]
    gi = {ts: i for i, ts in enumerate(grid["times"])}
    times = corr["times"]
    pick = lambda name: [grid[name][gi[ts]] if ts in gi else None for ts in times]  # noqa: E731
    t_grid = pick("t")
    coastal = grid["grid_elevation"] is not None and grid["grid_elevation"] <= 0
    t = blend.blend_temperature(times, t_grid, corr["t"], tz, sea=coastal)
    td = corr["td"]
    rh = [None if (a is None or b is None) else fao56.rh_pct(a, b) for a, b in zip(t, td)]
    doc = {
        "schema": 1,
        "year": year,
        "cell_lat": grid["lat"],
        "cell_lon": grid["lon"],
        "elev": elev,
        "grid_elevation": grid["grid_elevation"],
        "coastal": coastal,
        "tz": tz,
        "closed": False,
        "fetched_on": today.isoformat(),
        "latest_available": None,
        "provenance": {
            "t_c": {"source": "era5", "altitude_method": "elevation_corrected_coastal" if coastal else "blend"},
            "td_c": {"source": "era5", "altitude_method": "elevation_corrected"},
            "rh_pct": {"source": "era5", "altitude_method": "derived_from_t_and_td"},
            "precip_mm": {"source": "era5", "altitude_method": "none"},
            "radiation_w_m2": {"source": "era5", "altitude_method": "none"},
            "wind2m_ms": {"source": "era5", "altitude_method": "none"},
        },
        "times": times,
        "t": t,
        "td": td,
        "rh": rh,
        "precip": pick("precip"),
        "rad": pick("rad"),
        "wind10": pick("wind10"),
    }
    latest = latest_complete_day(doc)
    closed = _is_closed(year, latest, today)
    doc["latest_available"] = latest.isoformat() if latest else None
    doc["closed"] = closed
    if cell_ok:
        cache.put(key, doc, closed)
    return doc
