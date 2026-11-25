"""The gateway (datalogger box) as an official `Device`, with its beacon: pure contract.

No database, network or clock here: identifiers, the flag, the entity the entity-manager writes
and the IoT Agent mappings of the beacon. The projection and the repair (later tasks) build on it.

Contract (design 2026-11-25):
  * `urn:ngsi-ld:Device:<tenant>:gw-<12 lowercase hex>`; the `gw-...` part is the external id and
    the IoT Agent device id; beacon topic `/<apikey>/gw-<hex>/attrs` every 300 s.
  * The entity-manager writes `name`, `deviceCategory` (network), `supportedProtocol`,
    `controlledProperty` (empty: the box measures nothing of the environment), `controlledAsset`
    (its sensors, absent when none), `deviceState` and `dateInstalled`. The IoT Agent writes
    `dateLastValueReported` (arrival time), `softwareVersion` and `rssi`. Never the same attribute.
    `uptime` and `batteryLevel` live in the series (`DeviceMeasurement`) only.
  * Series: one `DeviceMeasurement` per beacon key (`uptime` SEC, `batteryLevel` C62, `rssi` DBM),
    written by the IoT Agent, no profile and no deployment.

Rulings R4/R5: the generator of sensor mappings (`common/iota_dm_mappings.py`) trusts the profile
and reads `TimeInstant`; a gateway has strict guards on what it accepts and its instants are the
arrival time. So the DM mappings of a gateway are built here, from the same `Channel`
description, and the generator is left as it is for sensors. Each guard below is the JEXL the IoT
Agent evaluates, checked with the agent's own library over every value class (see
`tests/gateway_jexl.py` and `tests/fixtures/gateway_jexl_golden.json`).

R7: QoS 1 may deliver a beacon twice; both readings are stored (same `uptime`, arrival instants
tens of milliseconds apart). No dedup here.
"""
from __future__ import annotations

import datetime
import os
import re
import secrets
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional

from common.device_measurement_shape import SHAPES, Channel
from common.unit_codes import to_unit_code

FLAG = "NKZ_GATEWAY_DEVICES"
SHAPE_ENV = "IOTA_DM_SHAPE"
DEFAULT_SHAPE = "legacy"
BEACON_INTERVAL_S = 300

GATEWAY_ID_RE = re.compile(r"gw-[0-9a-f]{12}")
_TENANT_RE = re.compile(r"[^:\s]+")
_SENSOR_URN_RE = re.compile(r"urn:ngsi-ld:(?:Device|ManufacturingMachine):[^:\s]+:[A-Za-z0-9_.-]{1,64}")
_UNSAFE_TOPIC_CHARS = re.compile(r"[+#/\s]")  # same rule as dataloggers.check_apikey
_STATUSES = ("active", "revoked")

# UN/CEFACT Recommendation 20: SEC second, DBM decibel-milliwatts, C62 "one" (dimensionless).
_UNIT_SECONDS = to_unit_code("s")
_UNIT_DBM = to_unit_code("dBm")
_UNIT_ONE = "C62"


# ── identity ────────────────────────────────────────────────────────────────


def is_gateway_external_id(value: Any) -> bool:
    return isinstance(value, str) and GATEWAY_ID_RE.fullmatch(value) is not None


def new_external_id() -> str:
    return "gw-" + secrets.token_hex(6)


def _check_tenant(tenant: Any) -> str:
    if not isinstance(tenant, str) or not _TENANT_RE.fullmatch(tenant):
        raise ValueError(f"Invalid tenant {tenant!r}")
    return tenant


def _check_external_id(external_id: Any) -> str:
    if not is_gateway_external_id(external_id):
        raise ValueError(f"Invalid gateway external id {external_id!r}")
    return external_id


def gateway_urn(tenant: str, external_id: str) -> str:
    return f"urn:ngsi-ld:Device:{_check_tenant(tenant)}:{_check_external_id(external_id)}"


