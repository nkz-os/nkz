#!/usr/bin/env python3
"""
Sensors Blueprint - Extracted from entity_management_api.py
"""
import os
import sys
import json
import re
import uuid
import logging
import math
import secrets
from datetime import datetime, timezone
from functools import wraps
from typing import Optional

from flask import Blueprint, request, jsonify, g
from psycopg2.extras import Json, RealDictCursor
import psycopg2
import requests
import paho.mqtt.client as mqtt

from common.auth_middleware import require_auth
from common.api_errors import internal_error
from common.ngsi_headers import inject_fiware_headers
from db_helper import get_db_connection_with_tenant, get_db_connection_simple, return_db_connection

# Import shared config
from helpers import ORION_URL, CONTEXT_URL
import dataloggers
import deployments
import observation_catalogue
from common.context_aliases import canonical_attribute
from common.observation_context import validate_channel_overrides, validate_observation_context
from common.jexl_expressions import validate_jexl_expression
from common.unit_codes import to_unit_code

logger = logging.getLogger(__name__)

# MQTT Configuration for device commands
MQTT_HOST = os.getenv('MQTT_HOST', 'mosquitto-service')
MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))
MQTT_USERNAME = os.getenv('MQTT_USERNAME', '')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD', '')

sensors_bp = Blueprint('sensors', __name__)


# =============================================================================
# Helper: normalize device ID
# =============================================================================

def _normalize_device_id(device_id: str) -> str:
    """Extract short device ID from NGSI-LD URN if needed.

    'urn:ngsi-ld:AgriSensor:abc123' -> 'abc123'
    'abc123' -> 'abc123'
    """
    if device_id and ':' in device_id:
        return device_id.rsplit(':', 1)[-1]
    return device_id


def _extract_prop_value(entity: dict, key: str):
    """Extract Property value from NGSI-LD entity dict."""
    prop = entity.get(key, {})
    if isinstance(prop, dict) and 'value' in prop:
        return prop['value']
    return prop


def _patch_command_status(
    tenant_id: str,
    entity_id: str,
    status: str,
    response_data: dict | None = None,
) -> bool:
    """PATCH a DeviceCommand entity status in Orion-LD."""
    try:
        patch_body = {
            'status': {
                'type': 'Property',
                'value': status,
            },
        }
        if status == 'sent':
            patch_body['executedAt'] = {
                'type': 'Property',
                'value': datetime.utcnow().isoformat(),
            }
        if response_data:
            patch_body['response'] = {
                'type': 'Property',
                'value': response_data,
            }

        headers = inject_fiware_headers(
            {'Content-Type': 'application/json'}, tenant=tenant_id, has_context_in_body=False
        )
        resp = requests.patch(
            f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}/attrs",
            json=patch_body,
            headers=headers,
            timeout=10,
        )
        return resp.status_code in (200, 204)
    except Exception as e:
        logger.error(
            "Failed to patch DeviceCommand %s: %s", entity_id, e
        )
        return False


_HEALTH_ATTRIBUTES = frozenset({
    "healthConfig", "reliabilityStatus", "isSilenced", "calibrationConfig",
})


def _extract_health_property(entity: dict, key: str) -> Optional[dict]:
    """Extract a health-related Property (which may contain nested objects)."""
    prop = entity.get(key)
    if isinstance(prop, dict) and prop.get("type") == "Property":
        return prop
    return None


# =============================================================================
# Sensor Registration
# =============================================================================


def _guard(fn):
    """Answer dataloggers.ApiError with its status and body; anything else is a 500."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except dataloggers.ApiError as e:
            return jsonify(e.body()), e.status
        except Exception as e:
            logger.error("Error in %s: %s", fn.__name__, type(e).__name__)
            return internal_error(e, fn.__name__, user_message='Request failed')
    return wrapper


def _tenant() -> str:
    """The caller's tenant in canonical form, so URN, RLS value and FIWARE headers agree."""
    tenant = g.tenant
    if not tenant:
        raise dataloggers.ApiError(401, 'Tenant not found in token')
    from common.tenant_utils import normalize_tenant_id
    try:
        return normalize_tenant_id(tenant)
    except ValueError:
        raise dataloggers.ApiError(400, 'Invalid tenant')


def _get_user_roles():
    """Get user roles from Flask g (set by auth middleware)"""
    roles = g.get('roles', [])
    if not roles:
        payload = g.get('current_user', {})
        if payload:
            roles = payload.get('realm_access', {}).get('roles', [])
    return roles


def _require_installer() -> None:
    dataloggers.require_installer(_get_user_roles())


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise dataloggers.ApiError(400, 'No data provided')
    return data


def _parcel_links(value) -> list:
    """Ordered, de-duplicated parcel URNs from a string, {'object': ...} or a list of those."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    urns = []
    for item in items:
        if isinstance(item, dict):
            urns.extend(_parcel_links(item.get('object')))
        elif isinstance(item, str) and item:
            urns.append(item)
    return list(dict.fromkeys(urns))


def _coordinate(value, name: str) -> float:
    if value is None or isinstance(value, bool):
        raise dataloggers.ApiError(400, 'Location (lat, lon) is required')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise dataloggers.ApiError(400, f'Invalid {name}')
    limit = 90 if name == 'lat' else 180
    if not math.isfinite(number) or abs(number) > limit:
        raise dataloggers.ApiError(400, f'Invalid {name}')
    return number


@dataloggers.in_step('db', 'Database error')
def _read_profile(tenant_id: str, profile_code: str):
    with get_db_connection_with_tenant(tenant_id) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, sdm_entity_type, sdm_device_category, mapping
            FROM sensor_profiles
            WHERE code = %s AND (tenant_id IS NULL OR tenant_id = %s)
            ORDER BY tenant_id NULLS LAST
            LIMIT 1
        """, (profile_code, tenant_id))
        row = cur.fetchone()
        cur.close()
    return row


@dataloggers.in_step('orion_read', 'Could not read the context broker')
def _orion_dedup_check(tenant_id: str, entity_type: str, external_id: str):
    return requests.get(
        f"{ORION_URL}/ngsi-ld/v1/entities?type={entity_type}"
        f'&q=externalId=="{external_id}"',
        headers=inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False),
        timeout=10)


@dataloggers.in_step('orion_entity', 'Failed to create sensor entity in context broker')
def _orion_create(tenant_id: str, entity: dict):
    return requests.post(
        f"{ORION_URL}/ngsi-ld/v1/entities", json=entity,
        headers=inject_fiware_headers({'Content-Type': 'application/ld+json'},
                                      tenant=tenant_id, has_context_in_body=True),
        timeout=10)


def _ensure_bridge_subscription(tenant: str) -> None:
    """Device -> DeviceMeasurement subscription for this tenant.

    Without it the device would register but its telemetry never reaches the
    timeseries, so a registrar error or exception fails the registration (502).
    """
    try:
        from blueprints.notifications import ensure_device_measurement_subscriptions
        result = ensure_device_measurement_subscriptions(tenants=[tenant])
    except Exception as exc:
        logger.error("bridge subscription not ensured for tenant %s: %s", tenant, type(exc).__name__)
        raise dataloggers.ApiError(502, 'Could not ensure the telemetry subscription',
                                   'bridge_subscription')
    errors = (result or {}).get('errors') if isinstance(result, dict) else None
    if errors:
        logger.error("bridge subscription not ensured for tenant %s: %d registrar error(s)",
                     tenant, len(errors))
        raise dataloggers.ApiError(502, 'Could not ensure the telemetry subscription',
                                   'bridge_subscription')


