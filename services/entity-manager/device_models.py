"""Orion writes of the official `DeviceModel` and of the Device attributes a profile contributes.

`common.device_model` builds them; this module writes them. Everything here is for entities of
type `Device`: a `ManufacturingMachine` never gets these attributes.

The relationship's target is always written first (`ensure_device_model`), so no Device points to
a DeviceModel that does not exist.
"""
import logging
from typing import Mapping, Optional

import requests
from nkz_platform_sdk import SyncOrionClient
from requests.exceptions import HTTPError

import dataloggers
from common.context_aliases import canonical_attribute
from common.device_model import (OPTIONAL_PROFILE_ATTRS, build_device_model, device_model_id,
                                 device_profile_attrs)
from common.ngsi_headers import inject_fiware_headers
from common.window_statistics import expand_measurements
from dataloggers import ApiError
from device_profile import entity_profile_code
from helpers import CONTEXT_URL, ORION_URL

logger = logging.getLogger(__name__)

_MODEL_ERROR = 'Could not write the device model'
_DEVICE_ERROR = 'Entity update failed'
_TIMEOUT = 10
PAGE_SIZE = 200


class StaleAttributeError(ApiError):
    """The profile's attributes are on the Device, but one it no longer has could not be deleted.

    The Device is already on the new profile, so the caller must not undo the IoT Agent;
    repeating the call finishes the job. The step `orion_profile_leftover` tells the client apart
    from `orion_profile`, where nothing changed.
    """


# ── Orion requests ─────────────────────────────────────────────────────

