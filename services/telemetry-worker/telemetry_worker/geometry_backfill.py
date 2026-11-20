"""Startup backfill of observation_geometry_cache (contract 1.4, 8.12).

The geometry subscription fires only on a create or a `location` change, so a
feature that existed before it never reaches the cache, and tier 2 silently
falls back to the logger location for it. At worker start this lists, per
managed tenant, every entity of the subscribed types and upserts its location.

Deletion is the dangerous half, so it is guarded:
- a type's cached rows are pruned only after a listing that is complete: every
  page answered 200 with a list, and the distinct ids equal Orion's
  NGSILD-Results-Count (offset paging is not a snapshot; a shifted page would
  otherwise make a live entity look deleted);
- any Orion error discards that listing entirely: nothing upserted, nothing deleted;
- only rows last written before the listing started are candidates, so a row a
  live notification wrote meanwhile is never removed.

Re-running converges: upserts are idempotent and deletes only remove rows of
entities Orion no longer has. Nothing here raises to the caller.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Sequence

import requests

from common.ngsi_headers import inject_fiware_headers

from . import subscription_manager as sm
from .geometry_cache import cached_urns, delete_geometry, upsert_geometry

logger = logging.getLogger(__name__)

GEOMETRY_ENTITY_TYPES = sm.GEOMETRY_ENTITY_TYPES

# Polygons make parcel pages heavy; Orion-LD caps a page at 1000.
PAGE_SIZE = 200
# 200 x 2500 = 500k entities of one type: far beyond any tenant, so hitting it
# means the broker is not paging (the same page again), not a big tenant.
MAX_PAGES = 2500
ORION_TIMEOUT_S = 30


class ListingFailed(Exception):
    """Orion did not return a usable listing; nothing from it may be used."""


@dataclass(frozen=True)
class Listing:
    entities: List[Dict[str, Any]]
    complete: bool


@dataclass
class BackfillSummary:
    tenant_id: str
    upserted: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    incomplete_types: List[str] = field(default_factory=list)


def list_entities(
    tenant_id: str,
    entity_type: str,
    *,
    http_get: Callable[..., Any] = requests.get,
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
) -> Listing:
    """Every entity of one type for one tenant (normalized), following Orion's paging.

    Raises ListingFailed on any non-200 page, a body that is not a list, a
    transport error, or a listing that never ends. `complete` is True only when
    the distinct ids match NGSILD-Results-Count on every page.

    ASSUMPTION: no `attrs=location` filter. In NGSI-LD `attrs` also drops entities
    that lack the attribute, which would make an entity without a location look
    deleted; the cache prune is defined on entity existence.
    """
    # Same headers as the subscriptions (subscription_manager._make_headers).
    headers = inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)
    url = f"{sm.ORION_URL}/ngsi-ld/v1/entities"
    entities: List[Dict[str, Any]] = []
    counts = set()
    offset = 0
    for _ in range(max_pages):
        params = {"type": entity_type, "limit": page_size, "offset": offset, "count": "true"}
        try:
            resp = http_get(url, headers=headers, params=params, timeout=ORION_TIMEOUT_S)
        except requests.RequestException as e:
            raise ListingFailed(f"transport error at offset={offset}: {e}") from e
        if resp.status_code != 200:
            raise ListingFailed(f"HTTP {resp.status_code} at offset={offset}")
        try:
            page = resp.json()
        except ValueError as e:
            raise ListingFailed(f"invalid JSON at offset={offset}") from e
        if not isinstance(page, list):
            raise ListingFailed(f"page at offset={offset} is not a list")
        raw_count = resp.headers.get("NGSILD-Results-Count")
        try:
            counts.add(int(raw_count) if raw_count is not None else None)
        except ValueError:
            counts.add(None)
        entities.extend(page)
        if len(page) < page_size:
            ids = [e.get("id") for e in entities if isinstance(e, dict)]
            distinct = set(i for i in ids if isinstance(i, str) and i)
            complete = (
                len(counts) == 1
                and None not in counts
                and len(distinct) == len(entities) == next(iter(counts))
            )
            return Listing(entities=entities, complete=complete)
        offset += page_size
    raise ListingFailed(f"no short page after {max_pages} pages (broker not paging?)")


def orion_tenant(tenant_id: str) -> str:
    """The tenant id Orion holds (and echoes in NGSILD-Tenant on every notification).

    Live notifications key the cache by that header, so the backfill keys rows by
    the same normalized id rather than by the raw tenants row.
    """
    headers = inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)
    return headers.get("NGSILD-Tenant") or tenant_id


async def db_now(pool) -> datetime:
    """The database clock: updated_at is written with it, so candidates compare against it."""
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT clock_timestamp()")


async def _backfill_type(
    pool, tenant_id: str, entity_type: str, lister: Callable[[str, str], Listing],
    summary: BackfillSummary,
) -> None:
    started = await db_now(pool)
    try:
        listing = await asyncio.to_thread(lister, tenant_id, entity_type)
    except ListingFailed as e:
        summary.incomplete_types.append(entity_type)
        logger.error(
            "geometry backfill listing failed, cache left untouched: tenant_id=%s "
            "entity_type=%s error=%s", tenant_id, entity_type, e,
        )
        return

    listed = set()
    for entity in listing.entities:
        urn = entity.get("id") if isinstance(entity, dict) else None
        if isinstance(urn, str) and urn:
            listed.add(urn)
        if not isinstance(entity, dict):
            summary.skipped += 1
            continue
        try:
            if await upsert_geometry(pool, tenant_id, entity):
                summary.upserted += 1
            else:
                summary.skipped += 1
        except Exception as e:
            summary.failed += 1
            logger.error(
                "geometry backfill upsert failed: tenant_id=%s entity_type=%s entity_urn=%s "
                "error=%s", tenant_id, entity_type, urn, e,
            )

    if not listing.complete:
        summary.incomplete_types.append(entity_type)
        logger.warning(
            "geometry backfill listing not verifiably complete, nothing deleted: "
            "tenant_id=%s entity_type=%s listed=%d", tenant_id, entity_type, len(listing.entities),
        )
        return

    for urn in sorted(await cached_urns(pool, tenant_id, entity_type, started) - listed):
        if await delete_geometry(pool, tenant_id, urn):
            summary.deleted += 1
            logger.info(
                "geometry backfill removed cached row of a deleted entity: tenant_id=%s "
                "entity_type=%s entity_urn=%s", tenant_id, entity_type, urn,
            )


async def backfill_tenant(
    pool,
    tenant_id: str,
    *,
    lister: Callable[[str, str], Listing] = list_entities,
    types: Sequence[str] = GEOMETRY_ENTITY_TYPES,
) -> BackfillSummary:
    """Backfill one tenant, type by type; one failing type never stops the others."""
    summary = BackfillSummary(tenant_id=tenant_id)
    for entity_type in types:
        try:
            await _backfill_type(pool, tenant_id, entity_type, lister, summary)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if entity_type not in summary.incomplete_types:
                summary.incomplete_types.append(entity_type)
            logger.error(
                "geometry backfill failed: tenant_id=%s entity_type=%s error=%s",
                tenant_id, entity_type, e, exc_info=True,
            )
    return summary


async def run_geometry_backfill(
    pool,
    *,
    tenants_fn: Callable[[], List[str]] = sm.managed_tenants,
    lister: Callable[[str, str], Listing] = list_entities,
    types: Sequence[str] = GEOMETRY_ENTITY_TYPES,
) -> List[BackfillSummary]:
    """Backfill every managed tenant and log one summary line each. Never raises
    (except cancellation at shutdown)."""
    summaries: List[BackfillSummary] = []
    try:
        tenants = await asyncio.to_thread(tenants_fn)
        for tenant_id in tenants:
            s = await backfill_tenant(pool, orion_tenant(tenant_id), lister=lister, types=types)
            summaries.append(s)
            level = logging.WARNING if (s.failed or s.incomplete_types) else logging.INFO
            logger.log(
                level,
                "geometry backfill done: tenant_id=%s upserted=%d deleted=%d skipped=%d "
                "failed=%d incomplete_types=%s",
                s.tenant_id, s.upserted, s.deleted, s.skipped, s.failed,
                ",".join(s.incomplete_types) or "-",
            )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("geometry backfill aborted: error=%s", e, exc_info=True)
    return summaries