# ---- observation context and deployments ---------------------------------------

_OPTIONAL_OBSERVATION_ATTRS = ('observesFeature', 'observationContext', 'channelOverrides')
_URN_CONTEXT_FIELDS = ('structure', 'enclosure')
_MIRROR_ERROR = 'Could not update the device in the context broker'


def _parse_instant(value, name: str) -> datetime:
    """ISO-8601 string with a UTC offset; anything else is a 400."""
    if not isinstance(value, str):
        raise dataloggers.ApiError(400, f'{name} must be an ISO-8601 timestamp with offset')
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        raise dataloggers.ApiError(400, f'{name} must be an ISO-8601 timestamp with offset')
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise dataloggers.ApiError(400, f'{name} must be an ISO-8601 timestamp with offset')
    return parsed


def _past_instant(value, name: str) -> datetime:
    """The given instant (never in the future), or now when absent."""
    now = datetime.now(timezone.utc)
    if value is None:
        return now
    parsed = _parse_instant(value, name)
    if parsed > now:
        raise dataloggers.ApiError(400, f'{name} must not be in the future')
    return parsed


def _zulu(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _reason(data: dict) -> str:
    reason = data.get('reason')
    if not isinstance(reason, str) or not reason.strip():
        raise dataloggers.ApiError(400, 'reason is required')
    return reason.strip()


def _location_pair(raw) -> Optional[tuple]:
    """{lat, lon} -> (lon, lat); None stays None."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise dataloggers.ApiError(400, 'location must be {lat, lon}')
    return (_coordinate(raw.get('lon'), 'lon'), _coordinate(raw.get('lat'), 'lat'))


def _validated_observation(data: dict, attributes: set) -> dict:
    """Validate the observation keys present in `data`; absent keys stay absent.

    `observesFeature: null` is meaningful (no feature); null for the other keys means
    "not supplied". An empty `observationContext` means "none declared".
    """
    out = {}
    if 'observesFeature' in data:
        feature = data['observesFeature']
        if feature is not None and (not isinstance(feature, str)
                                    or not feature.startswith('urn:ngsi-ld:')):
            raise dataloggers.ApiError(400, "observesFeature must be an NGSI-LD URN")
        out['observesFeature'] = feature
    try:
        if data.get('observationContext') is not None:
            ctx = data['observationContext']
            out['observationContext'] = (
                {} if ctx == {} else validate_observation_context(ctx))
        if data.get('channelOverrides') is not None:
            out['channelOverrides'] = validate_channel_overrides(data['channelOverrides'], attributes)
    except ValueError as exc:
        raise dataloggers.ApiError(400, str(exc))
    if data.get('mobility') is not None:
        if data['mobility'] not in deployments.MOBILITIES:
            raise dataloggers.ApiError(400, "mobility must be 'fixed' or 'mobile'")
        out['mobility'] = data['mobility']
    return out


def _named_urns(obs: dict) -> list:
    """Every entity URN the request refers to, in order, without repeats."""
    urns = []
    if obs.get('observesFeature'):
        urns.append(obs['observesFeature'])
    contexts = [obs.get('observationContext') or {}]
    for override in (obs.get('channelOverrides') or {}).values():
        if override.get('observesFeature'):
            urns.append(override['observesFeature'])
        contexts.append(override.get('observationContext') or {})
    for ctx in contexts:
        urns.extend(ctx[f] for f in _URN_CONTEXT_FIELDS if ctx.get(f))
    return list(dict.fromkeys(urns))


@dataloggers.in_step('orion_read', 'Could not read the context broker')
def _orion_get_entity(tenant_id: str, urn: str) -> Optional[dict]:
    """Tenant-scoped read of the FULL entity (an `attrs` filter 404s on an existing one)."""
    resp = requests.get(
        f"{ORION_URL}/ngsi-ld/v1/entities/{urn}",
        headers=inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False),
        timeout=10)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise dataloggers.ApiError(502, 'Could not read the context broker', 'orion_read')
    return resp.json()


def _require_entities_exist(tenant_id: str, urns: list) -> None:
    for urn in urns:
        if _orion_get_entity(tenant_id, urn) is None:
            raise dataloggers.ApiError(400, f'{urn} does not exist for this tenant')


def _profile_attributes(tenant_id: str, device: dict) -> set:
    """Canonical sdmAttribute names of the device's profile."""
    code = _extract_prop_value(device, 'profileCode')
    row = _read_profile(tenant_id, code) if isinstance(code, str) and code else None
    if not row:
        raise dataloggers.ApiError(400, 'The device has no known profile')
    mapping = row.get('mapping')
    measurements = (mapping.get('measurements') or []) if isinstance(mapping, dict) else []
    return {m['sdmAttribute'] for m in measurements if m.get('sdmAttribute')}


def _observation_attrs(dep: dict) -> dict:
    """Orion attributes for a deployment: only those with a value, never null."""
    attrs = {'mobility': {'type': 'Property', 'value': dep['mobility']},
             'deploymentId': {'type': 'Property', 'value': dep['id']}}
    if dep.get('observesFeature'):
        attrs['observesFeature'] = {'type': 'Relationship', 'object': dep['observesFeature']}
    if dep.get('observationContext'):
        attrs['observationContext'] = {'type': 'Property', 'value': dep['observationContext']}
    if dep.get('channelOverrides'):
        attrs['channelOverrides'] = {'type': 'Property', 'value': dep['channelOverrides']}
    return attrs


@dataloggers.in_step('orion_mirror', _MIRROR_ERROR)
def _mirror_deployment(tenant_id: str, device_urn: str, dep: Optional[dict]) -> None:
    """Make the Device's observation attributes match `dep` (the current deployment).

    POST /attrs appends and overwrites (PATCH would 404 on a missing attribute and
    null is a 400); attributes the deployment lacks are deleted, a 404 there is fine.
    `dep=None` (the device has no current deployment) deletes all of them, deploymentId
    included. A deployment's location is mirrored to `location`; a deployment without
    one leaves `location` alone (mobile devices keep their last reported position).
    """
    base = f"{ORION_URL}/ngsi-ld/v1/entities/{device_urn}"
    if dep is None:
        attrs, removable = {}, _OPTIONAL_OBSERVATION_ATTRS + ('deploymentId',)
    else:
        attrs, removable = _observation_attrs(dep), _OPTIONAL_OBSERVATION_ATTRS
        loc = dep.get('location')
        if loc:
            attrs['location'] = {'type': 'GeoProperty', 'value': {
                'type': 'Point', 'coordinates': [loc['lon'], loc['lat']]}}
    if attrs:
        resp = requests.post(
            f"{base}/attrs", json=attrs,
            headers=inject_fiware_headers({'Content-Type': 'application/json'}, tenant=tenant_id,
                                          has_context_in_body=False),
            timeout=10)
        if resp.status_code not in (200, 204):
            raise dataloggers.ApiError(502, _MIRROR_ERROR, 'orion_mirror')
    for attr in removable:
        if attr in attrs:
            continue
        gone = requests.delete(
            f"{base}/attrs/{attr}",
            headers=inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False),
            timeout=10)
        if gone.status_code not in (200, 204, 404):
            raise dataloggers.ApiError(502, _MIRROR_ERROR, 'orion_mirror')


