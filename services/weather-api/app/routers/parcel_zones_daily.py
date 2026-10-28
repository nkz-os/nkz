"""
GET /api/weather/parcel/{parcel_id}/zones/daily?start=YYYY-MM-DD&end=YYYY-MM-DD

Per-zone daily series (tMin, tMax, eto) of the parcel's ``AgriParcelZone`` entities,
persisted to ``telemetry_events`` by the telemetry bridge. One value per zone and UTC
day (the latest observation of the day wins). Rows without ``tMin`` (zones published
by other modules, e.g. terrain classes) are not part of the series. Nothing is
interpolated. A span over 400 days is clipped to its last 400 and flagged
``"truncated": true``.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from psycopg2.extras import RealDictCursor

from app.auth import require_auth
from app.deps import get_db_connection
from app.routers.parcel_daily import (
    MAX_RANGE_DAYS,
    _error,
    _escape_like,
    _fetch_parcel_entity,
    _num,
    _parse_day,
)
from app.routers.parcels import _normalize_parcel_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["parcels"])

_ATTRS = ("tMin", "tMax", "eto")


@router.get("/parcel/{parcel_id}/zones/daily")
def get_parcel_zones_daily(
    parcel_id: str,
    start: str = Query(..., description="First day, YYYY-MM-DD"),
    end: str = Query(..., description="Last day (inclusive), YYYY-MM-DD"),
    tenant_id: str = Depends(require_auth),
):
    d0, d1 = _parse_day(start), _parse_day(end)
    if d0 is None or d1 is None:
        return _error("start and end must be valid YYYY-MM-DD dates", 400)
    if d1 < d0:
        return _error("end must not be before start", 400)
    truncated = (d1 - d0).days + 1 > MAX_RANGE_DAYS
    if truncated:
        d0 = d1 - timedelta(days=MAX_RANGE_DAYS - 1)

    parcel_urn = _normalize_parcel_id(parcel_id)
    if _fetch_parcel_entity(parcel_urn, tenant_id) is None:
        return _error("parcel not found", 404)
    bare = parcel_urn.split(":")[-1]
    pattern = f"urn:ngsi-ld:AgriParcelZone:{_escape_like(tenant_id)}:{_escape_like(bare)}:%"

    lo = datetime(d0.year, d0.month, d0.day, tzinfo=timezone.utc)
    hi = datetime(d1.year, d1.month, d1.day, tzinfo=timezone.utc) + timedelta(days=1)

    conn = None
    try:
        conn = get_db_connection(tenant_id)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT DISTINCT ON (zone, day)
                   COALESCE(payload #>> '{measurements,zoneId}', split_part(entity_id, ':', -1)) AS zone,
                   (observed_at AT TIME ZONE 'UTC')::date AS day,
                   observed_at,
                   payload->'measurements' AS measurements
            FROM telemetry_events
            WHERE tenant_id = %s
              AND entity_type = 'AgriParcelZone'
              AND entity_id LIKE %s ESCAPE '\\'
              AND observed_at >= %s AND observed_at < %s
              AND payload #>> '{measurements,tMin}' IS NOT NULL
            ORDER BY zone, day, observed_at DESC
            """,
            (tenant_id, pattern, lo, hi),
        )
        rows = cur.fetchall()
    except Exception as e:
        logger.error("zone daily query failed tenant=%s parcel=%s: %s", tenant_id, parcel_urn, e)
        return _error("zone daily series unavailable", 503)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # (zone, date) -> (observed_at, record); the greatest observed_at wins.
    best: Dict[tuple, tuple] = {}
    for row in rows:
        ts = row["observed_at"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts = ts.astimezone(timezone.utc)
        m = row.get("measurements") or {}
        if isinstance(m, str):
            try:
                m = json.loads(m)
            except json.JSONDecodeError:
                m = {}
        if m.get("tMin") is None:  # the SQL filter already drops these; keep the series honest if it ever doesn't
            continue
        key = (str(row["zone"]), ts.strftime("%Y-%m-%d"))
        if key in best and best[key][0] >= ts:
            continue
        rec: Dict[str, Any] = {"date": key[1]}
        for a in _ATTRS:
            rec[a] = _num(m.get(a))
        best[key] = (ts, rec)

    zones: Dict[str, list] = {}
    dates = set()
    for (zone, day), (_, rec) in sorted(best.items()):
        zones.setdefault(zone, []).append(rec)
        dates.add(day)

    out: Dict[str, Any] = {"parcel_id": parcel_urn, "days": len(dates), "zones": zones}
    if truncated:
        out["truncated"] = True
    return out
