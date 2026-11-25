"""Geometry read-through cache over observation_geometry_cache (contract 1.4).

The table is FORCE RLS: ``cur`` must come from ``tenant_cursor`` (tenant already set);
every statement also filters tenant_id explicitly. The caller commits.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional, Tuple

import requests

import urn_resolution
from common.ngsi_read_guard import valid_entity_path

logger = logging.getLogger(__name__)

_GEOJSON_TYPES = frozenset(
    {"Point", "Polygon", "MultiPolygon", "LineString", "MultiPoint", "MultiLineString"}
)

_SELECT_SQL = """
SELECT ST_AsGeoJSON(geom::geometry)::json AS geojson,
       (updated_at > now() - interval '1 hour') AS fresh
FROM observation_geometry_cache
WHERE tenant_id = %s AND entity_urn = %s
"""
_UPSERT_SQL = """
INSERT INTO observation_geometry_cache (tenant_id, entity_urn, entity_type, geom, updated_at)
VALUES (%s, %s, %s, ST_GeomFromGeoJSON(%s)::geography, now())
ON CONFLICT (tenant_id, entity_urn) DO UPDATE
SET geom = EXCLUDED.geom, entity_type = EXCLUDED.entity_type, updated_at = now()
"""
_DELETE_SQL = "DELETE FROM observation_geometry_cache WHERE tenant_id = %s AND entity_urn = %s"

FetchResult = Tuple[str, Optional[Dict[str, Any]]]


def fetch_orion_entity_status(tenant_id: str, entity_id: str) -> FetchResult:
    """('ok', entity) | ('not_found', None) | ('error', None).

    Unlike urn_resolution.fetch_orion_entity this distinguishes a real 404 from an
    unreachable or failing Orion: only a 404 may delete a cached geometry.
    """
    if not urn_resolution.ORION_URL or not entity_id or not valid_entity_path(entity_id):
        return "error", None
    url = f"{urn_resolution.ORION_URL}/ngsi-ld/v1/entities/{entity_id}"
    try:
        r = requests.get(url, headers=urn_resolution._orion_headers(tenant_id), timeout=10)
    except Exception as e:
        logger.warning("Orion request failed for %s: %s", entity_id, e)
        return "error", None
    if r.status_code == 404:
        return "not_found", None
    if r.status_code != 200:
        logger.warning("Orion answered %s for %s", r.status_code, entity_id)
        return "error", None
    try:
        body = r.json()
    except ValueError:
        return "error", None
    return ("ok", body) if isinstance(body, dict) else ("error", None)


def _cached_geojson(value: Any) -> Optional[dict]:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else None


def _location(entity: Dict[str, Any]) -> Optional[dict]:
    loc = entity.get("location")
    geo = loc.get("value") if isinstance(loc, dict) else None
    if (isinstance(geo, dict) and geo.get("type") in _GEOJSON_TYPES
            and isinstance(geo.get("coordinates"), list) and geo["coordinates"]):
        return geo
    return None


def geometry_of(cur, tenant_id: str, urn: str,
                fetch: Callable[[str, str], FetchResult] = fetch_orion_entity_status
                ) -> Optional[dict]:
    """GeoJSON of ``urn``: fresh cache row, else read-through from Orion.

    Orion 200 + location -> upsert and return. 404 (or no location) -> delete the row,
    return None. Orion unreachable/failing -> the stale cached geometry if any, else None.
    """
    cur.execute(_SELECT_SQL, (tenant_id, urn))
    row = cur.fetchone()
    cached = _cached_geojson(row["geojson"]) if row else None
    if cached is not None and row.get("fresh"):
        return cached

    status, entity = fetch(tenant_id, urn)
    if status == "ok" and entity is not None:
        geo = _location(entity)
        etype = entity.get("type")
        if geo is not None and isinstance(etype, str) and etype:
            cur.execute(_UPSERT_SQL, (tenant_id, urn, etype, json.dumps(geo)))
            return geo
        if entity.get("location") is None:
            cur.execute(_DELETE_SQL, (tenant_id, urn))
            return None
        logger.warning("geometry invalid in Orion: tenant_id=%s entity_urn=%s", tenant_id, urn)
    elif status == "not_found":
        cur.execute(_DELETE_SQL, (tenant_id, urn))
        return None
    else:
        logger.warning("Orion unavailable for geometry: tenant_id=%s entity_urn=%s stale=%s",
                       tenant_id, urn, cached is not None)
    return cached