def _remirror_current(tenant_id: str, device_urn: str, failed_id: str, restored_id: str) -> None:
    """After a rollback, bring Orion back in line with the now-current deployment.

    The failed mirror may have written part of the new state (POST ok, a later DELETE
    failed). Best effort: if this fails too, log both ids and let the caller answer 502.
    """
    try:
        _mirror_deployment(tenant_id, device_urn,
                           deployments.current_deployment(tenant_id, device_urn))
    except Exception as exc:
        logger.error("orion mirror left diverged after rollback tenant=%s device=%s "
                     "failed_deployment=%s restored_deployment=%s error=%s",
                     tenant_id, device_urn, failed_id, restored_id, type(exc).__name__)


def _deployment_json(dep: dict) -> dict:
    out = dict(dep)
    for key in ('valid_from', 'valid_to'):
        if isinstance(out.get(key), datetime):
            out[key] = out[key].isoformat()
    return out


def _actor() -> str:
    return str(g.get('user_id') or g.get('user') or 'unknown')


def _is_current(dep: Optional[dict]) -> bool:
    if not dep:
        return False
    now = datetime.now(timezone.utc)
    return dep['valid_from'] <= now and (dep['valid_to'] is None or dep['valid_to'] > now)


@sensors_bp.route('/api/sensors/register', methods=['POST'])
@require_auth
@_guard
def register_sensor():
    """
    Register a sensor on a datalogger.

    Steps, each undone in reverse order if a later one fails (502 with the step):
    Orion entity, IoT Agent device, datalogger link row, broker ACL, then the
    Device -> DeviceMeasurement subscription (logged only if it fails).

    Request body:
    {
        "external_id": "BP_Vaso_PAR_1",
        "name": "BP Vaso PAR 1",
        "profile": "par_photon_flux",
        "location": {"lat": 42.57, "lon": -2.02},
        "controlledAsset": ["urn:ngsi-ld:AgriParcel:<id>", ...],   # owner first, >= 1 parcel
        "datalogger_id": "<uuid>",
        # optional observation fields (see deployments.py)
        "observesFeature": "urn:ngsi-ld:...", "observationContext": {"medium": "ambientAir"},
        "channelOverrides": {"<sdmAttribute>": {...}}, "mobility": "fixed|mobile",
        "validFrom": "2026-03-01T08:00:00+00:00"   # start of the first deployment, not in the future
    }
    """
    _require_installer()
    tenant_id = _tenant()
    data = _body()

    external_id = data.get('external_id')
    name = data.get('name')
    profile_code = data.get('profile')
    if not external_id or not name or not profile_code or not data.get('datalogger_id'):
        raise dataloggers.ApiError(
            400, 'Missing required fields: external_id, name, profile, datalogger_id')
    if not isinstance(external_id, str) or not dataloggers.EXTERNAL_ID_RE.fullmatch(external_id):
        raise dataloggers.ApiError(400, 'external_id must match [A-Za-z0-9_.-]{1,64}')

    location = data.get('location') or {}
    lat = _coordinate(location.get('lat'), 'lat')
    lon = _coordinate(location.get('lon'), 'lon')
    parcels = _parcel_links(data.get('controlledAsset'))
    if (not parcels
            or not all(u.startswith('urn:ngsi-ld:') for u in parcels)
            or not any(u.startswith('urn:ngsi-ld:AgriParcel:') for u in parcels)):
        raise dataloggers.ApiError(
            400, 'controlledAsset must hold NGSI-LD URNs and at least one AgriParcel.')

    valid_from = _past_instant(data.get('validFrom'), 'validFrom')

    # Reads and validations first: nothing is written until all of them pass.
    datalogger = dataloggers.require_usable_datalogger(tenant_id, data['datalogger_id'])

    profile_row = _read_profile(tenant_id, profile_code)
    if not profile_row:
        raise dataloggers.ApiError(404, f'Profile "{profile_code}" not found')

    sdm_entity_type = profile_row.get('sdm_entity_type') or 'Device'
    # tractor / implement / robot / harvester for machines, NULL for devices: the
    # one attribute that tells machines of the same type apart.
    device_category = profile_row.get('sdm_device_category')
    profile_mapping = profile_row.get('mapping')
    measurements = (profile_mapping.get('measurements') or []) if isinstance(profile_mapping, dict) else []
    try:
        dataloggers.iot_agent.build_iota_attributes(measurements)
    except (KeyError, TypeError, AttributeError):
        raise dataloggers.ApiError(400, 'Profile measurements need type and sdmAttribute')

    obs = _validated_observation(
        data, {m['sdmAttribute'] for m in measurements if m.get('sdmAttribute')})
    _require_entities_exist(tenant_id, _named_urns(obs))

    orion_entity_id = f"urn:ngsi-ld:{sdm_entity_type}:{tenant_id}:{external_id}"
    if dataloggers.device_link(tenant_id, orion_entity_id):
        raise dataloggers.ApiError(409, 'Device is already on a datalogger')

    orion_check = _orion_dedup_check(tenant_id, sdm_entity_type, external_id)
    if orion_check.status_code == 200 and orion_check.json():
        existing = orion_check.json()[0]
        return jsonify({
            'error': f'Sensor with external_id "{external_id}" already exists',
            'sensor': {'id': existing.get('id'),
                       'external_id': _extract_prop_value(existing, 'externalId'),
                       'name': _extract_prop_value(existing, 'name')},
        }), 409

    if deployments.list_deployments(tenant_id, orion_entity_id):
        # A leftover or foreign history: opening a deployment would close it, and the
        # undo would delete it.
        raise dataloggers.ApiError(409, f'Sensor "{external_id}" already has a deployment history')

    metadata = data.get('metadata', {})
    if data.get('station_id'):
        metadata['group'] = data['station_id']
        metadata['station_id'] = data['station_id']

    orion_entity = {
        '@context': [CONTEXT_URL],
        'id': orion_entity_id,
        'type': sdm_entity_type,
        'name': {'type': 'Property', 'value': name},
        'location': {'type': 'GeoProperty',
                     'value': {'type': 'Point', 'coordinates': [lon, lat]}},
        'externalId': {'type': 'Property', 'value': external_id},
        'sensorType': {'type': 'Property', 'value': profile_code},
        'profileCode': {'type': 'Property', 'value': profile_code},
        'installedAt': {'type': 'Property', 'value': _zulu(valid_from)},
        'status': {'type': 'Property', 'value': 'active'},
        # controlledAsset is the SDM device->asset relationship; first entry is the owner.
        'controlledAsset': {'type': 'Relationship', 'object': parcels},
    }
    if device_category:
        orion_entity['category'] = {'type': 'Property', 'value': device_category}
    if metadata:
        orion_entity['metadata'] = {'type': 'Property', 'value': metadata}
    if data.get('is_under_canopy'):
        orion_entity['isUnderCanopy'] = {'type': 'Property', 'value': True}
    if data.get('station_id'):
        orion_entity['stationId'] = {'type': 'Property', 'value': data['station_id']}
    if data.get('altitude_meters'):
        orion_entity['altitudeMeters'] = {'type': 'Property', 'value': data['altitude_meters']}
    for attr in _HEALTH_ATTRIBUTES:
        if attr in data and data[attr] is not None:
            orion_entity[attr] = {'type': 'Property', 'value': data[attr]}

    apikey = dataloggers.get_apikey(tenant_id)
    spec = deployments.DeploymentSpec(
        observes_feature=obs.get('observesFeature'),
        observation_context=obs.get('observationContext', {}),
        channel_overrides=obs.get('channelOverrides', {}),
        mobility=obs.get('mobility', 'fixed'),
        location=(lon, lat))

    undo = dataloggers.UndoStack()
    try:
        # Opened before the entity so the Orion body already carries deploymentId; its
        # undo goes on the stack at once, so any later failure removes it (last).
        first = deployments.open_deployment(tenant_id, orion_entity_id, spec, valid_from,
                                            _actor(), 'registration')
        undo.push('deployment', lambda: deployments.delete_deployments(tenant_id, orion_entity_id))
        orion_entity.update(_observation_attrs(first))

        orion_response = _orion_create(tenant_id, orion_entity)
        if orion_response.status_code == 409:
            # Not ours: never compensate (delete) an entity this request did not create.
            raise dataloggers.ApiError(409, f'Sensor "{external_id}" already exists')
        if orion_response.status_code not in (200, 201):
            logger.error("Orion rejected sensor %s: %s", external_id, orion_response.status_code)
            raise dataloggers.ApiError(502, 'Failed to create sensor entity in context broker',
                                       'orion_entity')
        undo.push('orion_entity', lambda: dataloggers.delete_orion_entity(tenant_id, orion_entity_id))

        dataloggers.provision_iota_device(tenant_id, apikey, external_id, orion_entity_id,
                                          sdm_entity_type, measurements)
        undo.push('iota_device', lambda: dataloggers.delete_iota_device(tenant_id, external_id))

        dataloggers.link_device(tenant_id, datalogger['id'], orion_entity_id, external_id)
        undo.push('link', lambda: dataloggers.undo_link(tenant_id, orion_entity_id))

        acl = dataloggers.reconcile_datalogger_acl(tenant_id, datalogger['id'])

        _ensure_bridge_subscription(tenant_id)
    except Exception:
        undo.unwind()
        raise

    logger.info("Registered sensor %s for tenant %s on datalogger %s",
                external_id, tenant_id, datalogger['id'])
    body = {
        'entity_id': orion_entity_id,
        'topic': dataloggers.topic_for(apikey, external_id),
        'datalogger_id': datalogger['id'],
    }
    if isinstance(acl, dict) and acl.get('needs_rotation'):
        body['needs_rotation'] = True
    return jsonify(body), 201


