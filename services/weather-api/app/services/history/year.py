"""One blended hourly year: cache get, else fetch grid + corrected, blend, cache put."""
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

import requests

from app.services.history import archive, blend, cache as cache_mod, fao56
from app.services.history.aggregate import local_date

logger = logging.getLogger(__name__)

CELL_DEG = 0.25                 # ERA5 grid spacing
CLOSE_MARGIN_DAYS = 7           # a year ending within this margin of "now" can still change


def snap_cell(lat: float, lon: float) -> tuple:
    return round(lat / CELL_DEG) * CELL_DEG, round(lon / CELL_DEG) * CELL_DEG


def _latest_complete_day(times, t, tz) -> Optional[date]:
    """Last local day whose final hour has a grid temperature."""
    for i in range(len(times) - 1, -1, -1):
        if t[i] is None:
            continue
        d = local_date(times[i], tz)
        if local_date(times[i] + 3600, tz) != d:
            return d
        return d - timedelta(days=1)
    return None


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
    today = today or datetime.now(timezone.utc).date()
    cache = cache if cache is not None else cache_mod.get_cache()
    cell_lat, cell_lon = snap_cell(lat, lon)
    key = cache_mod.cache_key(cell_lat, cell_lon, elev, year)

    doc = cache.get(key)
    if doc and (doc.get("closed") or doc.get("fetched_on") == today.isoformat()):
        return doc

    grid = archive.fetch_year(lat, lon, elev, year, "grid", http_get, timeout, today)
    corr = archive.fetch_year(lat, lon, elev, year, "corrected", http_get, timeout, today)
    if abs(grid["lat"] - cell_lat) > 0.01 or abs(grid["lon"] - cell_lon) > 0.01:
        logger.warning("ERA5 cell centre %s,%s differs from the snapped %s,%s", grid["lat"], grid["lon"], cell_lat, cell_lon)
    tz = corr["tz"]
    gi = {ts: i for i, ts in enumerate(grid["times"])}
    times = corr["times"]
    pick = lambda name: [grid[name][gi[ts]] if ts in gi else None for ts in times]  # noqa: E731
    t_grid = pick("t")
    coastal = grid["grid_elevation"] is not None and grid["grid_elevation"] <= 0
    t = blend.blend_temperature(times, t_grid, corr["t"], tz, sea=coastal)
    td = corr["td"]
    rh = [None if (a is None or b is None) else fao56.rh_pct(a, b) for a, b in zip(t, td)]
    latest = _latest_complete_day(times, t_grid if not coastal else corr["t"], tz)
    closed = _is_closed(year, latest, today)
    doc = {
        "schema": 1,
        "year": year,
        "lat": lat,
        "lon": lon,
        "cell_lat": grid["lat"],
        "cell_lon": grid["lon"],
        "elev": elev,
        "grid_elevation": grid["grid_elevation"],
        "coastal": coastal,
        "tz": tz,
        "closed": closed,
        "fetched_on": today.isoformat(),
        "latest_available": latest.isoformat() if latest else None,
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
    cache.put(key, doc, closed)
    return doc
