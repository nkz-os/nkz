"""Device entity -> `DeviceMeasurement` entities — pure transformer.

Turns one NGSI-LD `Device` (or `ManufacturingMachine`) entity notification, plus the
`sensor_profiles` catalogue row that describes what its attributes mean, into a list of
`DeviceMeasurement` entities ready to write to Orion-LD.

`build_measurements` is deliberately pure: entity and profile dicts in, a list of entity dicts
out. No network, no database, no wall-clock dependency on the primary path. Task 3 owns wiring
this into `blueprints/notifications.py` and the actual Orion write.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from common.unit_codes import to_unit_code

# Attributes that are never measurements, regardless of what the profile declares. Device
# identity/metadata, not a reading. Kept as an explicit allow-list-adjacent skip so a coincidental
# profile entry can never turn identity data into a bogus DeviceMeasurement.
_NON_MEASUREMENT_KEYS = frozenset({"id", "type", "@context"})


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


def _measurement_defs_by_attr(profile: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Index the profile's declared measurements by `sdmAttribute` (the entity attribute name)."""
    mapping = profile.get("mapping") or {}
    measurements = mapping.get("measurements") or []
    return {
        m["sdmAttribute"]: m
        for m in measurements
        if isinstance(m, dict) and m.get("sdmAttribute")
    }


def _resolve_observed_at(attr_value: Dict[str, Any], entity: Dict[str, Any]) -> str:
    """Pick the DateTime value for `dateObserved`.

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
    if not observed_at:
        observed_at = datetime.now(timezone.utc).isoformat()
    return observed_at


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
    measurement_defs = _measurement_defs_by_attr(profile)

    results: List[Dict[str, Any]] = []
    for attr_name, attr_value in entity.items():
        if attr_name in _NON_MEASUREMENT_KEYS:
            continue
        measurement_def = measurement_defs.get(attr_name)
        if measurement_def is None:
            continue
        if not isinstance(attr_value, dict) or attr_value.get("type") != "Property":
            # A Relationship/GeoProperty happening to share a name with a declared measurement
            # is not a reading either — a measurement's value always arrives as a Property.
            continue

        value = attr_value.get("value")
        unit_symbol = measurement_def.get("unit", "") or ""
        # Always resolved, even when the branch below produces textValue: an unrecognized unit
        # must raise regardless of the value's type (see judgement call in the docstring).
        unit_code = to_unit_code(unit_symbol)

        value_prop: Dict[str, Any] = {"type": "Property", "value": value}
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value_key = "numValue"
            if unit_code is not None:
                value_prop["unitCode"] = unit_code
        else:
            value_key = "textValue"

        measurement: Dict[str, Any] = {
            "id": f"urn:ngsi-ld:DeviceMeasurement:{tenant}:{external_id}:{attr_name}",
            "type": "DeviceMeasurement",
            "refDevice": {"type": "Relationship", "object": entity_id},
            "controlledProperty": {"type": "Property", "value": attr_name},
            "measurementType": {"type": "Property", "value": measurement_def.get("type")},
            value_key: value_prop,
            "dateObserved": {
                "type": "Property",
                "value": _resolve_observed_at(attr_value, entity),
            },
        }
        results.append(measurement)

    return results