# =============================================================================
# Dataloggers
# =============================================================================


@sensors_bp.route('/api/sensors/dataloggers', methods=['POST'])
@require_auth
@_guard
def create_datalogger():
    _require_installer()
    tenant = _tenant()
    data = _body()
    name = data.get('name')
    kind = data.get('kind')
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise dataloggers.ApiError(400, 'name is required')
    if kind not in dataloggers.KINDS:
        raise dataloggers.ApiError(400, f'kind must be one of {", ".join(dataloggers.KINDS)}')
    created = dataloggers.create_datalogger(tenant, name.strip(), kind)
    return jsonify({'id': created['id'], 'mqtt': created['mqtt'], 'devices': []}), 201


@sensors_bp.route('/api/sensors/dataloggers', methods=['GET'])
@require_auth
@_guard
def list_dataloggers():
    return jsonify(dataloggers.list_dataloggers(_tenant())), 200


@sensors_bp.route('/api/sensors/dataloggers/<datalogger_id>/config', methods=['GET'])
@require_auth
@_guard
def datalogger_config(datalogger_id):
    _require_installer()
    return jsonify(dataloggers.get_datalogger_config(_tenant(), datalogger_id)), 200


@sensors_bp.route('/api/sensors/dataloggers/<datalogger_id>/rotate', methods=['POST'])
@require_auth
@_guard
def rotate_datalogger(datalogger_id):
    _require_installer()
    return jsonify(dataloggers.rotate_datalogger(_tenant(), datalogger_id)), 200


@sensors_bp.route('/api/sensors/dataloggers/<datalogger_id>/revoke', methods=['POST'])
@require_auth
@_guard
def revoke_datalogger(datalogger_id):
    _require_installer()
    dataloggers.set_datalogger_status(_tenant(), datalogger_id, revoke=True)
    return jsonify({'status': 'revoked'}), 200


@sensors_bp.route('/api/sensors/dataloggers/<datalogger_id>/enable', methods=['POST'])
@require_auth
@_guard
def enable_datalogger(datalogger_id):
    _require_installer()
    dataloggers.set_datalogger_status(_tenant(), datalogger_id, revoke=False)
    return jsonify({'status': 'active'}), 200


@sensors_bp.route('/api/sensors/dataloggers/<datalogger_id>', methods=['DELETE'])
@require_auth
@_guard
def delete_datalogger(datalogger_id):
    _require_installer()
    dataloggers.delete_datalogger(_tenant(), datalogger_id)
    return '', 204


@sensors_bp.route('/api/sensors/devices/<path:device_urn>/datalogger', methods=['PUT'])
@require_auth
@_guard
def move_device(device_urn):
    _require_installer()
    raw = _body().get('datalogger_id')
    try:
        target = str(uuid.UUID(str(raw)))
    except (ValueError, AttributeError, TypeError):
        raise dataloggers.ApiError(400, 'datalogger_id must be a valid id')
    dataloggers.move_device(_tenant(), device_urn, target)
    return jsonify({'entity_id': device_urn, 'datalogger_id': target}), 200


@sensors_bp.route('/api/sensors/devices/<path:device_urn>/profile', methods=['PUT'])
@require_auth
@_guard
def change_device_profile(device_urn):
    _require_installer()
    code = _body().get('profile')
    if not isinstance(code, str) or not code:
        raise dataloggers.ApiError(400, 'profile is required')
    dataloggers.change_device_profile(_tenant(), device_urn, code)
    return jsonify({'entity_id': device_urn, 'profile': code}), 200


@sensors_bp.route('/api/sensors/devices/<path:device_urn>', methods=['DELETE'])
@require_auth
@_guard
def delete_sensor_device(device_urn):
    _require_installer()
    dataloggers.delete_device(_tenant(), device_urn)
    return '', 204


# =============================================================================
# Device deployments
# =============================================================================


@sensors_bp.route('/api/sensors/devices/<path:device_urn>/deployments', methods=['GET'])
@require_auth
@_guard
def list_device_deployments(device_urn):
    _require_installer()
    found = deployments.list_deployments(_tenant(), device_urn)
    return jsonify({'deployments': [_deployment_json(d) for d in found]}), 200


@sensors_bp.route('/api/sensors/devices/<path:device_urn>/deployments', methods=['POST'])
@require_auth
@_guard
def move_device_deployment(device_urn):
    """Close the current deployment and open a new one; absent fields are copied."""
    _require_installer()
    tenant_id = _tenant()
    data = _body()
    reason = _reason(data)
    valid_from = _past_instant(data.get('validFrom'), 'validFrom')

    device = _orion_get_entity(tenant_id, device_urn)
    if device is None:
        raise dataloggers.ApiError(404, 'Device not found')
    attributes = _profile_attributes(tenant_id, device) if data.get('channelOverrides') else set()
    supplied = _validated_observation(data, attributes)
    _require_entities_exist(tenant_id, _named_urns(supplied))

    current = deployments.current_deployment(tenant_id, device_urn)
    if 'location' in data:
        location = _location_pair(data['location'])
    elif current and current.get('location'):
        location = (current['location']['lon'], current['location']['lat'])
    else:
        location = None
    mobility = supplied.get('mobility') or (current['mobility'] if current else 'fixed')
    if mobility == 'fixed' and location is None:
        raise dataloggers.ApiError(400, 'location is required for a fixed deployment')

    def pick(key, default):
        if key in supplied:
            return supplied[key]
        return current[key] if current else default

    spec = deployments.DeploymentSpec(
        observes_feature=pick('observesFeature', None),
        observation_context=pick('observationContext', {}),
        channel_overrides=pick('channelOverrides', {}),
        mobility=mobility, location=location)
    actor = _actor()
    created = deployments.open_deployment(tenant_id, device_urn, spec, valid_from, actor, reason)
    try:
        _mirror_deployment(tenant_id, device_urn, created)
    except Exception:
        logger.error("deployment mirror failed, reverting tenant=%s device=%s deployment=%s",
                     tenant_id, device_urn, created['id'])
        try:
            deployments.revert_open(tenant_id, created['id'], actor)
        except Exception as exc:
            logger.critical("deployment revert failed tenant=%s deployment=%s error=%s",
                            tenant_id, created['id'], type(exc).__name__)
        else:
            _remirror_current(tenant_id, device_urn, created['id'],
                              current['id'] if current else 'none')
        raise
    return jsonify(_deployment_json(created)), 201


