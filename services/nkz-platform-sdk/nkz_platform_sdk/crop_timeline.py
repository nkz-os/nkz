"""Crop-cycle timeline of a parcel, read from the broker.

The rule lives in ``crop_cycles`` (pure); this module does the I/O every reader
needs: the parcel, its crops and the operations that can set a cycle boundary,
then today at the parcel. Module backends call this instead of a core service.
A failure is ``None`` (logged), never an empty timeline.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from nkz_platform_sdk.crop_cycles import (
    ParcelCropData,
    declared_campaign_entries,
    has_catalogue_shape,
    resolve_crop_cycles,  # noqa: F401  (kept importable from here)
    resolve_parcel_crops,
    _crop_target,
    _str_read,
)
from nkz_platform_sdk.orion import OrionClient

logger = logging.getLogger(__name__)

_PAGE = 500
# Only operations that can set a cycle boundary: years of sprays and irrigations
# must not push a parcel's sowings out of the page.
_BOUNDARY_OPERATIONS = 'operationType=="sowing","harvesting","tillage"'


class CropDataUnavailable(Exception):
    """The broker could not answer for this parcel."""


class ParcelNotFound(CropDataUnavailable):
    """The parcel does not exist in this tenant."""


class CropDataRejected(CropDataUnavailable):
    """The broker answered but refused the request (4xx other than 404/408/429):
    a request bug, not an outage."""


def normalize_parcel_urn(parcel_id: str) -> str:
    return parcel_id if parcel_id.startswith("urn:") else f"urn:ngsi-ld:AgriParcel:{parcel_id}"


def _val(attr):
    if isinstance(attr, dict):
        return attr.get("@value") or attr.get("object") or _val(attr.get("value"))
    return attr


def local_today(parcel: dict, now: datetime | None = None) -> date:
    """Today at the parcel, from its IANA ``timeZone``; UTC when absent or unusable."""
    now = now or datetime.now(timezone.utc)
    tz = _val((parcel or {}).get("timeZone"))
    if isinstance(tz, str) and tz:
        try:
            return now.astimezone(ZoneInfo(tz)).date()
        except (ZoneInfoNotFoundError, ValueError) as e:
            logger.warning("unusable timeZone %r on parcel %s: %s; using UTC", tz, (parcel or {}).get("id"), e)
    return now.astimezone(timezone.utc).date()


async def _query_all(client: OrionClient, entity_type: str, q: str) -> list[dict]:
    out, offset = [], 0
    while True:
        page = await client.query_entities(type=entity_type, q=q, limit=_PAGE, offset=offset, options="keyValues") or []
        out.extend(page)
        if len(page) < _PAGE:
            return out
        offset += _PAGE


class CatalogueShapeError(CropDataUnavailable):
    """The parcel has the catalogue shape: the legacy 3-tuple cannot describe it.

    ``data`` already holds everything that was read, so ``load_parcel_crops`` does not read twice."""

    def __init__(self, data: ParcelCropData):
        super().__init__("catalogue shape: use load_parcel_crops")
        self.data = data


def _read_error(e: Exception, parcel_urn: str, *, missing: str | None = None) -> CropDataUnavailable:
    """Map a failed read to the SDK errors. ``missing`` names what a 404 means (default: the parcel)."""
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code if e.response is not None else None
        if status == 404:
            if missing:
                return CropDataUnavailable(f"{missing} not found")
            return ParcelNotFound(parcel_urn)
        # 408/429 are the broker being slow or busy, not a bad request.
        if status is not None and 400 <= status < 500 and status not in (408, 429):
            return CropDataRejected(f"{status}: {e}")
        return CropDataUnavailable(f"{status}: {e}")
    if isinstance(e, httpx.HTTPError):
        return CropDataUnavailable(str(e))
    return CropDataUnavailable(f"unreadable answer: {e}")  # a 200 that is not JSON (proxy page, truncated body)


async def _read_parcel_crops(client: OrionClient, parcel_urn: str) -> ParcelCropData:
    try:
        parcel = await client.get_entity(parcel_urn, options="keyValues")
        q = f'hasAgriParcel=="{parcel_urn}"'
        legacy = await _query_all(client, "AgriCrop", q)
        ops = await _query_all(client, "AgriParcelOperation", f"{q};{_BOUNDARY_OPERATIONS}")
    except (httpx.HTTPError, ValueError) as e:
        raise _read_error(e, parcel_urn) from e

    # Crops and varieties are read by id, one at a time and never twice: no query grows with the data.
    legacy_ids = {c.get("id") for c in legacy}
    targets: dict[str, None] = {}
    for entity in (*ops, parcel):
        urn = _crop_target(entity)
        if urn and urn not in legacy_ids:
            targets[urn] = None
    for entry in declared_campaign_entries(parcel):
        urn = entry.get("crop")
        urn = urn.get("object") if isinstance(urn, dict) else urn
        if isinstance(urn, str) and urn and urn not in legacy_ids:
            targets[urn] = None
    crops: dict[str, dict] = {}
    for urn in targets:
        try:
            crops[urn] = await client.get_entity(urn, options="keyValues")
        except (httpx.HTTPError, ValueError) as e:
            raise _read_error(e, parcel_urn, missing=f"crop {urn}") from e

    products: dict[str, dict] = {}
    for urn in dict.fromkeys(u for u in (_str_read(o, "hasAgriProductType") for o in ops) if u):
        try:
            products[urn] = await client.get_entity(urn, options="keyValues")
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning("crop catalogue: variety %s not found, cycle without variety", urn)
                continue
            raise _read_error(e, parcel_urn) from e
        except (httpx.HTTPError, ValueError) as e:
            raise _read_error(e, parcel_urn) from e
    return ParcelCropData(parcel={"id": parcel_urn, **parcel}, operations=tuple(ops), legacy_crops=tuple(legacy),
                          crops=crops, products=products)


async def load_crop_data(client: OrionClient, parcel_urn: str) -> tuple[dict, list[dict], list[dict]]:
    """Legacy shape only: (parcel, AgriCrop of the parcel, boundary operations).

    A parcel with the catalogue shape raises ``CatalogueShapeError`` (a ``CropDataUnavailable``)."""
    data = await _read_parcel_crops(client, parcel_urn)
    if has_catalogue_shape(data):
        raise CatalogueShapeError(data)
    return data.parcel, list(data.legacy_crops), list(data.operations)


async def load_parcel_crops(client: OrionClient, parcel_urn: str) -> ParcelCropData:
    """Parcel, operations, legacy crops, catalogue crops and varieties, for either shape.

    Raises ``ParcelNotFound`` / ``CropDataRejected`` / ``CropDataUnavailable``; a catalogue crop that
    does not exist is ``CropDataUnavailable``, a variety that does not exist is only absent."""
    try:
        parcel, crops, ops = await load_crop_data(client, parcel_urn)
    except CatalogueShapeError as e:
        return e.data
    return ParcelCropData(parcel={"id": parcel_urn, **parcel}, operations=tuple(ops), legacy_crops=tuple(crops))


async def fetch_crop_timeline(
    tenant_id: str,
    parcel_id: str,
    at: date | None = None,
    *,
    base_url: str | None = None,
    context_url: str | None = None,
) -> dict | None:
    """The parcel's crop-cycle timeline (``CropCycleTimeline.to_dict()``), or ``None`` on failure."""
    urn = normalize_parcel_urn(parcel_id)
    client = OrionClient(tenant_id, base_url=base_url, context_url=context_url)
    try:
        data = await load_parcel_crops(client, urn)
    except CropDataRejected as e:
        logger.error("crop timeline: broker rejected the request tenant=%s parcel=%s: %s", tenant_id, urn, e)
        return None
    except CropDataUnavailable as e:
        logger.warning("crop timeline unavailable tenant=%s parcel=%s: %s", tenant_id, urn, e)
        return None
    finally:
        await client.close()
    return resolve_parcel_crops(data, at or local_today(data.parcel)).to_dict()
