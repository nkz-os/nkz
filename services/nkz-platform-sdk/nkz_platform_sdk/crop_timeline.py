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

from nkz_platform_sdk.crop_cycles import resolve_crop_cycles
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


async def load_crop_data(client: OrionClient, parcel_urn: str) -> tuple[dict, list[dict], list[dict]]:
    try:
        parcel = await client.get_entity(parcel_urn, options="keyValues")
        q = f'hasAgriParcel=="{parcel_urn}"'
        crops = await _query_all(client, "AgriCrop", q)
        ops = await _query_all(client, "AgriParcelOperation", f"{q};{_BOUNDARY_OPERATIONS}")
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else None
        if status == 404:
            raise ParcelNotFound(parcel_urn) from e
        # 408/429 are the broker being slow or busy, not a bad request.
        if status is not None and 400 <= status < 500 and status not in (408, 429):
            raise CropDataRejected(f"{status}: {e}") from e
        raise CropDataUnavailable(f"{status}: {e}") from e
    except httpx.HTTPError as e:
        raise CropDataUnavailable(str(e)) from e
    except ValueError as e:  # a 200 that is not JSON (proxy page, truncated body)
        raise CropDataUnavailable(f"unreadable answer: {e}") from e
    return parcel, crops, ops


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
        parcel, crops, ops = await load_crop_data(client, urn)
    except CropDataRejected as e:
        logger.error("crop timeline: broker rejected the request tenant=%s parcel=%s: %s", tenant_id, urn, e)
        return None
    except CropDataUnavailable as e:
        logger.warning("crop timeline unavailable tenant=%s parcel=%s: %s", tenant_id, urn, e)
        return None
    finally:
        await client.close()
    return resolve_crop_cycles(urn, crops, ops, at or local_today(parcel)).to_dict()