_CORRECTION_KEYS = {'validFrom': 'valid_from', 'validTo': 'valid_to',
                    'observesFeature': 'observesFeature',
                    'observationContext': 'observation_context',
                    'channelOverrides': 'channel_overrides', 'location': 'location'}


@sensors_bp.route('/api/sensors/deployments/<deployment_id>', methods=['PUT'])
@require_auth
@_guard
def correct_device_deployment(deployment_id):
    """Fix a deployment in place; only the current one is mirrored to the Device."""
    _require_installer()
    tenant_id = _tenant()
    data = _body()
    reason = _reason(data)
    unknown = sorted(set(data) - set(_CORRECTION_KEYS) - {'reason'})
    if unknown:
        raise dataloggers.ApiError(
            400, f"cannot correct: {', '.join(unknown)} (open a new deployment to change mobility)")
    if not any(k in data for k in _CORRECTION_KEYS):
        raise dataloggers.ApiError(400, 'No changes given')

    before = deployments.get_deployment(tenant_id, deployment_id)
    if before is None:
        raise dataloggers.ApiError(404, 'Deployment not found')
    device_urn = before['device_urn']

    attributes = set()
    if data.get('channelOverrides'):
        device = _orion_get_entity(tenant_id, device_urn)
        if device is None:
            raise dataloggers.ApiError(404, 'Device not found')
        attributes = _profile_attributes(tenant_id, device)
    supplied = _validated_observation(data, attributes)
    _require_entities_exist(tenant_id, _named_urns(supplied))

    changes = {}
    if 'validFrom' in data:
        changes['valid_from'] = _parse_instant(data['validFrom'], 'validFrom')
    if 'validTo' in data:
        changes['valid_to'] = (None if data['validTo'] is None
                               else _parse_instant(data['validTo'], 'validTo'))
    if 'observesFeature' in supplied:
        changes['observesFeature'] = supplied['observesFeature']
    if 'observationContext' in supplied:
        changes['observation_context'] = supplied['observationContext']
    if 'channelOverrides' in supplied:
        changes['channel_overrides'] = supplied['channelOverrides']
    if 'location' in data:
        location = _location_pair(data['location'])
        if location is None and before['mobility'] == 'fixed':
            raise dataloggers.ApiError(400, 'location is required for a fixed deployment')
        changes['location'] = location
    if not changes:
        raise dataloggers.ApiError(400, 'No changes given')

    actor = _actor()
    after = deployments.correct_deployment(tenant_id, deployment_id, changes, actor, reason)

    touched = _is_current(after) or _is_current(before)
    if touched:
        # No current deployment left ("sensor removed"): the mirror clears the attributes.
        target = after if _is_current(after) else deployments.current_deployment(
            tenant_id, device_urn)
        try:
            _mirror_deployment(tenant_id, device_urn, target)
        except Exception:
            logger.error("deployment mirror failed, restoring tenant=%s deployment=%s",
                         tenant_id, deployment_id)
            try:
                deployments.correct_deployment(
                    tenant_id, deployment_id,
                    {'valid_from': before['valid_from'], 'valid_to': before['valid_to'],
                     'observesFeature': before['observesFeature'],
                     'observation_context': before['observationContext'],
                     'channel_overrides': before['channelOverrides'],
                     'location': (None if not before['location']
                                  else (before['location']['lon'], before['location']['lat']))},
                    actor, 'rollback: orion mirror failed')
            except Exception as exc:
                logger.critical("deployment restore failed tenant=%s deployment=%s error=%s",
                                tenant_id, deployment_id, type(exc).__name__)
            else:
                _remirror_current(tenant_id, device_urn, deployment_id, deployment_id)
            raise
    return jsonify(_deployment_json(after)), 200


# =============================================================================
# Sensor Profiles
# =============================================================================


_PROFILE_CODE = re.compile(r'[A-Za-z0-9_.-]{1,64}')
_VALUE_TYPES = ('Number', 'Text')


def _validated_profile(data: dict, tenant_id: str) -> tuple:
    """(name, description, mapping) of a profile body, with `sdmAttribute` canonicalized."""
    name = data.get('name')
    if not isinstance(name, str) or not name.strip():
        raise dataloggers.ApiError(400, 'name is required')
    description = data.get('description')
    if description is not None and not isinstance(description, str):
        raise dataloggers.ApiError(400, 'description must be text')
    if data.get('sdm_entity_type', 'Device') != 'Device':
        raise dataloggers.ApiError(400, 'sdm_entity_type must be Device')
    mapping = data.get('mapping')
    raw = mapping.get('measurements') if isinstance(mapping, dict) else None
    if not isinstance(raw, list) or not raw:
        raise dataloggers.ApiError(400, 'mapping.measurements must be a non-empty list')
    keys = {(m.get('incomingKey') or m.get('type')) for m in raw
            if isinstance(m, dict) and isinstance(m.get('incomingKey') or m.get('type'), str)}
    measurements, seen = [], set()
    for m in raw:
        if not isinstance(m, dict):
            raise dataloggers.ApiError(400, 'Each measurement must be an object')
        for field in ('type', 'sdmAttribute'):
            if not isinstance(m.get(field), str) or not m[field].strip():
                raise dataloggers.ApiError(400, f'Measurement {field} is required')
        unit = m.get('unit', '')
        if not isinstance(unit, str):
            raise dataloggers.ApiError(400, 'Measurement unit must be text')
        try:
            to_unit_code(unit)
        except ValueError:
            raise dataloggers.ApiError(400, f'Unknown unit "{unit}"')
        if m.get('valueType') is not None and m['valueType'] not in _VALUE_TYPES:
            raise dataloggers.ApiError(400, 'valueType must be Number or Text')
        for field in ('incomingKey', 'expression'):
            if m.get(field) is not None and not isinstance(m[field], str):
                raise dataloggers.ApiError(400, f'Measurement {field} must be text')
        if m.get('expression'):
            ok, why = validate_jexl_expression(m['expression'], variables=keys)
            if not ok:
                raise dataloggers.ApiError(400, f'Invalid expression: {why}')
        observed = m.get('observedProperty')
        counter_max = m.get('counterMax')
        if observed is not None:
            if not isinstance(observed, str) or not observed.strip():
                raise dataloggers.ApiError(400, 'observedProperty must be text')
            observed = observed.strip()
            prop = observation_catalogue.get_property(tenant_id, observed)
            if prop is None:
                raise dataloggers.ApiError(400, f'Unknown observedProperty "{observed}"')
        if 'counterMax' in m:
            if observed is None:
                raise dataloggers.ApiError(400, 'counterMax requires observedProperty')
            if (isinstance(counter_max, bool) or not isinstance(counter_max, (int, float))
                    or not math.isfinite(counter_max) or counter_max <= 0):
                raise dataloggers.ApiError(400, 'counterMax must be a positive number')
            if prop['temporal_semantics'] != 'cumulative':
                raise dataloggers.ApiError(
                    400, f'counterMax only applies to cumulative properties, "{observed}" is not')
        attribute = canonical_attribute(m['sdmAttribute'].strip())
        if attribute in seen:
            raise dataloggers.ApiError(400, f'Duplicate sdmAttribute "{attribute}"')
        seen.add(attribute)
        clean = {'type': m['type'].strip(), 'sdmAttribute': attribute, 'unit': unit}
        for field in ('incomingKey', 'valueType', 'expression'):
            if m.get(field):
                clean[field] = m[field]
        if observed is not None:
            clean['observedProperty'] = observed
        if 'counterMax' in m:
            clean['counterMax'] = counter_max
        measurements.append(clean)
    return name.strip(), description, {**mapping, 'measurements': measurements}


