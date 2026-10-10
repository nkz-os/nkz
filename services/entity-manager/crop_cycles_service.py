"""Crop cycles for a parcel: read the broker, resolve with the SDK, reconcile back.

The rules live in nkz_platform_sdk.crop_cycles; this module only does the I/O.
The reconciler writes facts (field-operation dates, the current crop link,
lastPlantedAt) and only when they change, so its own notifications settle.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from nkz_platform_sdk import SyncOrionClient
from nkz_platform_sdk.crop_cycles import resolve_crop_cycles

logger = logging.getLogger(__name__)

ORION_URL = os.getenv('ORION_URL', 'http://orion-ld-service:1026')
CONTEXT_URL = os.getenv('CONTEXT_URL', '')
_PAGE = 500


class ParcelNotFound(Exception):
    pass


class OrionUnavailable(Exception):
    pass


class BrokerRejected(Exception):
    """The broker answered but refused the request (4xx other than 404): a request bug, not an outage."""


def normalize_parcel_urn(parcel_id: str) -> str:
    return parcel_id if parcel_id.startswith('urn:') else f'urn:ngsi-ld:AgriParcel:{parcel_id}'


def _client(tenant_id: str):
    return SyncOrionClient(tenant_id, base_url=ORION_URL, context_url=CONTEXT_URL or None)


# Only operations that can set a cycle boundary; a parcel's history of sprays and
# irrigations must not push its sowings out of the page.
_BOUNDARY_OPERATIONS = 'operationType=="sowing","harvesting","tillage"'


def _query_all(client, entity_type: str, q: str) -> list:
    out, offset = [], 0
    while True:
        page = client.query_entities(type=entity_type, q=q, limit=_PAGE, offset=offset, options='keyValues') or []
        out.extend(page)
        if len(page) < _PAGE:
            return out
        offset += _PAGE


def _load(client, parcel_urn: str):
    try:
        parcel = client.get_entity(parcel_urn, options='keyValues')
        q = f'hasAgriParcel=="{parcel_urn}"'
        crops = _query_all(client, 'AgriCrop', q)
        ops = _query_all(client, 'AgriParcelOperation', f'{q};{_BOUNDARY_OPERATIONS}')
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status == 404:
            raise ParcelNotFound(parcel_urn) from e
        # 408/429 are the broker being slow or busy, not a bad request.
        if status is not None and 400 <= status < 500 and status not in (408, 429):
            raise BrokerRejected(f'{status}: {e}') from e
        raise OrionUnavailable(str(e)) from e
    except requests.RequestException as e:
        raise OrionUnavailable(str(e)) from e
    return parcel, crops or [], ops or []


def local_today(parcel: dict, now: datetime | None = None) -> date:
    """Today at the parcel, from its `timeZone` (IANA, written by the weather worker).

    Without a usable zone, UTC: between midnight and the UTC offset the day is then
    the previous one, which is what this avoids where the zone is known.
    """
    now = now or datetime.now(timezone.utc)
    tz = _val((parcel or {}).get('timeZone'))
    if isinstance(tz, str) and tz:
        try:
            return now.astimezone(ZoneInfo(tz)).date()
        except (ZoneInfoNotFoundError, ValueError) as e:
            logger.warning('unusable timeZone %r on parcel %s: %s; using UTC', tz, (parcel or {}).get('id'), e)
    return now.astimezone(timezone.utc).date()


def timeline(tenant_id: str, parcel_urn: str, at: date | None, client=None) -> dict:
    """Crop cycles of a parcel at `at` (default: today at the parcel)."""
    client = client or _client(tenant_id)
    parcel, crops, ops = _load(client, parcel_urn)
    return resolve_crop_cycles(parcel_urn, crops, ops, at or local_today(parcel)).to_dict()


def _val(attr):
    if isinstance(attr, dict):
        return attr.get('@value') or attr.get('object') or _val(attr.get('value'))
    return attr


def _date_prop(d: date) -> dict:
    return {'type': 'Property', 'value': {'@type': 'Date', '@value': d.isoformat()}}


def reconcile_parcel(tenant_id: str, parcel_urn: str, at: date | None = None, client=None) -> list:
    client = client or _client(tenant_id)
    parcel, crops, ops = _load(client, parcel_urn)
    tl = resolve_crop_cycles(parcel_urn, crops, ops, at or local_today(parcel))
    by_id = {c.get('id'): c for c in crops}
    written = []
    for cy in tl.cycles:
        crop = by_id.get(cy.crop_id, {})
        for boundary, attr in ((cy.start, 'actualPlantingDate'), (cy.end, 'actualTerminationDate')):
            if boundary.provenance == 'actual' and str(_val(crop.get(attr)) or '')[:10] != boundary.date.isoformat():
                client.append_entity_attrs(cy.crop_id, {attr: _date_prop(boundary.date)})
                written.append((cy.crop_id, attr))
    cur = tl.current
    if cur:
        linked = _val(parcel.get('hasAgriCrop')) or _val(parcel.get('refAgriCrop'))
        if linked != cur.crop_id:
            client.append_entity_attrs(parcel_urn, {'hasAgriCrop': {'type': 'Relationship', 'object': cur.crop_id}})
            written.append((parcel_urn, 'hasAgriCrop'))
        if cur.start.provenance in ('actual', 'declared'):
            stamp = f'{cur.start.date.isoformat()}T00:00:00Z'
            if str(_val(parcel.get('lastPlantedAt')) or '') != stamp:
                client.append_entity_attrs(parcel_urn, {'lastPlantedAt': {
                    'type': 'Property', 'value': {'@type': 'DateTime', '@value': stamp}}})
                written.append((parcel_urn, 'lastPlantedAt'))
    if written:
        logger.info('crop-cycle reconcile tenant=%s parcel=%s wrote=%s', tenant_id, parcel_urn, written)
    return written


def list_parcels(tenant_id: str, client=None) -> list:
    """Every AgriParcel id of a tenant, paged. No attrs filter: attrs selects
    entities that HAVE those attributes, and 'id' is not an attribute."""
    client = client or _client(tenant_id)
    ids, offset = [], 0
    try:
        while True:
            page = client.query_entities(type='AgriParcel', limit=_PAGE, offset=offset, options='keyValues')
            ids.extend(e['id'] for e in page or [] if e.get('id'))
            if not page or len(page) < _PAGE:
                return ids
            offset += _PAGE
    except requests.RequestException as e:
        raise OrionUnavailable(str(e)) from e


def parcels_in_notification(entities: list) -> set:
    out = set()
    for e in entities or []:
        if e.get('type') not in ('AgriCrop', 'AgriParcelOperation'):
            continue
        p = _val(e.get('hasAgriParcel')) or _val(e.get('refAgriParcel'))
        if isinstance(p, str) and p:
            out.add(p)
    return out