def is_gateway_urn(urn: Any) -> bool:
    if not isinstance(urn, str):
        return False
    parts = urn.split(":")
    return (len(parts) == 5 and parts[:3] == ["urn", "ngsi-ld", "Device"] and bool(parts[3])
            and is_gateway_external_id(parts[4]))


def enabled(tenant: str) -> bool:
    """`NKZ_GATEWAY_DEVICES`: comma-separated tenants, `*` for all, empty (default) for none."""
    if not isinstance(tenant, str) or not tenant:
        return False
    names = {n.strip() for n in os.environ.get(FLAG, "").split(",")}
    names.discard("")
    return "*" in names or tenant in names


def iota_dm_shape() -> str:
    """The DeviceMeasurement shape in force (`IOTA_DM_SHAPE`, default `legacy`)."""
    shape = os.environ.get(SHAPE_ENV, "") or DEFAULT_SHAPE
    if shape not in SHAPES:
        raise ValueError(f"{SHAPE_ENV}={shape!r}; expected one of {SHAPES}")
    return shape


def beacon_topic(apikey: str, external_id: str) -> str:
    if not isinstance(apikey, str) or not apikey or _UNSAFE_TOPIC_CHARS.search(apikey):
        raise ValueError("The service key is not usable as a topic level")
    return f"/{apikey}/{_check_external_id(external_id)}/attrs"


# ── the entity the entity-manager writes ────────────────────────────────────


def _prop(value: Any) -> Dict[str, Any]:
    return {"type": "Property", "value": value}