def why(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        return exc.message
    status = getattr(getattr(exc, 'response', None), 'status_code', None)
    return f'{type(exc).__name__} status={status}' if status else type(exc).__name__


def _headers(tenant: str, link: bool = True) -> dict:
    """The headers of a request without `@context`; raises when its Link is missing.

    Without one the broker expands every name with the default vocabulary and answers 204 to a
    write, so nothing is sent.
    """
    headers = inject_fiware_headers({}, tenant=tenant, has_context_in_body=False)
    if not link:
        headers.pop('Link', None)
    elif not headers.get('Link'):
        raise ApiError(502, 'CONTEXT_URL is not set: the request would go out without its context')
    return headers


def _entity_url(urn: str) -> str:
    return f"{ORION_URL}/ngsi-ld/v1/entities/{urn}"


def read_entity(tenant: str, urn: str, link: bool = True) -> Optional[dict]:
    """The FULL entity, or None when it does not exist.

    `link=False` reads it without the platform context, so names come as they are stored: full
    IRIs, except the terms of the default vocabulary, which the broker always shortens.
    """
    resp = requests.get(_entity_url(urn), headers=_headers(tenant, link), timeout=_TIMEOUT)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise HTTPError(response=resp)
    return resp.json()


def list_entities(tenant: str, entity_type: str) -> list[dict]:
    """Every entity of a type; raises unless the pages add up to the total the broker reports."""
    found: dict = {}
    offset, total = 0, None
    while total is None or offset < total:
        resp = requests.get(f"{ORION_URL}/ngsi-ld/v1/entities", headers=_headers(tenant),
                            params={'type': entity_type, 'limit': PAGE_SIZE, 'offset': offset,
                                    'count': 'true'}, timeout=_TIMEOUT)
        if resp.status_code != 200:
            raise HTTPError(response=resp)
        page = resp.json()
        total = int(resp.headers['NGSILD-Results-Count'])
        found.update({entity['id']: entity for entity in page})
        offset += PAGE_SIZE
    if len(found) != total:
        raise ApiError(502, f'The broker reports {total} entities and listed {len(found)}')
    return list(found.values())


def append_attrs(tenant: str, urn: str, attrs: dict) -> None:
    """POST /attrs adds and overwrites; PATCH would not add a missing attribute (207)."""
    resp = requests.post(f"{_entity_url(urn)}/attrs", json=attrs, headers=_headers(tenant),
                         timeout=_TIMEOUT)
    if resp.status_code not in (200, 204):
        raise HTTPError(response=resp)


def delete_attr(tenant: str, urn: str, attr: str) -> None:
    """The attribute is gone afterwards; that it was not there (404) is fine."""
    resp = requests.delete(f"{_entity_url(urn)}/attrs/{attr}", headers=_headers(tenant),
                           timeout=_TIMEOUT)
    if resp.status_code not in (200, 204, 404):
        raise HTTPError(response=resp)


# ── writes ─────────────────────────────────────────────────────────────

def profile_attrs(tenant: str, profile: Mapping) -> dict:
    """The Device attributes of `profile`; 400 when its code cannot be part of a DeviceModel id.

    Sends nothing, so callers run it before their first write.
    """
    try:
        return device_profile_attrs(tenant, profile)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None


def ensure_device_model(tenant: str, profile: Mapping) -> None:
    """Make the tenant's DeviceModel of `profile` (a `sensor_profiles` row) exist and be current.

    Idempotent and shared by every Device on the profile: never undone by a caller.
    """
    try:
        model = build_device_model(tenant, profile)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None
    try:
        # ASSUMPTION: without the platform context nothing is written, not even this upsert, which
        # carries its own context; so the answer does not depend on what the profile maps.
        _headers(tenant)
        with SyncOrionClient(tenant, base_url=ORION_URL, context_url=CONTEXT_URL) as client:
            # A 207 does not raise: what Orion refused is in `errors`.
            errors = client.upsert_entities_batch([model])['errors']
    except Exception as exc:
        logger.error("device model upsert failed: tenant=%s device_model=%s error=%s",
                     tenant, model['id'], why(exc))
        raise ApiError(502, _MODEL_ERROR, 'device_model') from None
    if errors:
        logger.error("device model upsert refused: tenant=%s device_model=%s errors=%s",
                     tenant, model['id'], errors)
        raise ApiError(502, _MODEL_ERROR, 'device_model')
    for attr in OPTIONAL_PROFILE_ATTRS:
        if attr in model:
            continue
        # The upsert keeps attributes it does not carry: a stale one would be untrue.
        try:
            delete_attr(tenant, model['id'], attr)
        except Exception as exc:
            logger.error("device model attribute not removed: tenant=%s device_model=%s "
                         "attribute=%s error=%s", tenant, model['id'], attr, why(exc))
            raise ApiError(502, _MODEL_ERROR, 'device_model') from None


def write_profile_attrs(tenant: str, urn: str, profile: Mapping, extra: Optional[dict] = None) -> None:
    """Put the Device on `profile`: one POST /attrs (with `extra`), then delete what it no longer has.

    ApiError when the POST failed; StaleAttributeError when only a DELETE failed.
    """
    attrs = profile_attrs(tenant, profile)
    try:
        append_attrs(tenant, urn, {**(extra or {}), **attrs})
    except Exception as exc:
        logger.error("profile attributes not written: tenant=%s device=%s profile=%s error=%s",
                     tenant, urn, profile.get('code'), why(exc))
        raise ApiError(502, _DEVICE_ERROR, 'orion_profile') from None
    for attr in OPTIONAL_PROFILE_ATTRS:
        if attr in attrs:
            continue
        try:
            delete_attr(tenant, urn, attr)
        except Exception as exc:
            logger.error("stale profile attribute not removed: tenant=%s device=%s profile=%s "
                         "attribute=%s error=%s", tenant, urn, profile.get('code'), attr, why(exc))
            raise StaleAttributeError(502, _DEVICE_ERROR, 'orion_profile_leftover') from None


# ── differences and undated readings ───────────────────────────────────

def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _is_stored(stored, wanted: dict) -> bool:
    """`stored` says what `wanted` says; Orion returns a one-element array as its bare element."""
    if not isinstance(stored, Mapping) or stored.get('type') != wanted['type']:
        return False
    member = 'object' if wanted['type'] == 'Relationship' else 'value'
    return _as_list(stored.get(member)) == _as_list(wanted[member])


def profile_attr_changes(tenant: str, profile: Mapping, entity: Mapping) -> tuple[dict, list[str]]:
    """What a Device read from Orion needs: (attributes to write, attribute names to delete)."""
    wanted = profile_attrs(tenant, profile)
    changed = {name: attr for name, attr in wanted.items() if not _is_stored(entity.get(name), attr)}
    stale = [name for name in OPTIONAL_PROFILE_ATTRS if name not in wanted and name in entity]
    return changed, stale


def is_model_current(tenant: str, profile: Mapping) -> bool:
    """The tenant's DeviceModel of `profile` exists and says what `ensure_device_model` would write."""
    model = build_device_model(tenant, profile)
    stored = read_entity(tenant, model['id'])
    if stored is None or any(a in stored and a not in model for a in OPTIONAL_PROFILE_ATTRS):
        return False
    return all(_is_stored(stored.get(name), attr) for name, attr in model.items()
               if name not in ('id', 'type'))


def reading_channels(profile: Mapping) -> set[str]:
    """The attributes a profile declares as readings: canonical names, statistics expanded."""
    mapping = profile.get('mapping') or {}
    measurements = [m for m in mapping.get('measurements') or [] if isinstance(m, dict)]
    return {canonical_attribute(m['sdmAttribute'])
            for m in expand_measurements(measurements) if m.get('sdmAttribute')}


def undated_readings(profile: Mapping, entity: Mapping) -> list[str]:
    """Channels of `profile` that `entity` carries without a time of their own, sorted.

    Any write on a Device fires the Device -> DeviceMeasurement bridge, which stamps the current
    time on such a reading: a measurement nobody took. Same matching as the bridge
    (`blueprints.measurements.build_measurements`): canonical names, statistics channels expanded,
    the canonical attribute winning over its alias, Properties only, and a time taken from the
    attribute or from the entity-level `observedAt`.
    """
    entity_time = entity.get('observedAt')
    if isinstance(entity_time, Mapping) and entity_time.get('value'):
        return []
    channels = reading_channels(profile)
    readings: dict = {}
    for name, attr in entity.items():
        canonical = canonical_attribute(name)
        if canonical in channels and (canonical not in readings or name == canonical):
            readings[canonical] = attr
    return sorted(name for name, attr in readings.items()
                  if isinstance(attr, Mapping) and attr.get('type') == 'Property'
                  and not attr.get('observedAt'))


# ── profile edit ───────────────────────────────────────────────────────

def refresh_profile(tenant: str, code: str) -> dict:
    """Bring Orion in line with an edited profile: its DeviceModel, then the Devices on it.

    The profile is read again first, so two edits at the same time both write what was saved last.

    The DeviceModel is refreshed only if it exists, and Devices are the tenant's linked ones
    (the selection of `reprovision_profile_devices`). Only attributes that are missing or
    different are written, because each Device write fires the measurement bridge; a Device with
    an undated reading is not written at all and is listed in `skipped`.

    Best effort and never raises: the profile is already saved. What failed is logged and listed
    in `refresh_failed`; saving the profile again retries it.
    """
    out: dict = {'refreshed': [], 'skipped': [], 'refresh_failed': []}
    try:
        model_id = device_model_id(tenant, code)
    except ValueError as exc:
        logger.error("profile not refreshed: tenant=%s profile=%s error=%s", tenant, code, exc)
        out['error'] = 'profile_code_unusable'
        return out
    try:
        profile = dataloggers.read_profile(tenant, code)
    except Exception as exc:
        logger.error("profile not refreshed, it could not be read again: tenant=%s profile=%s error=%s",
                     tenant, code, why(exc))
        out['error'] = 'profile_read_failed'
        return out
    if not profile:
        logger.warning("profile not refreshed, it is gone: tenant=%s profile=%s", tenant, code)
        out['error'] = 'profile_not_found'
        return out
    model_exists = False
    try:
        model_exists = read_entity(tenant, model_id) is not None
        if model_exists:
            ensure_device_model(tenant, profile)
    except Exception as exc:
        logger.error("device model not refreshed: tenant=%s device_model=%s profile=%s error=%s",
                     tenant, model_id, code, why(exc))
        out['refresh_failed'].append(model_id)
    try:
        links = dataloggers.links_on_profile(tenant, code)
    except Exception as exc:
        logger.error("profile devices not listed for refresh: tenant=%s profile=%s error=%s",
                     tenant, code, why(exc))
        out['error'] = 'device_listing_failed'
        return out
    for urn in (link['device_urn'] for link in links):
        if not dataloggers.is_device_type(urn):
            continue
        try:
            outcome = _refresh_device(tenant, urn, profile, model_exists)
        except Exception as exc:
            logger.error("device not refreshed: tenant=%s device=%s profile=%s error=%s",
                         tenant, urn, code, why(exc))
            outcome = 'refresh_failed'
        if outcome:
            out[outcome].append(urn)
    return out


def _refresh_device(tenant: str, urn: str, profile: Mapping, model_exists: bool) -> Optional[str]:
    """'refreshed', 'skipped', or None when there is nothing to write: the Device already has
    everything, or it left the profile."""
    entity = read_entity(tenant, urn)
    if entity is None:
        raise ApiError(404, 'Device not found')
    if not dataloggers.is_on_profile(tenant, entity, profile.get('code')):
        # It moved after the listing: another profile's attributes would be untrue on it.
        logger.warning("device not refreshed, it left the profile: tenant=%s device=%s "
                       "expected=%r found=%r", tenant, urn, profile.get('code'),
                       entity_profile_code(tenant, entity))
        return None
    changed, stale = profile_attr_changes(tenant, profile, entity)
    if not model_exists:
        # ASSUMPTION: a profile edit creates no DeviceModel and a Device never points to a missing
        # one, so the relationship is left out until the model exists.
        changed.pop('refDeviceModel', None)
    if not changed and not stale:
        return None
    undated = undated_readings(profile, entity)
    if undated:
        logger.warning("device refresh skipped, readings without observedAt: tenant=%s device=%s "
                       "profile=%s readings=%s", tenant, urn, profile.get('code'), undated)
        return 'skipped'
    if changed:
        append_attrs(tenant, urn, changed)
    for attr in stale:
        delete_attr(tenant, urn, attr)
    return 'refreshed'
