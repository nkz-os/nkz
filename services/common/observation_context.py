"""Validation of observation context and per-channel overrides.

Pure functions, no I/O. A context says what a measurement observes (the medium
and where in it). Callers turn ValueError into a client error (HTTP 400).
"""
import math
from typing import Any, Dict, Set

MEDIA = frozenset({
    "ambientAir", "ambientAirUnderStructure", "enclosedAir",
    "soil", "plant", "water", "equipment",
})
ORGANS = frozenset({"stem", "leaf", "fruit", "canopy", "root"})

_URN_PREFIX = "urn:ngsi-ld:"

# medium -> (required fields, optional fields)
_FIELDS = {
    "ambientAir": ((), ("heightAboveGround",)),
    "ambientAirUnderStructure": (("structure",), ("heightAboveGround",)),
    "enclosedAir": (("enclosure",), ("heightAboveGround",)),
    "soil": (("depthFrom", "depthTo"), ()),
    "plant": (("organ",), ()),
    "water": ((), ()),
    "equipment": ((), ()),
}
_NUMBER_FIELDS = {"heightAboveGround", "depthFrom", "depthTo"}
_URN_FIELDS = {"structure", "enclosure"}


def _number(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


def _urn(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.startswith(_URN_PREFIX):
        raise ValueError(f"{name} must be a URN starting with '{_URN_PREFIX}'")
    return value


def validate_observation_context(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Return a normalized copy of `ctx`, or raise ValueError."""
    if not isinstance(ctx, dict):
        raise ValueError("observationContext must be an object")
    medium = ctx.get("medium")
    if not isinstance(medium, str) or medium not in MEDIA:
        raise ValueError(
            f"medium must be one of {sorted(MEDIA)}, got {medium!r}"
        )
    required, optional = _FIELDS[medium]
    allowed = set(required) | set(optional)
    for key in ctx:
        if key != "medium" and key not in allowed:
            raise ValueError(f"field {key} is not allowed for medium {medium}")
    for key in required:
        if key not in ctx:
            raise ValueError(f"{key} is required for medium {medium}")

    out: Dict[str, Any] = {"medium": medium}
    for key in required + tuple(optional):
        if key not in ctx:
            continue
        value = ctx[key]
        if key in _NUMBER_FIELDS:
            out[key] = _number(key, value)
        elif key in _URN_FIELDS:
            out[key] = _urn(key, value)
        elif key == "organ":
            if not isinstance(value, str) or value not in ORGANS:
                raise ValueError(f"organ must be one of {sorted(ORGANS)}, got {value!r}")
            out[key] = value

    if "heightAboveGround" in out and out["heightAboveGround"] < 0:
        raise ValueError("heightAboveGround must be >= 0")
    if medium == "soil" and not 0 <= out["depthFrom"] < out["depthTo"]:
        raise ValueError("depthFrom must satisfy 0 <= depthFrom < depthTo")
    return out


def validate_channel_overrides(
    overrides: Dict[str, Any], attributes: Set[str]
) -> Dict[str, Any]:
    """Validate `channelOverrides` keyed by canonical sdmAttribute names."""
    if not isinstance(overrides, dict):
        raise ValueError("channelOverrides must be an object")
    out: Dict[str, Any] = {}
    for attr, override in overrides.items():
        if attr not in attributes:
            raise ValueError(
                f"channelOverrides key {attr!r} is not an attribute of the device profile"
            )
        if not isinstance(override, dict):
            raise ValueError(f"channelOverrides[{attr}] must be an object")
        unknown = set(override) - {"observesFeature", "observationContext"}
        if unknown:
            raise ValueError(
                f"channelOverrides[{attr}] has unsupported fields {sorted(unknown)}"
            )
        if not override:
            raise ValueError(
                f"channelOverrides[{attr}] needs observesFeature or observationContext"
            )
        item: Dict[str, Any] = {}
        if "observesFeature" in override:
            item["observesFeature"] = _urn(
                f"channelOverrides[{attr}].observesFeature", override["observesFeature"]
            )
        if "observationContext" in override:
            try:
                item["observationContext"] = validate_observation_context(
                    override["observationContext"]
                )
            except ValueError as exc:
                raise ValueError(f"channelOverrides[{attr}]: {exc}") from exc
        out[attr] = item
    return out


# ---------------------------------------------------------------------------
# DeviceMeasurement entity id rule (contract 8.10 segment pruning)
# ---------------------------------------------------------------------------
# telemetry_measurements is compressed with segmentby (tenant_id, entity_id, attribute_name);
# device_id is not a segmentby key. The timeseries-reader prunes compressed batches with
# `entity_id LIKE '%' || measurement_entity_suffix(device_id, attribute)` (sources.py
# `_SERIES_PRUNE`). The telemetry-worker writes observation context only for rows that satisfy
# the same rule (`entity_id_conforms`), so a row the reader's prune would skip never carries a
# deployment: it is excluded from every read path alike, never silently from only one.

def measurement_entity_suffix(device_id: str, attribute: str) -> str:
    """`:{device_id}:{attribute}`: how every observation row's entity_id must end.

    entity-manager `build_measurements` mints `urn:ngsi-ld:DeviceMeasurement:{tenant}:
    {externalId}:{attr}`; the worker stores device_id = last segment of the Device URN and
    attribute_name = the measurement key (controlledProperty).
    """
    return f":{device_id}:{attribute}"


def entity_id_conforms(entity_id: Any, device_id: Any, attribute: Any) -> bool:
    """True when `entity_id` ends with measurement_entity_suffix(device_id, attribute)."""
    if not all(isinstance(x, str) and x for x in (entity_id, device_id, attribute)):
        return False
    return entity_id.endswith(measurement_entity_suffix(device_id, attribute))
