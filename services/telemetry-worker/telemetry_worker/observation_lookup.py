"""Tenant-scoped, cached DB lookups for observation resolution.

device_deployments, observation_properties and sensor_profiles are FORCE RLS
tables and the worker connects as a non-superuser role (the table owner is
subject to the policies too). Without `app.current_tenant` set in the same
transaction every query silently returns 0 rows, so each access here runs
`set_config(..., true)` inside a transaction first and also filters tenant_id
explicitly (contract 1.2 "Tenant context for the lookups", 8.1).
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import asyncpg

from common.context_aliases import canonical_attribute
from common.window_statistics import expand_measurements
from .observations import Deployment, PropertyDef

MAX_TTL_S = 60.0
MISS_REVALIDATE_S = 5.0


class TenantContextMissing(Exception):
    """A lookup was attempted without a tenant id (RLS would hide every row)."""


_DEPLOYMENTS_SQL = """
SELECT id, device_urn, valid, observes_feature, observation_context,
       channel_overrides, mobility,
       ST_X(location::geometry) AS lon, ST_Y(location::geometry) AS lat
FROM public.device_deployments
WHERE tenant_id = $1 AND device_urn = $2
ORDER BY lower(valid)
"""

_PROFILE_SQL = """
SELECT mapping
FROM public.sensor_profiles
WHERE code = $2 AND (tenant_id IS NULL OR tenant_id = $1)
ORDER BY tenant_id NULLS LAST
LIMIT 1
"""

_PROPERTY_SQL = """
SELECT name, temporal_semantics, valid_min, valid_max, unit
FROM public.observation_properties
WHERE name = $2 AND deprecated_at IS NULL AND (tenant_id IS NULL OR tenant_id = $1)
ORDER BY tenant_id NULLS LAST
LIMIT 1
"""


def _json(value: Any) -> Any:
    """asyncpg returns jsonb as text unless a codec is registered."""
    if isinstance(value, (str, bytes)):
        return json.loads(value)
    return value


def _in_range(rng: Any, at: datetime) -> bool:
    """Range membership with the range's own bound inclusivity ([lo, hi) by default)."""
    if getattr(rng, "isempty", False):
        return False
    lo, hi = rng.lower, rng.upper
    if lo is not None and (at < lo or (at == lo and not rng.lower_inc)):
        return False
    if hi is not None and (at > hi or (at == hi and not rng.upper_inc)):
        return False
    return True


def _to_deployment(row: Any) -> Deployment:
    lon, lat = row["lon"], row["lat"]
    return Deployment(
        id=str(row["id"]),
        device_urn=row["device_urn"],
        observes_feature=row["observes_feature"],
        observation_context=_json(row["observation_context"]) or {},
        channel_overrides=_json(row["channel_overrides"]) or {},
        mobility=row["mobility"],
        location=(float(lon), float(lat)) if lon is not None and lat is not None else None,
    )


