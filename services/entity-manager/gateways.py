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
import logging
import os
import re
import secrets
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional

from common.device_measurement_shape import SHAPES, Channel
from common.unit_codes import to_unit_code

logger = logging.getLogger(__name__)

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


# ── the projection: Orion entity, IoT Agent device, MQTT permission ─────────

OFF = "off"
DONE = "done"
_CLIENT_NOT_FOUND = "Client not found"
_AGENT_KEYS = ("object_id", "name", "type", "expression", "entity_name", "entity_type", "metadata")


def failed(step: str) -> str:
    return f"failed:{step}"


def _plain(value: Any) -> Any:
    """Orion returns a DateTime as {"@type", "@value"}."""
    if isinstance(value, Mapping) and "@value" in value:
        return value["@value"]
    return value


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else [value]


def _instant_of(value: Any) -> Optional[datetime.datetime]:
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(datetime.timezone.utc).replace(microsecond=0)


def _is_stored(stored: Any, wanted: Mapping[str, Any], name: str) -> bool:
    """`stored` (as Orion returns it) says what `wanted` says: single-element lists come back
    as scalars, dates as {"@type", "@value"} and with fractions of a second."""
    if not isinstance(stored, Mapping) or stored.get("type") != wanted["type"]:
        return False
    member = "object" if wanted["type"] == "Relationship" else "value"
    got, want = _plain(stored.get(member)), _plain(wanted[member])
    if name == "dateInstalled":
        return _instant_of(got) is not None and _instant_of(got) == _instant_of(want)
    got, want = _as_list(got), _as_list(want)
    if name == "controlledAsset":
        return sorted(map(str, got)) == sorted(map(str, want))
    return got == want


def entity_changes(stored: Mapping[str, Any], wanted: Mapping[str, Any]) -> tuple:
    """(attributes to write, attributes to delete) to bring a stored Device to `wanted`.

    Only the attributes the entity-manager owns are looked at: the IoT Agent's stay.
    """
    changed = {name: attr for name, attr in wanted.items()
               if name not in ("id", "type") and not _is_stored(stored.get(name), attr, name)}
    stale = ["controlledAsset"] if "controlledAsset" in stored and "controlledAsset" not in wanted else []
    return changed, stale


def agent_matches(doc: Mapping[str, Any], urn: str, apikey: str, attributes: List[Mapping[str, Any]]) -> bool:
    """The agent's own document says what the projection would provision."""
    if doc.get("entity_name") != urn or doc.get("entity_type") != "Device":
        return False
    if "apikey" in doc and doc["apikey"] != apikey:
        return False
    stored = doc.get("attributes")
    if not isinstance(stored, list) or len(stored) != len(attributes):
        return False
    for have, want in zip(stored, attributes):
        if not isinstance(have, Mapping):
            return False
        if any(have.get(k) != want.get(k) for k in _AGENT_KEYS):
            return False
    return True


def _upsert_entity(tenant: str, entity: Dict[str, Any]) -> None:
    import device_models
    with device_models.SyncOrionClient(tenant, base_url=device_models.ORION_URL,
                                       context_url=device_models.CONTEXT_URL) as client:
        errors = client.upsert_entities_batch([entity])["errors"]  # a 207 does not raise
    if errors:
        raise RuntimeError("the broker refused the entity")


def _sync_entity(tenant: str, entity: Dict[str, Any]) -> None:
    """Create the Device, or write what differs in the attributes the entity-manager owns."""
    import device_models
    stored = device_models.read_entity(tenant, entity["id"])
    if stored is None:
        _upsert_entity(tenant, entity)
        return
    changed, stale = entity_changes(stored, entity)
    if changed:
        # POST /attrs: PATCH cannot add an attribute that is missing (207).
        device_models.append_attrs(tenant, entity["id"], changed)
    for name in stale:
        device_models.delete_attr(tenant, entity["id"], name)


def _sync_agent_device(tenant: str, apikey: str, external_id: str, urn: str) -> None:
    """R6: the device exists in the agent as the projection says, or is made so. No write when equal."""
    import dataloggers
    iot_agent = dataloggers.iot_agent
    url, headers = dataloggers.iota_url(), dataloggers.iota_headers(tenant)
    attributes = build_iota_attributes(tenant, external_id, iota_dm_shape())
    for _ in range(2):
        try:
            doc = iot_agent.get_device(url, headers, external_id)
        except iot_agent.IotAgentError as exc:
            if exc.status_code != 404:
                raise
            doc = None
        if doc is not None:
            if agent_matches(doc, urn, apikey, attributes):
                return
            # autoprovisioned bare, another entity name or other mappings: replace
            if not iot_agent.delete_device(url, headers, external_id):
                raise RuntimeError("the agent did not delete the device")
        try:
            iot_agent.provision_device(url, headers, apikey=apikey, device_id=external_id,
                                       entity_name=urn, entity_type="Device", attributes=attributes)
            return
        except iot_agent.IotAgentError as exc:
            if exc.status_code != 409:
                raise
            # created in between (a beacon autoprovisions): read it again and compare
    raise RuntimeError("the agent device kept changing")


