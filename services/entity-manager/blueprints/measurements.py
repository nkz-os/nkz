"""Device entity -> `DeviceMeasurement` entities — pure transformer.

Turns one NGSI-LD `Device` (or `ManufacturingMachine`) entity notification, plus the
`sensor_profiles` catalogue row that describes what its attributes mean, into a list of
`DeviceMeasurement` entities ready to write to Orion-LD.

`build_measurements` is deliberately pure: entity and profile dicts in, a list of entity dicts
out. No network, no database, no wall-clock dependency on the primary path. Task 3 owns wiring
this into `blueprints/notifications.py` and the actual Orion write.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from common.context_aliases import canonical_attribute
from common.device_measurement_shape import channels_by_name
from common.device_model import entity_profile

# Attributes that are never measurements, regardless of what the profile declares. Device
# identity/metadata, not a reading. Kept as an explicit allow-list-adjacent skip so a coincidental
# profile entry can never turn identity data into a bogus DeviceMeasurement.
_NON_MEASUREMENT_KEYS = frozenset({"id", "type", "@context"})

logger = logging.getLogger(__name__)

SDM_SHAPE_FLAG = "DEVICE_MEASUREMENT_SDM_SHAPE_ENABLED"
# The shape of each DeviceMeasurement (id, fixed attributes, unit) comes from
# `common.device_measurement_shape`; this module only adds the reading itself.


def sdm_shape_enabled() -> bool:
    """Effective value of `DEVICE_MEASUREMENT_SDM_SHAPE_ENABLED` (default off).

    Parsed like `RECONCILE_BACKSTOP_ENABLED`: only `.lower() == "true"` is on, any other value
    (including unparseable ones) is off. Read on every call so the effective value is never stale.
    """
    return os.getenv(SDM_SHAPE_FLAG, "false").lower() == "true"


def log_sdm_shape_flag() -> None:
    """Log the effective flag value once at startup, as a structured field."""
    enabled = sdm_shape_enabled()
    logger.info(
        "device_measurement_sdm_shape_flag %s=%s",
        SDM_SHAPE_FLAG,
        enabled,
        extra={"flag": SDM_SHAPE_FLAG, "enabled": enabled},
    )


class DeviceIdShapeError(ValueError):
    """Raised when an entity id does not match `urn:ngsi-ld:{Type}:{tenant}:{externalId}`."""


def _parse_device_id(entity_id: str) -> Tuple[str, str]:
    """Split a device entity id into (tenant, externalId).

    Expected shape: `urn:ngsi-ld:{Type}:{tenant}:{externalId}` (see `sensors.py`, which mints
    ids this way). `externalId` is rejoined from any remaining segments rather than taken as
    `parts[-1]`, so an externalId that itself contains a colon does not get truncated.

    Judgement call: an id that does not match this shape is a data problem, not something to
    paper over with a guessed tenant. It raises loudly here rather than silently producing a
    malformed `DeviceMeasurement` id downstream.
    """
    parts = entity_id.split(":") if entity_id else []
    if len(parts) < 5 or parts[0] != "urn" or parts[1] != "ngsi-ld":
        raise DeviceIdShapeError(
            f"Device entity id {entity_id!r} does not match "
            "'urn:ngsi-ld:{Type}:{tenant}:{externalId}' — refusing to build measurements "
            "with a guessed tenant/externalId."
        )
    tenant = parts[3]
    external_id = ":".join(parts[4:])
    return tenant, external_id


def _resolve_observed_at(attr_value: Dict[str, Any], entity: Dict[str, Any]) -> Tuple[str, str]:
    """Pick the DateTime value for `dateObserved` and say where it came from.

    Returns `(iso_instant, time_source)`; `time_source` is `"device"` when the instant came
    from the attribute or entity `observedAt`, `"server"` when it is the wall-clock fallback.

    Priority: the attribute's own `observedAt` (normalized NGSI-LD Property metadata — the most
    precise, per-reading timestamp) -> the entity-level `observedAt` attribute, if the whole
    entity carries one -> wall-clock `utcnow()` as a last resort, matching the fallback already
    used elsewhere in this service (e.g. `blueprints/calibration.py`) when no source timestamp is
    available at all.
    """
    observed_at = attr_value.get("observedAt")
    if not observed_at:
        entity_level = entity.get("observedAt")
        if isinstance(entity_level, dict):
            observed_at = entity_level.get("value")
    if observed_at:
        return observed_at, "device"
    return datetime.now(timezone.utc).isoformat(), "server"


def build_measurements(entity: Dict[str, Any], profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turn a device entity's measurement attributes into `DeviceMeasurement` entities.

    `entity` is a normalized NGSI-LD entity (each attribute `{"type": "Property", "value": ...}`
    or `{"type": "Relationship", "object": ...}`). `profile` is a `sensor_profiles` row; only its
    `mapping.measurements` (list of `{type, unit, sdmAttribute}`) is read.

    Judgement call — attributes the profile does not declare: silently dropped, not included.
    The entity carries identity/metadata attributes (`name`, `location`, `controlledAsset`,
    `status`, `externalId`, ...) that are not readings; blindly turning every attribute into a
    `DeviceMeasurement` would write garbage entities for all of them. Only what the catalogue
    explicitly names as a measurement becomes one.

    Judgement call — `to_unit_code` raising for a profile's unit: left uncaught, propagates out
    of this function. Task 1 made unknown units fail loudly on purpose (no guessed code, no
    silent pass-through); swallowing that exception here to keep half a batch would defeat the
    fail-fast entirely and let an unknown unit ship anyway. The caller (Task 3, `/notify`) decides
    what "fail loudly" means operationally (log-and-drop-notification, 5xx, ...) — this function's
    job is only to make sure the exception surfaces, never to hide it.
    """
    entity_id = entity.get("id", "")
    tenant, external_id = _parse_device_id(entity_id)
    # The rule of `device_profile.entity_profile_code`, without its log: this module is pure and
    # is also loaded outside this service.
    profile_code = entity_profile(tenant, entity)[0]

    shape = "sdm" if sdm_shape_enabled() else "legacy"
    results: List[Dict[str, Any]] = []
    # One reading per canonical name: when the entity carries both aliases, the canonical
    # attribute wins.
    chosen: Dict[str, Tuple[str, Any]] = {}
    for raw_name, raw_value in entity.items():
        if raw_name in _NON_MEASUREMENT_KEYS:
            continue
        canon = canonical_attribute(raw_name)
        if canon not in chosen or raw_name == canon:
            chosen[canon] = (raw_name, raw_value)

    mapping = profile.get("mapping") or {}
    # Only channels actually read are resolved, so an unknown unit in an absent channel does
    # not raise (an unknown unit in a present one still does, whatever the value's type).
    channels = channels_by_name(
        mapping.get("measurements") or [],
        names={name for name, (_, v) in chosen.items()
               if isinstance(v, dict) and v.get("type") == "Property"},
        tenant=tenant,
        external_id=external_id,
        device_urn=entity_id,
        profile_code=profile_code,
        shape=shape,
    )

    for attr_name, (_, attr_value) in chosen.items():
        channel = channels.get(attr_name)
        if channel is None:
            continue
        if not isinstance(attr_value, dict) or attr_value.get("type") != "Property":
            # A Relationship/GeoProperty happening to share a name with a declared measurement
            # is not a reading either — a measurement's value always arrives as a Property.
            continue

        value = attr_value.get("value")
        value_prop: Dict[str, Any] = {"type": "Property", "value": value}
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value_key = "numValue"
            if channel.unit_code is not None:
                value_prop["unitCode"] = channel.unit_code
        else:
            value_key = "textValue"

        observed_at, time_source = _resolve_observed_at(attr_value, entity)
        measurement: Dict[str, Any] = {
            "id": channel.dm_id,
            "type": "DeviceMeasurement",
            "refDevice": {"type": "Relationship", "object": entity_id},
            value_key: value_prop,
            "dateObserved": {
                "type": "Property",
                "value": observed_at,
            },
            "timeSource": {"type": "Property", "value": time_source},
        }
        for fixed_name, fixed_value in channel.fixed.items():
            measurement[fixed_name] = {"type": "Property", "value": fixed_value}
        results.append(measurement)

    return results