@dataloggers.in_step('db', 'Database error')
def _profile_visible(tenant_id: str, code: str):
    with get_db_connection_with_tenant(tenant_id) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT tenant_id FROM sensor_profiles
            WHERE code = %s AND (tenant_id IS NULL OR tenant_id = %s)
            ORDER BY tenant_id NULLS LAST
            LIMIT 1
        """, (code, tenant_id))
        row = cur.fetchone()
        cur.close()
    return row


@sensors_bp.route('/api/sensors/profiles', methods=['POST'])
@require_auth
@_guard
def create_sensor_profile():
    tenant = _tenant()
    data = _body()
    code = data.get('code')
    if not isinstance(code, str) or not _PROFILE_CODE.fullmatch(code):
        raise dataloggers.ApiError(400, 'Invalid code')
    name, description, mapping = _validated_profile(data, tenant)
    if _profile_visible(tenant, code):
        raise dataloggers.ApiError(409, 'Profile code already exists')
    try:
        with get_db_connection_with_tenant(tenant) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO sensor_profiles (tenant_id, code, name, description, sdm_entity_type, mapping)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (tenant, code, name, description, 'Device', Json(mapping)))
            conn.commit()
            cur.close()
    except psycopg2.errors.UniqueViolation:
        raise dataloggers.ApiError(409, 'Profile code already exists')
    except Exception as exc:
        logger.error("profile insert failed: %s", type(exc).__name__)
        raise dataloggers.ApiError(502, 'Database error', 'db')
    return jsonify({'code': code, 'name': name, 'sdm_entity_type': 'Device',
                    'mapping': mapping}), 201


@sensors_bp.route('/api/sensors/profiles/<code>', methods=['PUT'])
@require_auth
@_guard
def update_sensor_profile(code):
    tenant = _tenant()
    data = _body()
    if data.get('code', code) != code:
        raise dataloggers.ApiError(400, 'code in body does not match the URL')
    name, description, mapping = _validated_profile(data, tenant)
    row = _profile_visible(tenant, code)
    if not row:
        raise dataloggers.ApiError(404, 'Profile not found')
    if row.get('tenant_id') is None:
        raise dataloggers.ApiError(403, 'Global profiles are read-only')
    try:
        with get_db_connection_with_tenant(tenant) as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE sensor_profiles SET name = %s, description = %s, mapping = %s
                WHERE code = %s AND tenant_id = %s
            """, (name, description, Json(mapping), code, tenant))
            updated = cur.rowcount
            conn.commit()
            cur.close()
    except Exception as exc:
        logger.error("profile update failed: %s", type(exc).__name__)
        raise dataloggers.ApiError(502, 'Database error', 'db')
    if not updated:
        raise dataloggers.ApiError(404, 'Profile not found')
    result = {'reprovisioned': [], 'failed': []}
    try:
        result = dataloggers.reprovision_profile_devices(
            tenant, code, mapping.get('measurements') or [])
    except Exception as exc:
        # The profile row is saved; the devices keep their old attributes until re-saved.
        logger.error("profile %s: devices could not be listed for re-provisioning: %s",
                     code, type(exc).__name__)
        result['error'] = 'device_listing_failed'
    return jsonify({'code': code, 'name': name, 'mapping': mapping, **result}), 200


@sensors_bp.route('/api/sensors/profiles', methods=['GET'])
@require_auth
def list_sensor_profiles():
    """List available sensor profiles"""
    try:
        tenant_id = g.tenant

        with get_db_connection_with_tenant(tenant_id) as conn:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("""
                    SELECT code, name, description, sdm_entity_type, sdm_category, mapping, metadata, health_defaults
                    FROM sensor_profiles
                    WHERE tenant_id IS NULL OR tenant_id = %s
                    ORDER BY code
                """, (tenant_id,))

                profiles = []
                for row in cur.fetchall():
                    profile_data = {
                        'code': row['code'],
                        'name': row['name'],
                        'description': row['description'],
                        'sdm_entity_type': row['sdm_entity_type'],
                        'sdm_category': row['sdm_category']
                    }

                    # Include metadata if available
                    if row.get('metadata'):
                        profile_data['metadata'] = row['metadata']

                    # Include health_defaults if available
                    if row.get('health_defaults'):
                        profile_data['health_defaults'] = row['health_defaults']

                    # Include mapping info for frontend hints
                    if row.get('mapping'):
                        mapping = row['mapping']
                        if isinstance(mapping, dict):
                            measurements = mapping.get('measurements', [])
                            if measurements:
                                # Extract SDM attributes for hints
                                sdm_attributes = [m.get('sdmAttribute') for m in measurements if m.get('sdmAttribute')]
                                if sdm_attributes:
                                    profile_data['sdm_attributes'] = sdm_attributes

                    profiles.append(profile_data)

                cur.close()

                return jsonify({
                    'profiles': profiles
                }), 200

            except Exception as e:
                logger.error(f"Error listing profiles: {e}")
                return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in list_sensor_profiles: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@sensors_bp.route('/api/sensors/profiles/status', methods=['GET'])
@require_auth
def sensor_profiles_status():
    """Check if sensor profiles are initialized"""
    try:
        tenant_id = g.tenant

        with get_db_connection_with_tenant(tenant_id) as conn:
            try:
                cur = conn.cursor()

                # Count global profiles (tenant_id IS NULL)
                cur.execute("SELECT COUNT(*) FROM sensor_profiles WHERE tenant_id IS NULL")
                global_count = cur.fetchone()[0] or 0

                # Count tenant-specific profiles
                cur.execute("SELECT COUNT(*) FROM sensor_profiles WHERE tenant_id = %s", (tenant_id,))
                tenant_count = cur.fetchone()[0] or 0

                cur.close()

                return jsonify({
                    'initialized': global_count > 0,
                    'global_profiles': global_count,
                    'tenant_profiles': tenant_count,
                    'total': global_count + tenant_count
                }), 200

            except Exception as e:
                logger.error(f"Error checking profiles status: {e}")
                return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in sensor_profiles_status: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# =============================================================================
# Sensor List
# =============================================================================


