"""observation_geometry_cache maintenance (contract 1.4 / migration 117).

The table is FORCE RLS: every statement runs in a transaction that first sets
app.current_tenant, and also filters tenant_id explicitly. Invalid or missing
geometry is skipped with a warning and never raised to the caller.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

_GEOJSON_TYPES = frozenset(
    {"Point", "Polygon", "MultiPolygon", "LineString", "MultiPoint", "MultiLineString"}
)

_SET_TENANT_SQL = "SELECT set_config('app.current_tenant', $1, true)"

_UPSERT_SQL = """
INSERT INTO observation_geometry_cache (tenant_id, entity_urn, entity_type, geom, updated_at)
VALUES ($1, $2, $3, ST_GeomFromGeoJSON($4)::geography, now())
ON CONFLICT (tenant_id, entity_urn) DO UPDATE
SET geom = EXCLUDED.geom,
    entity_type = EXCLUDED.entity_type,
    updated_at = now()
"""

_DELETE_SQL = "DELETE FROM observation_geometry_cache WHERE tenant_id = $1 AND entity_urn = $2"

_CACHED_URNS_SQL = """
SELECT entity_urn FROM observation_geometry_cache
WHERE tenant_id = $1 AND entity_type = $2 AND updated_at < $3
"""


def _location_geojson(entity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """GeoJSON from a normalized `location` GeoProperty, or None when absent/invalid."""
    loc = entity.get("location")
    if not isinstance(loc, dict):
        return None
    geo = loc.get("value")
    if not isinstance(geo, dict):
        return None
    if geo.get("type") not in _GEOJSON_TYPES:
        return None
    coords = geo.get("coordinates")
    if not isinstance(coords, list) or not coords:
        return None
    return geo


async def upsert_geometry(pool, tenant_id: Optional[str], entity: Dict[str, Any]) -> bool:
    """Upsert the entity's location. True when a row was written, False when skipped."""
    urn = entity.get("id")
    etype = entity.get("type")
    if not tenant_id or not isinstance(urn, str) or not urn or not isinstance(etype, str) or not etype:
        logger.warning(
            "geometry skipped, tenant/id/type missing: tenant_id=%s entity_urn=%s", tenant_id, urn
        )
        return False
    if entity.get("location") is None:
        return False
    geo = _location_geojson(entity)
    if geo is None:
        logger.warning(
            "geometry skipped, invalid location: tenant_id=%s entity_urn=%s", tenant_id, urn
        )
        return False
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_SET_TENANT_SQL, tenant_id)
            await conn.execute(_UPSERT_SQL, tenant_id, urn, etype, json.dumps(geo))
    return True


async def delete_geometry(pool, tenant_id: str, urn: str) -> bool:
    """Remove a cached geometry. True when a row was deleted."""
    if not tenant_id or not urn:
        return False
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_SET_TENANT_SQL, tenant_id)
            status = await conn.execute(_DELETE_SQL, tenant_id, urn)
    return status == "DELETE 1"


async def cached_urns(pool, tenant_id: str, entity_type: str, before: datetime) -> Set[str]:
    """URNs cached for one tenant and entity type, last written before `before`."""
    if not tenant_id:
        raise ValueError("tenant_id is required (RLS would hide every row)")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_SET_TENANT_SQL, tenant_id)
            rows = await conn.fetch(_CACHED_URNS_SQL, tenant_id, entity_type, before)
    return {r["entity_urn"] for r in rows}