class ObservationLookup:
    def __init__(
        self,
        pool: "asyncpg.Pool",
        ttl_s: float = MAX_TTL_S,
        clock: Callable[[], float] = time.monotonic,
        miss_revalidate_s: float = MISS_REVALIDATE_S,
    ):
        if not 0 < ttl_s <= MAX_TTL_S:
            raise ValueError(f"ttl_s must be in (0, {MAX_TTL_S}], got {ttl_s}")
        if not 0 < miss_revalidate_s <= ttl_s:
            raise ValueError(f"miss_revalidate_s must be in (0, ttl_s], got {miss_revalidate_s}")
        self._miss_revalidate_s = miss_revalidate_s
        self._pool = pool
        self._ttl_s = ttl_s
        self._clock = clock
        # tenant -> {(kind, key): (expires_at, value)}
        self._cache: Dict[str, Dict[Tuple[str, Any], Tuple[float, Any]]] = {}

    def invalidate(self, tenant_id: str) -> None:
        self._cache.pop(tenant_id, None)

    @staticmethod
    def _require_tenant(tenant_id: Optional[str]) -> None:
        if not tenant_id:
            raise TenantContextMissing("tenant_id is required for observation lookups")

    async def _fetch(self, tenant_id: str, sql: str, *args: Any) -> List[Any]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant', $1, true)", tenant_id)
            return await conn.fetch(sql, tenant_id, *args)

    async def _cached(self, tenant_id: str, kind: str, key: Any, loader: Callable[[], Any]) -> Any:
        now = self._clock()
        entry = self._cache.get(tenant_id, {}).get((kind, key))
        if entry is not None and now < entry[0]:
            return entry[1]
        value = await loader()
        self._cache.setdefault(tenant_id, {})[(kind, key)] = (self._clock() + self._ttl_s, value)
        return value

    async def deployment_at(
        self, tenant_id: str, device_urn: str, at: datetime
    ) -> Optional[Deployment]:
        self._require_tenant(tenant_id)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("`at` must be timezone-aware")

        async def load() -> List[Tuple[Any, Deployment]]:
            rows = await self._fetch(tenant_id, _DEPLOYMENTS_SQL, device_urn)
            return [(r["valid"], _to_deployment(r)) for r in rows]

        def select(intervals: List[Tuple[Any, Deployment]]) -> Optional[Deployment]:
            for rng, dep in intervals:
                if _in_range(rng, at):
                    return dep
            return None

        key = ("deployments", device_urn)
        intervals = await self._cached(tenant_id, "deployments", device_urn, load)
        found = select(intervals)
        if found is not None:
            return found
        # Miss: the device may have been registered after the entry was cached
        # (e.g. an empty list). Refetch once if the entry is older than the floor.
        expires_at = self._cache[tenant_id][key][0]
        age = self._clock() - (expires_at - self._ttl_s)
        if age < self._miss_revalidate_s:
            return None
        intervals = await load()
        self._cache.setdefault(tenant_id, {})[key] = (self._clock() + self._ttl_s, intervals)
        return select(intervals)

    async def measurement_def(
        self, tenant_id: str, profile_code: str, attr: str
    ) -> Optional[dict]:
        self._require_tenant(tenant_id)

        async def load() -> Optional[Dict[str, Any]]:
            rows = await self._fetch(tenant_id, _PROFILE_SQL, profile_code)
            return _json(rows[0]["mapping"]) if rows else None

        mapping = await self._cached(tenant_id, "profile", profile_code, load)
        for item in (mapping or {}).get("measurements") or []:
            if not isinstance(item, dict):
                continue
            name = item.get("sdmAttribute")
            if isinstance(name, str) and canonical_attribute(name) == canonical_attribute(attr):
                return item
        return None

    async def channel_def(
        self, tenant_id: str, profile_code: str, channel: str
    ) -> Optional[dict]:
        """The profile entry of a PUBLISHED channel, after window-statistics expansion.

        Unlike `measurement_def` (unexpanded entries), this matches the
        channels the entity-manager builder publishes (`common.window_statistics`): a
        derived `<attr>Min`/`Max`/`StdDev` channel, or the base channel of a profile with
        `statistics`, returns an entry carrying `statistic` (and `baseAttribute`).
        Entries without a string `sdmAttribute` are ignored.
        """
        self._require_tenant(tenant_id)

        async def load() -> Optional[Dict[str, Any]]:
            rows = await self._fetch(tenant_id, _PROFILE_SQL, profile_code)
            return _json(rows[0]["mapping"]) if rows else None

        mapping = await self._cached(tenant_id, "profile", profile_code, load)
        items = [
            m for m in (mapping or {}).get("measurements") or []
            if isinstance(m, dict) and isinstance(m.get("sdmAttribute"), str) and m["sdmAttribute"]
        ]
        wanted = canonical_attribute(channel)
        for item in expand_measurements(items):
            if canonical_attribute(item["sdmAttribute"]) == wanted:
                return item
        return None

    async def property(self, tenant_id: str, name: str) -> Optional[PropertyDef]:
        self._require_tenant(tenant_id)

        async def load() -> Optional[PropertyDef]:
            rows = await self._fetch(tenant_id, _PROPERTY_SQL, name)
            if not rows:
                return None
            r = rows[0]
            return PropertyDef(
                name=r["name"],
                temporal_semantics=r["temporal_semantics"],
                valid_min=r["valid_min"],
                valid_max=r["valid_max"],
                unit=r["unit"],
            )

        return await self._cached(tenant_id, "property", name, load)