def _instant(value: Any) -> str:
    if not isinstance(value, datetime.datetime) or value.tzinfo is None:
        raise ValueError("created_at must be a timezone-aware datetime")
    utc = value.astimezone(datetime.timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_entity(tenant: str, row: Mapping[str, Any], sensor_urns: Iterable[str]) -> Dict[str, Any]:
    """The attributes the entity-manager owns, in NGSI-LD normalized form (no `@context`).

    `row` is the datalogger row (`gateway_external_id`, `name`, `status`, `created_at`).
    `controlledAsset` is absent when the gateway has no sensors: an empty relationship list is not
    valid, and an attribute added later with `POST /attrs` is created, not patched.
    """
    urn = gateway_urn(tenant, row.get("gateway_external_id"))
    name = row.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("A gateway needs a name")
    status = row.get("status")
    if status not in _STATUSES:
        raise ValueError(f"Unknown gateway status {status!r}")
    sensors = sorted(set(sensor_urns))
    for sensor in sensors:
        if not isinstance(sensor, str) or not _SENSOR_URN_RE.fullmatch(sensor) or is_gateway_urn(sensor):
            raise ValueError(f"Not a sensor URN: {sensor!r}")
    entity: Dict[str, Any] = {
        "id": urn,
        "type": "Device",
        "name": _prop(name),
        "deviceCategory": _prop(["network"]),
        "supportedProtocol": _prop(["mqtt"]),
        "controlledProperty": _prop([]),
    }
    if sensors:
        entity["controlledAsset"] = {"type": "Relationship", "object": sensors}
    entity["deviceState"] = _prop(status)
    entity["dateInstalled"] = _prop(_instant(row.get("created_at")))
    return entity


# ── the IoT Agent mappings of the beacon ────────────────────────────────────

# The guards (R4). `typeof` is JavaScript's: a JSON number is 'number', a numeric string is
# 'string', `true` is 'boolean', a list or object is 'object'; a JSON null is removed from the
# agent's context before the expression runs.
UPTIME_OK = ("(((uptime|typeof) == 'number') && uptime >= 0 && uptime < 1000000000000"
             " && (uptime|floor) == uptime)")
BATTERY_OK = ("(((batteryLevel|typeof) == 'number') && ((batteryLevel >= 0 && batteryLevel <= 1)"
              " || batteryLevel == -1))")
RSSI_OK = "(((rssi|typeof) == 'number') && rssi >= -200 && rssi <= 0)"
VERSION_OK = ("(((softwareVersion|typeof) == 'string') && softwareVersion != ''"
              " && (softwareVersion|length) <= 64)")
_NOW = "(0|now|toisodate)"

# key -> guard of the series channel of that key
_GUARDS = {"uptime": UPTIME_OK, "batteryLevel": BATTERY_OK, "rssi": RSSI_OK}


def _lit(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def gateway_channels(tenant: str, external_id: str, shape: str) -> List[Channel]:
    """The series channels of a gateway, in the shared `Channel` description (no profile)."""
    if shape not in SHAPES:
        raise ValueError(f"Unknown DeviceMeasurement shape {shape!r}; expected one of {SHAPES}")
    _check_tenant(tenant)
    _check_external_id(external_id)
    channels = []
    for name, unit_code in (("uptime", _UNIT_SECONDS), ("batteryLevel", _UNIT_ONE), ("rssi", _UNIT_DBM)):
        # legacy: measurementType names the channel (a gateway has no profile `type`)
        fixed: Dict[str, Any] = {"controlledProperty": name,
                                 "measurementType": "instant" if shape == "sdm" else name}
        if shape == "sdm":
            fixed["name"] = name
        channels.append(Channel(
            dm_id=f"urn:ngsi-ld:DeviceMeasurement:{tenant}:{external_id}:{name}",
            name=name, key=name, expression=None, kind="number", unit_code=unit_code,
            fixed=MappingProxyType(fixed)))
    return channels


def _dm_mappings(channels: List[Channel], device_urn: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for channel in channels:
        ok = _GUARDS[channel.key]
        ent = {"entity_name": channel.dm_id, "entity_type": "DeviceMeasurement"}

        def mapping(object_id: str, name: str, type_: str, expression: str) -> Dict[str, Any]:
            return {"object_id": object_id, "name": name, "type": type_, "expression": expression, **ent}

        value = mapping(channel.key, "numValue", "Number", f"{ok} ? {channel.key} : null")
        value["metadata"] = {"unitCode": {"type": "Text", "value": channel.unit_code}}
        out.append(value)
        # R5: arrival time, never the box's clock. Read from the channel's own key, so a
        # `TimeInstant` in the message is not read at all.
        out.append(mapping(channel.key, "dateObserved", "DateTime", f"{ok} ? {_NOW} : null"))
        out.append(mapping(channel.key, "timeSource", "Text", f"{ok} ? 'server' : null"))
        out.append(mapping(channel.key, "refDevice", "Relationship", f"{ok} ? {_lit(device_urn)} : null"))
        for name, fixed_value in channel.fixed.items():
            out.append(mapping(channel.key, name, "Text", f"{ok} ? {_lit(fixed_value)} : null"))
    return out


def _device_mappings() -> List[Dict[str, Any]]:
    """What the IoT Agent writes on the `Device` itself. Never `uptime` or `batteryLevel`."""
    return [
        # R4: written exactly when the `uptime` series is: the same guard, from one place.
        {"object_id": "uptime", "name": "dateLastValueReported", "type": "DateTime",
         "expression": f"{UPTIME_OK} ? {_NOW} : null"},
        {"object_id": "softwareVersion", "name": "softwareVersion", "type": "Text",
         "expression": f"{VERSION_OK} ? softwareVersion : null"},
        {"object_id": "rssi", "name": "rssi", "type": "Number",
         "expression": f"{RSSI_OK} ? rssi : null",
         "metadata": {"unitCode": {"type": "Text", "value": _UNIT_DBM}}},
    ]


def build_iota_attributes(tenant: str, external_id: str, shape: str) -> List[Dict[str, Any]]:
    """The IoT Agent attribute list of a gateway: series mappings first, then the Device ones.

    The order is load-bearing (see `common/iota_dm_mappings.py`): the agent evaluates the list in
    order over one context, so the `Device` mappings come after every DM mapping.
    """
    channels = gateway_channels(tenant, external_id, shape)
    return _dm_mappings(channels, gateway_urn(tenant, external_id)) + _device_mappings()