@sensors_bp.route('/api/sensors', methods=['GET'])
@require_auth
def list_tenant_sensors():
    """List sensors for the current tenant"""
    try:
        tenant_id = g.tenant

        with get_db_connection_with_tenant(tenant_id) as conn:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("""
                    SELECT
                        s.id,
                        s.external_id,
                        s.name,
                        sp.code as profile_code,
                        sp.name as profile_name,
                        s.is_under_canopy,
                        s.metadata,
                        s.created_at,
                        ST_X(s.installation_location::geometry) as lon,
                        ST_Y(s.installation_location::geometry) as lat,
                        (SELECT MAX(observed_at) FROM telemetry_events
                         WHERE tenant_id = %s AND device_id = s.external_id) as last_telemetry
                    FROM sensors s
                    JOIN sensor_profiles sp ON s.profile_id = sp.id
                    WHERE s.tenant_id = %s
                    ORDER BY s.created_at DESC
                """, (tenant_id, tenant_id))

                sensors = []
                for row in cur.fetchall():
                    sensor_data = {
                        'id': str(row['id']),
                        'external_id': row['external_id'],
                        'name': row['name'],
                        'profile': {
                            'code': row['profile_code'],
                            'name': row['profile_name']
                        },
                        'is_under_canopy': row['is_under_canopy'],
                        'metadata': row['metadata'],
                        'created_at': row['created_at'].isoformat(),
                        'last_telemetry': row['last_telemetry'].isoformat() if row['last_telemetry'] else None
                    }

                    # Add location if coordinates are available
                    if row['lon'] is not None and row['lat'] is not None:
                        sensor_data['installation_location'] = {
                            'lon': float(row['lon']),
                            'lat': float(row['lat'])
                        }

                    sensors.append(sensor_data)

                cur.close()

                return jsonify({
                    'sensors': sensors
                }), 200

            except Exception as e:
                logger.error(f"Error listing sensors: {e}")
                return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in list_tenant_sensors: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# =============================================================================
# Device Telemetry Endpoints
# =============================================================================


@sensors_bp.route('/api/devices/<device_id>/telemetry', methods=['GET'])
@require_auth
def get_device_telemetry(device_id):
    """Get telemetry history for a device"""
    try:
        device_id = _normalize_device_id(device_id)
        tenant_id = g.tenant

        # Get query parameters
        start_time = request.args.get('start_time')
        end_time = request.args.get('end_time')
        limit = int(request.args.get('limit', 1000))

        with get_db_connection_with_tenant(tenant_id) as conn:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)

                query = """
                    SELECT
                        observed_at,
                        payload,
                        metadata
                    FROM telemetry_events
                    WHERE tenant_id = %s AND device_id = %s
                """
                params = [tenant_id, device_id]

                if start_time:
                    query += " AND observed_at >= %s"
                    params.append(start_time)
                if end_time:
                    query += " AND observed_at <= %s"
                    params.append(end_time)

                query += " ORDER BY observed_at DESC LIMIT %s"
                params.append(limit)

                cur.execute(query, params)

                telemetry = []
                for row in cur.fetchall():
                    telemetry.append({
                        'observed_at': row['observed_at'].isoformat(),
                        'payload': row['payload'],
                        'metadata': row['metadata']
                    })

                cur.close()

                return jsonify({
                    'device_id': device_id,
                    'telemetry': telemetry,
                    'count': len(telemetry)
                }), 200

            except Exception as e:
                logger.error(f"Error getting telemetry: {e}")
                return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in get_device_telemetry: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@sensors_bp.route('/api/devices/<device_id>/telemetry/latest', methods=['GET'])
@require_auth
def get_device_latest_telemetry(device_id):
    """Get latest telemetry value for a device"""
    try:
        device_id = _normalize_device_id(device_id)
        tenant_id = g.tenant

        with get_db_connection_with_tenant(tenant_id) as conn:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("""
                    SELECT
                        observed_at,
                        payload,
                        metadata
                    FROM telemetry_events
                    WHERE tenant_id = %s AND device_id = %s
                    ORDER BY observed_at DESC
                    LIMIT 1
                """, (tenant_id, device_id))

                row = cur.fetchone()
                cur.close()

                if row:
                    return jsonify({
                        'device_id': device_id,
                        'observed_at': row['observed_at'].isoformat(),
                        'payload': row['payload'],
                        'metadata': row['metadata']
                    }), 200
                else:
                    return jsonify({
                        'device_id': device_id,
                        'message': 'No telemetry data available'
                    }), 404

            except Exception as e:
                logger.error(f"Error getting latest telemetry: {e}")
                return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in get_device_latest_telemetry: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@sensors_bp.route('/api/devices/<device_id>/telemetry/stats', methods=['GET'])
@require_auth
def get_device_telemetry_stats(device_id):
    """Get aggregated statistics for device telemetry"""
    try:
        device_id = _normalize_device_id(device_id)
        tenant_id = g.tenant

        # Get query parameters
        start_time = request.args.get('start_time')
        end_time = request.args.get('end_time')

        with get_db_connection_with_tenant(tenant_id) as conn:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)

                query = """
                    SELECT
                        COUNT(*) as total_records,
                        MIN(observed_at) as first_record,
                        MAX(observed_at) as last_record
                    FROM telemetry_events
                    WHERE tenant_id = %s AND device_id = %s
                """
                params = [tenant_id, device_id]

                if start_time:
                    query += " AND observed_at >= %s"
                    params.append(start_time)
                if end_time:
                    query += " AND observed_at <= %s"
                    params.append(end_time)

                cur.execute(query, params)
                row = cur.fetchone()

                cur.close()

                return jsonify({
                    'device_id': device_id,
                    'stats': {
                        'total_records': row['total_records'],
                        'first_record': row['first_record'].isoformat() if row['first_record'] else None,
                        'last_record': row['last_record'].isoformat() if row['last_record'] else None
                    }
                }), 200

            except Exception as e:
                logger.error(f"Error getting telemetry stats: {e}")
                return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in get_device_telemetry_stats: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# =============================================================================
# Device Commands Endpoints
# =============================================================================


def get_mqtt_client():
    """Get or create MQTT client (thread-safe)"""
    if not hasattr(get_mqtt_client, '_client') or get_mqtt_client._client is None:
        client = mqtt.Client(client_id=f"entity-manager-{secrets.token_hex(8)}")
        if MQTT_USERNAME and MQTT_PASSWORD:
            client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                logger.info("MQTT client connected successfully")
            else:
                logger.error(f"MQTT connection failed with code {rc}")

        def on_disconnect(client, userdata, rc):
            logger.warning(f"MQTT client disconnected (rc={rc})")

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect

        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            client.loop_start()
            get_mqtt_client._client = client
        except Exception as e:
            logger.error(f"Failed to connect MQTT client: {e}")
            get_mqtt_client._client = None

    return get_mqtt_client._client


get_mqtt_client._client = None