def _tenant_suspended(exc: Exception) -> bool:
    return getattr(exc, "status", None) == 409 and getattr(exc, "step", None) == "tenant_suspended"


def _set_topics(tenant: str, cur: Any, row: Mapping[str, Any], apikey: str) -> None:
    import dataloggers
    client = dataloggers._dynsec()
    username = row["mqtt_username"]
    try:
        client.run([{"command": "getClient", "username": username}])
    except Exception as exc:
        if dataloggers._broker_error(exc) != dataloggers._CLIENT_NOT_FOUND:
            raise
        # R2: an account the broker lost is for `rotate`; nothing is created here.
        logger.warning("gateway projection: datalogger %s is unknown to the broker", username)
        return
    dataloggers.mqtt_dynsec.set_role_topics(client, username, dataloggers.role_topics(cur, tenant, row, apikey))


def project_gateway(tenant: str, dl_id: Any) -> str:
    """Make Orion, the IoT Agent and the broker role say what the datalogger row says.

    Returns `done`, `off` (flag off or tenant suspended: nothing was touched) or `failed:<step>`.
    Never raises; logs ids and exception type names only. Order is the protection against bare
    entities and permissions without a device: Orion entity, then agent device, then the marker
    and the role topics. A failed step leaves the later ones undone and the row rolled back.
    """
    step = "start"
    try:
        if not enabled(tenant):
            return OFF
        import dataloggers
        from psycopg2.extras import RealDictCursor
        step = "row"
        dl_id = dataloggers._dl_id(dl_id)
        for _ in range(2):
            with dataloggers.get_db_connection_with_tenant(tenant) as conn:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                try:
                    step = "lock"
                    dataloggers.require_active_tenant(cur, tenant)  # ingestion lock, then deleted_at
                    step = "row"
                    row = dataloggers._row(cur, tenant, dl_id, lock=True)  # then the row lock
                    if row.get("gateway_external_id") is None:
                        # Committed before anything is written elsewhere: whatever the repair
                        # finds in Orion has an id that is in the table. Then both locks again.
                        step = "id"
                        cur.execute("UPDATE iot_dataloggers SET gateway_external_id = %s "
                                    "WHERE id = %s AND tenant_id = %s AND gateway_external_id IS NULL",
                                    (new_external_id(), dl_id, tenant))
                        conn.commit()
                        continue
                    return _project_locked(tenant, conn, cur, row)
                except Exception as exc:
                    conn.rollback()
                    if _tenant_suspended(exc):
                        return OFF
                    raise
        return failed("id")
    except Exception as exc:
        logger.error("gateway projection failed: tenant=%s datalogger=%s step=%s error=%s",
                     tenant, dl_id, step, type(exc).__name__)
        return failed(step)


def _project_locked(tenant: str, conn: Any, cur: Any, row: Mapping[str, Any]) -> str:
    import dataloggers
    external_id, dl_id = row["gateway_external_id"], str(row["id"])
    urn = gateway_urn(tenant, external_id)
    step = "entity"
    try:
        sensors = [d["device_urn"] for d in dataloggers._device_rows(cur, tenant, dl_id)]
        entity = build_entity(tenant, row, sensors)
        step = "orion_entity"
        _sync_entity(tenant, entity)
        if row["status"] == "revoked":
            # The account is disabled by the user's revoke: the agent device and the topics stay.
            step = "commit"
            conn.commit()
            return DONE
        step = "service_group"
        apikey = dataloggers.get_apikey(tenant)
        step = "agent_device"
        _sync_agent_device(tenant, apikey, external_id, urn)
        if row.get("gateway_provisioned_at") is None:
            step = "marker"
            cur.execute("UPDATE iot_dataloggers SET gateway_provisioned_at = now() "
                        "WHERE id = %s AND tenant_id = %s AND gateway_provisioned_at IS NULL",
                        (dl_id, tenant))
            row = {**row, "gateway_provisioned_at": True}
        step = "acl"
        _set_topics(tenant, cur, row, apikey)
        step = "commit"
        conn.commit()
        return DONE
    except Exception as exc:
        conn.rollback()
        logger.error("gateway projection failed: tenant=%s datalogger=%s step=%s error=%s",
                     tenant, dl_id, step, type(exc).__name__)
        return failed(step)
