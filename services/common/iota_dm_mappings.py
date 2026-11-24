"""IoT Agent multi-entity mappings that write `DeviceMeasurement` (pattern of spec 5.2).

Pure: builds the attribute list from `Channel`s (`common/device_measurement_shape.py`).
Ordering and conditions are load-bearing:
  * every DM mapping goes BEFORE the Device ones. A Device mapping whose own expression
    rewrites a key named like its attribute would otherwise scale the value twice;
  * a value is written only when the raw value is usable (and, when scaled, so is the scaled
    result), on the mapping's own name, so a partial message creates or touches no absent DM;
  * `TimeInstant`/`dateObserved`/`timeSource` read `TimeInstant` and are conditioned on the
    channel's raw key, so nulling one in a DM does not leak into the next entity.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from common.device_measurement_shape import Channel
from common.iot_agent import build_iota_attributes

# Every attribute name a DM mapping uses (any shape) plus the time key: an incoming key with
# one of these names would be overwritten by the mapping that loads it.
RESERVED_KEYS = frozenset({
    "numValue", "textValue", "refDevice", "controlledProperty", "measurementType", "deviceType",
    "name", "dateObserved", "timeSource", "TimeInstant",
})
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LIMIT = "1000000000000000"


class InvalidChannelKey(ValueError):
    """An incoming key that cannot be a JEXL identifier or collides with a DM attribute name."""


class UnsupportedChannel(ValueError):
    """A channel the generator cannot map: a scaling expression on a text or runtime channel."""


def _num(n: str) -> str:
    return (f"({n} != null && ({n}|parsefloat) == {n} && ({n}|parsefloat) < {_LIMIT}"
            f" && ({n}|parsefloat) > -{_LIMIT})")


def _is(n: str, js_type: str) -> str:
    return f"(({n}|typeof) == '{js_type}')"


def _either(n: str) -> str:
    return f"({_is(n, 'number')} || {_is(n, 'string')})"


def _lit(value: Any) -> str:
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


def _check_key(channel: Channel) -> None:
    if not _KEY_RE.match(channel.key) or channel.key in RESERVED_KEYS:
        raise InvalidChannelKey(f"Invalid incoming key {channel.key!r} for channel {channel.name!r}")
    if channel.kind not in ("number", "text", "runtime"):
        raise UnsupportedChannel(f"Unknown channel kind {channel.kind!r} for {channel.name!r}")
    if channel.expression and channel.kind != "number":
        raise UnsupportedChannel(
            f"Channel {channel.name!r}: a scaling expression is only supported on numeric channels")


def _channel_mappings(channel: Channel, device_urn: str) -> List[Dict[str, Any]]:
    _check_key(channel)
    key, expr = channel.key, channel.expression
    ent = {"entity_name": channel.dm_id, "entity_type": "DeviceMeasurement"}
    scaled = f" && ((({expr})|parsefloat) != null)" if expr else ""

    if channel.kind == "number":
        def cond(n: str) -> str:
            return _num(n) + scaled
    elif channel.kind == "text":
        def cond(n: str) -> str:
            return f"{n} != null"
    else:
        cond = _either

    def mapping(object_id: str, name: str, type_: str, expression: Optional[str]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"object_id": object_id, "name": name, "type": type_}
        if expression is not None:
            out["expression"] = expression
        out.update(ent)
        return out

    out: List[Dict[str, Any]] = []
    if channel.kind == "number":
        value = f"(({expr})|parsefloat)" if expr else "(numValue|parsefloat)"
        out.append(mapping(key, "numValue", "Number", f"{cond('numValue')} ? {value} : null"))
    if channel.kind == "text":
        out.append(mapping(key, "textValue", "Text", f"{cond('textValue')} ? textValue : null"))
    if channel.kind == "runtime":
        out.append(mapping(key, "numValue", "Number", f"{_is('numValue', 'number')} ? numValue : null"))
        out.append(mapping(key, "textValue", "Text", f"{_is('textValue', 'string')} ? textValue : null"))
    if channel.unit_code:
        out[0]["metadata"] = {"unitCode": {"type": "Text", "value": channel.unit_code}}

    raw = cond(key)
    out.append(mapping("TimeInstant", "TimeInstant", "DateTime", f"{raw} ? TimeInstant : null"))
    out.append(mapping("TimeInstant", "dateObserved", "DateTime",
                       f"{raw} ? (dateObserved ? dateObserved : (0|now|toisodate)) : null"))
    out.append(mapping("TimeInstant", "timeSource", "Text",
                       f"{raw} ? (timeSource ? 'device' : 'server') : null"))
    out.append(mapping(key, "refDevice", "Relationship", f"{cond('refDevice')} ? {_lit(device_urn)} : null"))
    for name, value in channel.fixed.items():
        if value is None:
            continue
        out.append(mapping(key, name, "Text", f"{cond(name)} ? {_lit(value)} : null"))
    return out


def dm_mappings(channels: Sequence[Channel], *, device_urn: str) -> List[Dict[str, Any]]:
    """One full mapping set per channel, in the given order (canonical last per entity)."""
    out: List[Dict[str, Any]] = []
    for channel in channels:
        out.extend(_channel_mappings(channel, device_urn))
    return out


def device_attributes(measurements: list, channels: Optional[Sequence[Channel]] = None, *,
                      device_urn: Optional[str] = None) -> List[Dict[str, Any]]:
    """The IoT Agent attribute list of a device: DM mappings (if any) then the Device ones."""
    device = build_iota_attributes(measurements)
    if channels is None:
        return device
    if not device_urn:
        raise ValueError("device_urn is required to build DeviceMeasurement mappings")
    return dm_mappings(channels, device_urn=device_urn) + device