@sensors_bp.route('/api/devices/<device_id>/commands', methods=['POST'])
@require_auth
def send_device_command(device_id):
    """Send a command to a device via MQTT"""
    try:
        tenant_id = g.tenant
        data = request.get_json()

        if not data:
            return jsonify({'error': 'Request body is required'}), 400

        command_type = data.get('command_type', 'custom')
        payload = data.get('payload', {})

        if not isinstance(payload, dict):
            return jsonify({'error': 'Payload must be a JSON object'}), 400

        # Get device info to determine MQTT topic
        try:
            with get_db_connection_with_tenant(tenant_id) as conn:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("""
                    SELECT external_id
                    FROM sensors
                    WHERE tenant_id = %s AND external_id = %s
                    LIMIT 1
                """, (tenant_id, device_id))
                device = cur.fetchone()
                cur.close()

            if not device:
                return jsonify({'error': 'Device not found'}), 404

            # Determine MQTT topic for commands
            mqtt_topic = f"{tenant_id}/{device_id}/cmd"

            # ── Create DeviceCommand entity in Orion-LD ──────────────────
            command_id = str(uuid.uuid4())
            command_entity_id = (
                f"urn:ngsi-ld:DeviceCommand:{tenant_id}:{command_id}"
            )
            now_iso = datetime.utcnow().isoformat()

            command_entity = {
                '@context': [CONTEXT_URL],
                'id': command_entity_id,
                'type': 'DeviceCommand',
                'commandId': {'type': 'Property', 'value': command_id},
                'commandType': {
                    'type': 'Property',
                    'value': command_type,
                },
                'targetDeviceId': {
                    'type': 'Property',
                    'value': device_id,
                },
                'mqttTopic': {
                    'type': 'Property',
                    'value': mqtt_topic,
                },
                'payload': {
                    'type': 'Property',
                    'value': payload,
                },
                'status': {'type': 'Property', 'value': 'pending'},
                'sentAt': {'type': 'Property', 'value': now_iso},
            }

            orion_headers = inject_fiware_headers(
                {'Content-Type': 'application/ld+json'}, tenant=tenant_id, has_context_in_body=True
            )
            orion_resp = requests.post(
                f"{ORION_URL}/ngsi-ld/v1/entities",
                json=command_entity,
                headers=orion_headers,
                timeout=10,
            )
            if orion_resp.status_code not in (200, 201):
                logger.error(
                    "Failed to create DeviceCommand in Orion-LD: %s",
                    orion_resp.text,
                )
                return jsonify({
                    'error': 'Failed to create command record'
                }), 502

            # Publish command to MQTT
            mqtt_client = get_mqtt_client()
            if not mqtt_client:
                # ── Update DeviceCommand status to failed via Orion-LD ──
                _patch_command_status(
                    tenant_id,
                    command_entity_id,
                    'failed',
                    {'error': 'MQTT client not available'},
                )
                return jsonify({'error': 'MQTT service unavailable'}), 503

            # Publish command
            command_message = {
                'command_id': command_id,
                'command_type': command_type,
                'payload': payload,
                'timestamp': now_iso,
            }

            try:
                result = mqtt_client.publish(
                    mqtt_topic,
                    json.dumps(command_message),
                    qos=1,
                )

                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    # ── Update DeviceCommand status to sent via Orion-LD ──
                    _patch_command_status(
                        tenant_id, command_entity_id, 'sent'
                    )

                    return jsonify({
                        'success': True,
                        'command_id': command_id,
                        'mqtt_topic': mqtt_topic,
                        'status': 'sent',
                        'sent_at': now_iso,
                    }), 201
                else:
                    _patch_command_status(
                        tenant_id,
                        command_entity_id,
                        'failed',
                        {
                            'error': (
                                f'MQTT publish failed with code '
                                f'{result.rc}'
                            )
                        },
                    )
                    return jsonify({
                        'error': (
                            f'Failed to publish command: '
                            f'MQTT error {result.rc}'
                        )
                    }), 500

            except Exception as mqtt_error:
                logger.error("MQTT publish error: %s", mqtt_error)
                _patch_command_status(
                    tenant_id,
                    command_entity_id,
                    'failed',
                    {'error': str(mqtt_error)},
                )
                return jsonify({
                    'error': f'Failed to publish command: {str(mqtt_error)}'
                }), 500

        except Exception as e:
            logger.error("Error sending command: %s", e)
            return jsonify({'error': 'Internal server error'}), 500

    except Exception as e:
        logger.error(f"Error in send_device_command: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@sensors_bp.route('/api/devices/<device_id>/commands', methods=['GET'])
@require_auth
def get_device_commands(device_id):
    """Get command history for a device"""
    try:
        tenant_id = g.tenant

        # Get query parameters
        limit = int(request.args.get('limit', 50))
        status = request.args.get('status')  # optional filter

        with get_db_connection_with_tenant(tenant_id) as conn:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)

                query = """
                    SELECT
                        id,
                        command_type,
                        payload,
                        status,
                        sent_at,
                        executed_at,
                        response
                    FROM commands
                    WHERE tenant_id = %s AND device_id = %s
                """
                params = [tenant_id, device_id]

                if status:
                    query += " AND status = %s"
                    params.append(status)

                query += " ORDER BY sent_at DESC LIMIT %s"
                params.append(limit)

                cur.execute(query, params)

                commands = []
                for row in cur.fetchall():
                    commands.append({
                        'id': str(row['id']),
                        'command_type': row['command_type'],
                        'payload': row['payload'] if isinstance(row['payload'], dict) else json.loads(row['payload']) if row['payload'] else {},
                        'status': row['status'],
                        'sent_at': row['sent_at'].isoformat() if row['sent_at'] else None,
                        'executed_at': row['executed_at'].isoformat() if row['executed_at'] else None,
                        'response': row['response'] if isinstance(row['response'], dict) else json.loads(row['response']) if row['response'] else None
                    })

                cur.close()

                return jsonify({
                    'device_id': device_id,
                    'commands': commands,
                    'count': len(commands)
                }), 200

            except Exception as e:
                logger.error(f"Error getting commands: {e}")
                return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in get_device_commands: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# =============================================================================
# Heartbeat / Connection Status Check
# =============================================================================


@sensors_bp.route('/api/heartbeat/check', methods=['GET'])
@require_auth
def check_entity_heartbeat():
    """
    Check if an entity (sensor, robot, device) has connected and sent data.

    Query params:
      - entity_id: The device/sensor external ID or entity URN
      - entity_type: 'sensor', 'robot', or 'device'

    Returns:
      - connected: boolean indicating if data has been received
      - last_seen: ISO timestamp of last data received
      - first_seen: ISO timestamp of first data received
    """
    try:
        tenant_id = g.tenant
        if not tenant_id:
            return jsonify({'error': 'Tenant not found'}), 401

        entity_id = request.args.get('entity_id')
        entity_type = request.args.get('entity_type', 'sensor')

        if not entity_id:
            return jsonify({'error': 'entity_id is required'}), 400

        # Extract device_id from URN if provided
        device_id = entity_id
        if entity_id.startswith('urn:ngsi-ld:'):
            # Format: urn:ngsi-ld:Type:tenant:device_id
            parts = entity_id.split(':')
            if len(parts) >= 5:
                device_id = parts[-1]

        conn = None
        try:
            conn = get_db_connection_simple()
            if not conn:
                return jsonify({'error': 'Database not configured'}), 503

            cur = conn.cursor(cursor_factory=RealDictCursor)

            # Check telemetry_events table for any data from this device
            cur.execute("""
                SELECT
                    MIN(observed_at) as first_seen,
                    MAX(observed_at) as last_seen,
                    COUNT(*) as event_count
                FROM telemetry_events
                WHERE tenant_id = %s AND device_id = %s
            """, (tenant_id, device_id))

            row = cur.fetchone()
            cur.close()

            if row and row['event_count'] and row['event_count'] > 0:
                return jsonify({
                    'connected': True,
                    'first_seen': row['first_seen'].isoformat() if row['first_seen'] else None,
                    'last_seen': row['last_seen'].isoformat() if row['last_seen'] else None,
                    'event_count': row['event_count']
                }), 200
            else:
                # No events found - check if we can query Orion-LD for entity status
                return jsonify({
                    'connected': False,
                    'first_seen': None,
                    'last_seen': None
                }), 200

        except Exception as db_error:
            logger.error(f"Database error checking heartbeat: {db_error}")
            return jsonify({'connected': False}), 200
        finally:
            # A pooled connection goes back to the pool; closing it leaks the slot.
            return_db_connection(conn)

    except Exception as e:
        logger.error(f"Error checking heartbeat: {e}")
        return internal_error(e, 'sensors_endpoint')
