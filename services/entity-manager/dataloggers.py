"""Datalogger service layer: PostgreSQL rows, broker accounts and IoT Agent devices.

No Flask here. Every function raises `ApiError` (status, message and the step that
failed) and undoes what it already did before raising. Passwords are generated
here, handed back to the caller once and never stored or logged.

All datalogger tables are forced-RLS: always go through
`get_db_connection_with_tenant`, never the simple connection.
"""
import logging
import os
import re
import secrets
import uuid
from functools import wraps
from typing import Callable, List, Optional

import requests
from psycopg2.extras import RealDictCursor

from common import iot_agent, mqtt_dynsec, mqtt_endpoint
from common.context_aliases import canonical_attribute
from common.ngsi_headers import inject_fiware_headers
from db_helper import get_db_connection_with_tenant
from helpers import ORION_URL

logger = logging.getLogger(__name__)

EXTERNAL_ID_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
KINDS = ('datak', 'generic_mqtt')
DEVICE_TYPES = ('Device', 'ManufacturingMachine')
_CLIENT_NOT_FOUND = 'Client not found'


class ApiError(Exception):
    """A request that must be answered with `status`; `step` names the failed step."""

    def __init__(self, status: int, message: str, step: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.step = step

    def body(self) -> dict:
        out = {'error': self.message}
        if self.step:
            out['step'] = self.step
        return out


def in_step(step: str, message: str = 'Operation failed'):
    """Decorator: any non-ApiError exception becomes ApiError(502, message, step)."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ApiError:
                raise
            except Exception as exc:
                logger.error("step %s failed in %s: %s", step, fn.__name__, type(exc).__name__)
                raise ApiError(502, message, step) from None
        return wrapper
    return deco


class UndoStack:
    """Compensations for completed steps; `unwind` runs them in reverse, never raising."""

    def __init__(self):
        self._steps: List[tuple] = []

    def push(self, name: str, fn: Callable[[], object]) -> None:
        self._steps.append((name, fn))

    def unwind(self) -> None:
        while self._steps:
            name, fn = self._steps.pop()
            try:
                fn()
            except Exception as exc:  # compensation must continue past a failure
                logger.critical("compensation failed: step=%s error=%s", name, type(exc).__name__)


def _run_undo(fn: Callable[[], object], name: str) -> None:
    stack = UndoStack()
    stack.push(name, fn)
    stack.unwind()


# ── helpers ────────────────────────────────────────────────────────────

def iota_url() -> str:
    return os.getenv('IOT_AGENT_URL', 'http://iot-agent-json-service:4041')


def iota_headers(tenant: str) -> dict:
    # inject_fiware_headers adds NGSILD-Tenant, Fiware-Service and Fiware-ServicePath: /
    return inject_fiware_headers({'Content-Type': 'application/json'}, tenant=tenant)


def mqtt_endpoint_for_devices() -> dict:
    return mqtt_endpoint.endpoint_for_devices(
        os.getenv('MQTT_EXTERNAL_HOST', ''),
        mqtt_endpoint.parse_port(os.getenv('MQTT_EXTERNAL_PORT')))


def get_apikey(tenant: str) -> str:
    apikey = iot_agent.get_or_create_service_group(
        tenant, iota_url(), iota_headers(tenant), cbroker=ORION_URL)
    if not apikey:
        raise ApiError(502, 'IoT Agent service group unavailable', 'service_group')
    return check_apikey(apikey)


_UNSAFE_TOPIC_CHARS = re.compile(r'[+#/\s]')


def check_apikey(apikey: str) -> str:
    """An apikey becomes a topic level of the ACL: wildcards or separators would widen it."""
    if not isinstance(apikey, str) or not apikey or _UNSAFE_TOPIC_CHARS.search(apikey):
        logger.error("IoT Agent service key is not usable as a topic level")
        raise ApiError(502, 'IoT Agent service key is not usable in a topic', 'service_group')
    return apikey


def topic_for(apikey: str, external_id: str) -> str:
    check_apikey(apikey)
    return f'/{apikey}/{external_id}/attrs'


def check_dynsec_config() -> None:
    """Startup check: datalogger routes fail closed per request without broker admin credentials."""
    missing = [n for n in ('MQTT_DYNSEC_USERNAME', 'MQTT_DYNSEC_PASSWORD') if not os.getenv(n)]
    if missing:
        logger.critical("MQTT dynsec credentials missing (%s): datalogger routes will answer 502",
                        ', '.join(missing))


def _dynsec():
    try:
        return mqtt_dynsec.dynsec_from_env()
    except Exception as exc:
        logger.error("dynsec unavailable: %s", type(exc).__name__)
        raise ApiError(502, 'MQTT account service unavailable', 'mqtt_account')


def _broker_error(exc: Exception) -> str:
    return getattr(exc, 'error', '') or ''


def parse_device_urn(tenant: str, urn: str) -> str:
    """The externalId of `urn:ngsi-ld:<Device|ManufacturingMachine>:<tenant>:<externalId>`."""
    parts = (urn or '').split(':')
    if (len(parts) != 5 or parts[:2] != ['urn', 'ngsi-ld'] or parts[2] not in DEVICE_TYPES
            or parts[3] != tenant or not EXTERNAL_ID_RE.fullmatch(parts[4])):
        raise ApiError(404, 'Device not found')
    return parts[4]


def is_registered_device_urn(tenant: str, urn: str) -> bool:
    """True for the URN shape register_sensor writes (tenant-scoped externalId)."""
    try:
        parse_device_urn(tenant, urn)
    except ApiError:
        return False
    return True


def _dl_id(value) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise ApiError(404, 'Datalogger not found')


def _row(cur, tenant: str, dl_id: str, lock: bool = False) -> dict:
    sql = ("SELECT id, name, kind, mqtt_username, status FROM iot_dataloggers "
           "WHERE id = %s AND tenant_id = %s")
    if lock:
        sql += " FOR UPDATE"
    cur.execute(sql, (dl_id, tenant))
    row = cur.fetchone()
    if not row:
        raise ApiError(404, 'Datalogger not found')
    return row


def _device_rows(cur, tenant: str, dl_id: str) -> list:
    cur.execute("SELECT datalogger_id, device_urn, external_id FROM iot_datalogger_devices "
                "WHERE tenant_id = %s AND datalogger_id = %s ORDER BY external_id",
                (tenant, dl_id))
    return cur.fetchall()


def _link_row(cur, tenant: str, urn: str) -> Optional[dict]:
    cur.execute("SELECT datalogger_id, device_urn, external_id FROM iot_datalogger_devices "
                "WHERE tenant_id = %s AND device_urn = %s", (tenant, urn))
    return cur.fetchone()


# ── datalogger accounts ────────────────────────────────────────────────

def create_datalogger(tenant: str, name: str, kind: str) -> dict:
    username = 'dl_' + secrets.token_hex(8)
    password = secrets.token_urlsafe(24)
    client = _dynsec()
    # Before any write: a bad MQTT_EXTERNAL_PORT must not leave a row and an account
    # behind with the one-time password lost.
    endpoint = mqtt_endpoint_for_devices()
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute("INSERT INTO iot_dataloggers (tenant_id, name, kind, mqtt_username) "
                        "VALUES (%s, %s, %s, %s) RETURNING id", (tenant, name, kind, username))
            dl_id = str(cur.fetchone()['id'])
        except Exception as exc:
            logger.error("datalogger insert failed: error=%s", type(exc).__name__)
            raise ApiError(502, 'Could not save the datalogger', 'db')
        try:
            mqtt_dynsec.create_account(client, username, password)
        except Exception as exc:
            conn.rollback()
            logger.error("create_account failed: datalogger=%s error=%s", username, type(exc).__name__)
            raise ApiError(502, 'Could not create the MQTT account', 'mqtt_account')
        try:
            conn.commit()
        except Exception as exc:
            logger.error("commit failed after account creation: datalogger=%s error=%s",
                         username, type(exc).__name__)
            _run_undo(lambda: mqtt_dynsec.delete_account(client, username), 'delete_account')
            raise ApiError(502, 'Could not save the datalogger', 'db_commit')
    return {'id': dl_id, 'username': username, 'password': password,
            'mqtt': {**endpoint, 'username': username, 'password': password}}


@in_step('db', 'Database error')
def list_dataloggers(tenant: str) -> list:
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, name, kind, mqtt_username, status, created_at "
                    "FROM iot_dataloggers WHERE tenant_id = %s ORDER BY created_at", (tenant,))
        rows = cur.fetchall()
        cur.execute("SELECT datalogger_id, device_urn, external_id FROM iot_datalogger_devices "
                    "WHERE tenant_id = %s ORDER BY external_id", (tenant,))
        devices = cur.fetchall()
    out = []
    for r in rows:
        rid = str(r['id'])
        created = r.get('created_at')
        out.append({
            'id': rid, 'name': r['name'], 'kind': r['kind'],
            'username': r['mqtt_username'], 'status': r['status'],
            'created_at': created.isoformat() if hasattr(created, 'isoformat') else created,
            'devices': [{'externalId': d['external_id'], 'deviceUrn': d['device_urn']}
                        for d in devices if str(d['datalogger_id']) == rid],
        })
    return out


@in_step('orion_read', 'Could not read the devices from the context broker')
def _orion_devices(tenant: str, urns: list) -> dict:
    found = {}
    headers = inject_fiware_headers({'Accept': 'application/json'}, tenant=tenant,
                                    has_context_in_body=False)
    for i in range(0, len(urns), 50):
        chunk = urns[i:i + 50]
        resp = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/entities?id={','.join(chunk)}"
            f"&type={','.join(DEVICE_TYPES)}&attrs=name,profileCode&limit=1000",
            headers=headers, timeout=10)
        if resp.status_code != 200:
            raise ApiError(502, 'Could not read the devices from the context broker', 'orion_read')
        for ent in resp.json():
            found[ent.get('id')] = ent
    return found


def _prop(ent: dict, key: str):
    v = (ent or {}).get(key)
    return v.get('value') if isinstance(v, dict) else v


@in_step('db', 'Database error')
def get_datalogger_config(tenant: str, dl_id: str) -> dict:
    dl_id = _dl_id(dl_id)
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        row = _row(cur, tenant, dl_id)
        devices = _device_rows(cur, tenant, dl_id)
    apikey = get_apikey(tenant)
    entities = _orion_devices(tenant, [d['device_urn'] for d in devices]) if devices else {}
    codes = sorted({_prop(e, 'profileCode') for e in entities.values() if _prop(e, 'profileCode')})
    mappings = {}
    if codes:
        with get_db_connection_with_tenant(tenant) as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT code, mapping FROM sensor_profiles WHERE code = ANY(%s) "
                        "AND (tenant_id IS NULL OR tenant_id = %s) ORDER BY tenant_id NULLS LAST",
                        (codes, tenant))
            for r in cur.fetchall():
                mappings.setdefault(r['code'], r.get('mapping') or {})
    out_devices = []
    for d in devices:
        ent = entities.get(d['device_urn'], {})
        ms = (mappings.get(_prop(ent, 'profileCode')) or {}).get('measurements', [])
        out_devices.append({
            'externalId': d['external_id'],
            'name': _prop(ent, 'name') or d['external_id'],
            'topic': topic_for(apikey, d['external_id']),
            'measurements': [{'key': m.get('incomingKey') or m.get('type'),
                              'sdmAttribute': (canonical_attribute(m['sdmAttribute']) if m.get('sdmAttribute') else None),
                              'unit': m.get('unit')} for m in ms],
        })
    ep = mqtt_endpoint_for_devices()
    mqtt = {k: ep[k] for k in ('host', 'port') if k in ep} if ep.get('configured') else dict(ep)
    mqtt.update({'username': row['mqtt_username'], 'apikey': apikey, 'password': None})
    return {'datalogger_id': dl_id, 'name': row['name'], 'mqtt': mqtt, 'devices': out_devices}


@in_step('acl', 'ACL update failed')
def reconcile_datalogger_acl(tenant: str, dl_id: str, client=None, mutate=None) -> dict:
    """Make the broker role equal to the device rows. Never creates a client.

    The row lock is held from reading the device rows until the broker call is done,
    so two concurrent reconciles of one datalogger cannot interleave.

    `mutate(cur)`, when given, changes the link rows inside that same transaction
    (after the lock, before the rows are read): a broker failure rolls it back, so a
    retry still finds the change to make.
    """
    dl_id = _dl_id(dl_id)
    client = client or _dynsec()
    apikey = get_apikey(tenant)
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        row = _row(cur, tenant, dl_id, lock=True)
        username = row['mqtt_username']
        if mutate is not None:
            try:
                mutate(cur)
            except Exception as exc:
                conn.rollback()
                logger.error("link change failed: datalogger=%s error=%s", username, type(exc).__name__)
                raise ApiError(502, 'Could not update the device link', 'link')
        topics = {topic_for(apikey, d['external_id']) for d in _device_rows(cur, tenant, dl_id)}
        needs_rotation = False
        try:
            client.run([{'command': 'getClient', 'username': username}])
        except Exception as exc:
            if _broker_error(exc) != _CLIENT_NOT_FOUND:
                conn.rollback()
                logger.error("getClient failed: datalogger=%s error=%s", username, type(exc).__name__)
                raise ApiError(502, 'ACL update failed', 'acl')
            needs_rotation = True
        try:
            mqtt_dynsec.set_role_topics(client, username, topics)
        except Exception as exc:
            conn.rollback()
            logger.error("set_role_topics failed: datalogger=%s error=%s", username, type(exc).__name__)
            raise ApiError(502, 'ACL update failed', 'acl')
        conn.commit()
    if needs_rotation:
        logger.warning("datalogger %s is unknown to the broker; its password must be rotated", username)
    return {'needs_rotation': needs_rotation, 'topics': topics}


@in_step('mqtt_account', 'Could not change the MQTT password')
def rotate_datalogger(tenant: str, dl_id: str) -> dict:
    dl_id = _dl_id(dl_id)
    client = _dynsec()
    password = secrets.token_urlsafe(24)
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        row = _row(cur, tenant, dl_id, lock=True)
        username = row['mqtt_username']
        try:
            mqtt_dynsec.set_password(client, username, password)
        except Exception as exc:
            if _broker_error(exc) != _CLIENT_NOT_FOUND:
                conn.rollback()
                logger.error("set_password failed: datalogger=%s error=%s", username, type(exc).__name__)
                raise ApiError(502, 'Could not change the MQTT password', 'mqtt_account')
            _recreate_account(cur, conn, tenant, dl_id, username, password, client,
                              revoked=row['status'] == 'revoked')
        cur.execute("UPDATE iot_dataloggers SET credentials_rotated_at = now() "
                    "WHERE id = %s AND tenant_id = %s", (dl_id, tenant))
        try:
            conn.commit()
        except Exception as exc:
            # The broker already holds the new password; the timestamp is metadata only.
            logger.error("rotation timestamp not saved: datalogger=%s error=%s", username, type(exc).__name__)
    return {'password': password}


def _recreate_account(cur, conn, tenant, dl_id, username, password, client, revoked=False) -> None:
    """The broker lost the client (e.g. a new volume): rebuild it and its role from the rows."""
    try:
        apikey = get_apikey(tenant)
        topics = {topic_for(apikey, d['external_id']) for d in _device_rows(cur, tenant, dl_id)}
        mqtt_dynsec.delete_account(client, username)
        mqtt_dynsec.create_account(client, username, password)
        mqtt_dynsec.set_role_topics(client, username, topics)
        if revoked:
            mqtt_dynsec.disable(client, username)  # status stays revoked
    except ApiError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        logger.error("account rebuild failed: datalogger=%s error=%s", username, type(exc).__name__)
        raise ApiError(502, 'Could not rebuild the MQTT account', 'mqtt_account')


@in_step('mqtt_account', 'Could not change the MQTT account state')
def set_datalogger_status(tenant: str, dl_id: str, revoke: bool) -> None:
    dl_id = _dl_id(dl_id)
    client = _dynsec()
    sql = ("UPDATE iot_dataloggers SET status = 'revoked', revoked_at = now() "
           "WHERE id = %s AND tenant_id = %s") if revoke else (
          "UPDATE iot_dataloggers SET status = 'active', revoked_at = NULL "
          "WHERE id = %s AND tenant_id = %s")
    do, undo = (mqtt_dynsec.disable, mqtt_dynsec.enable) if revoke else (
        mqtt_dynsec.enable, mqtt_dynsec.disable)
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        row = _row(cur, tenant, dl_id, lock=True)
        username = row['mqtt_username']
        cur.execute(sql, (dl_id, tenant))
        try:
            do(client, username)
        except Exception as exc:
            # A client the broker lost cannot connect anyway: revoking it is already true.
            if revoke and _broker_error(exc) == _CLIENT_NOT_FOUND:
                logger.warning("revoke: datalogger %s is unknown to the broker", username)
            else:
                conn.rollback()
                logger.error("status change failed: datalogger=%s error=%s", username, type(exc).__name__)
                raise ApiError(502, 'Could not change the MQTT account state', 'mqtt_account')
        try:
            conn.commit()
        except Exception:
            _run_undo(lambda: undo(client, username), 'restore_account_state')
            raise ApiError(502, 'Could not save the datalogger', 'db_commit')


@in_step('db', 'Database error')
def delete_datalogger(tenant: str, dl_id: str) -> None:
    dl_id = _dl_id(dl_id)
    client = _dynsec()
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        row = _row(cur, tenant, dl_id, lock=True)
        if _device_rows(cur, tenant, dl_id):
            conn.rollback()
            raise ApiError(409, 'Datalogger still has devices')
        cur.execute("DELETE FROM iot_dataloggers WHERE id = %s AND tenant_id = %s", (dl_id, tenant))
        try:
            mqtt_dynsec.delete_account(client, row['mqtt_username'])
        except Exception as exc:
            conn.rollback()
            logger.error("delete_account failed: datalogger=%s error=%s",
                         row['mqtt_username'], type(exc).__name__)
            raise ApiError(502, 'Could not delete the MQTT account', 'mqtt_account')
        try:
            conn.commit()
        except Exception:
            # The account is gone but the row stays: rotate rebuilds it.
            logger.critical("datalogger %s: account deleted, row kept", row['mqtt_username'])
            raise ApiError(502, 'Could not save the datalogger', 'db_commit')


# ── devices ────────────────────────────────────────────────────────────

@in_step('db', 'Database error')
def require_usable_datalogger(tenant: str, dl_id) -> dict:
    """404 when it is not the tenant's, 409 when revoked."""
    dl_id = _dl_id(dl_id)
    with get_db_connection_with_tenant(tenant) as conn:
        row = _row(conn.cursor(cursor_factory=RealDictCursor), tenant, dl_id)
    if row['status'] != 'active':
        raise ApiError(409, 'Datalogger is revoked')
    row['id'] = dl_id
    return row


@in_step('link', 'Database error')
def device_link(tenant: str, urn: str) -> Optional[dict]:
    with get_db_connection_with_tenant(tenant) as conn:
        return _link_row(conn.cursor(cursor_factory=RealDictCursor), tenant, urn)


@in_step('link', 'Could not link the device to the datalogger')
def link_device(tenant: str, dl_id: str, urn: str, external_id: str) -> None:
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("INSERT INTO iot_datalogger_devices (datalogger_id, tenant_id, device_urn, external_id) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING device_urn",
                    (dl_id, tenant, urn, external_id))
        if not cur.fetchone():
            conn.rollback()
            raise ApiError(409, 'Device is already on a datalogger')
        conn.commit()


@in_step('link', 'Could not unlink the device')
def unlink_device(tenant: str, urn: str) -> None:
    with get_db_connection_with_tenant(tenant) as conn:
        conn.cursor(cursor_factory=RealDictCursor).execute(
            "DELETE FROM iot_datalogger_devices WHERE tenant_id = %s AND device_urn = %s",
            (tenant, urn))
        conn.commit()


def unlink_and_reconcile(tenant: str, urn: str) -> None:
    """Remove the link row and close the ACL for it in ONE transaction.

    The DELETE runs under the datalogger lock inside the reconcile, so a broker
    failure rolls it back and a retry still finds the link to close.
    No-op when the device had no link.
    """
    link = device_link(tenant, urn)
    if not link:
        return

    def _delete_link(cur):
        cur.execute("DELETE FROM iot_datalogger_devices WHERE tenant_id = %s AND device_urn = %s",
                    (tenant, urn))

    reconcile_datalogger_acl(tenant, str(link['datalogger_id']), mutate=_delete_link)


def undo_link(tenant: str, urn: str) -> None:
    """Compensation for a link made by this request: drop the row first, then rebuild the ACL.

    Unlike a user delete, the row must go even if the broker is down (the request is
    being abandoned); the next reconcile of that datalogger rebuilds the role from the rows.
    """
    link = device_link(tenant, urn)
    unlink_device(tenant, urn)
    if link:
        try:
            reconcile_datalogger_acl(tenant, str(link['datalogger_id']))
        except ApiError:
            logger.critical("INCONSISTENCY: ACL of datalogger %s not rebuilt after undoing %s; "
                            "reconcile it", link['datalogger_id'], urn)


def provision_iota_device(tenant: str, apikey: str, external_id: str, urn: str,
                          entity_type: str, measurements: list) -> None:
    try:
        iot_agent.provision_device(
            iota_url(), iota_headers(tenant), apikey=apikey, device_id=external_id,
            entity_name=urn, entity_type=entity_type,
            attributes=iot_agent.build_iota_attributes(measurements))
    except Exception as exc:
        logger.error("IoT Agent provisioning failed: device=%s error=%s", external_id, type(exc).__name__)
        raise ApiError(502, 'IoT Agent device provisioning failed', 'iota_device')


def delete_iota_device(tenant: str, external_id: str) -> None:
    try:
        ok = iot_agent.delete_device(iota_url(), iota_headers(tenant), external_id)
    except Exception as exc:
        logger.error("IoT Agent delete failed: device=%s error=%s", external_id, type(exc).__name__)
        ok = False
    if not ok:
        raise ApiError(502, 'IoT Agent device removal failed', 'iota_device')


@in_step('orion_entity', 'Entity removal failed')
def delete_orion_entity(tenant: str, urn: str) -> None:
    resp = requests.delete(f"{ORION_URL}/ngsi-ld/v1/entities/{urn}",
                           headers=inject_fiware_headers({}, tenant=tenant, has_context_in_body=False),
                           timeout=10)
    if resp.status_code not in (200, 204, 404):
        raise ApiError(502, 'Entity removal failed', 'orion_entity')


def delete_device(tenant: str, urn: str) -> None:
    """Close the ACL, remove the IoT Agent device, then the entity.

    Every step is idempotent, so a 502 is recovered by repeating the call; the entity
    goes last so a half-deleted device is still visible and can be retried.
    """
    external_id = parse_device_urn(tenant, urn)
    unlink_and_reconcile(tenant, urn)
    delete_iota_device(tenant, external_id)
    delete_orion_entity(tenant, urn)


def move_device(tenant: str, urn: str, new_dl_id) -> None:
    parse_device_urn(tenant, urn)
    target = require_usable_datalogger(tenant, new_dl_id)
    link = device_link(tenant, urn)
    if not link:
        raise ApiError(404, 'Device is not on a datalogger')
    old_id = str(link['datalogger_id'])
    if old_id == target['id']:
        return
    def _move(cur):
        cur.execute("UPDATE iot_datalogger_devices SET datalogger_id = %s "
                    "WHERE tenant_id = %s AND device_urn = %s", (target['id'], tenant, urn))

    # The row moves inside the target's reconcile transaction: if the broker refuses,
    # nothing was committed.
    reconcile_datalogger_acl(tenant, target['id'], mutate=_move)
    try:
        reconcile_datalogger_acl(tenant, old_id)
    except ApiError:
        _set_link_datalogger(tenant, urn, old_id)
        for dl in (target['id'], old_id):
            _run_undo(lambda dl=dl: reconcile_datalogger_acl(tenant, dl), 'reconcile')
        raise


@in_step('link', 'Database error')
def _set_link_datalogger(tenant: str, urn: str, dl_id: str) -> None:
    with get_db_connection_with_tenant(tenant) as conn:
        conn.cursor(cursor_factory=RealDictCursor).execute(
            "UPDATE iot_datalogger_devices SET datalogger_id = %s "
            "WHERE tenant_id = %s AND device_urn = %s", (dl_id, tenant, urn))
        conn.commit()


@in_step('db', 'Database error')
def _profile_measurements(tenant: str, code: str) -> Optional[list]:
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, mapping FROM sensor_profiles WHERE code = %s "
                    "AND (tenant_id IS NULL OR tenant_id = %s) ORDER BY tenant_id NULLS LAST LIMIT 1",
                    (code, tenant))
        row = cur.fetchone()
    if not row:
        return None
    return (row.get('mapping') or {}).get('measurements', [])


def change_device_profile(tenant: str, urn: str, code: str) -> None:
    """Re-provision the IoT Agent attributes, then write profileCode; undo the first if the second fails."""
    external_id = parse_device_urn(tenant, urn)
    new_ms = _profile_measurements(tenant, code)
    if new_ms is None:
        raise ApiError(404, f'Profile "{code}" not found')
    entity = _orion_devices(tenant, [urn]).get(urn)
    if not entity:
        raise ApiError(404, 'Device not found')
    old_code = _prop(entity, 'profileCode')
    old_ms = _profile_measurements(tenant, old_code) if old_code else None
    try:
        new_attrs = iot_agent.build_iota_attributes(new_ms)
    except KeyError:
        raise ApiError(400, 'Profile has measurements without sdmAttribute')
    try:
        iot_agent.update_device_attributes(iota_url(), iota_headers(tenant), external_id, new_attrs)
    except Exception as exc:
        logger.error("IoT Agent update failed: device=%s error=%s", external_id, type(exc).__name__)
        raise ApiError(502, 'IoT Agent device update failed', 'iota_device')
    try:
        resp = requests.patch(
            f"{ORION_URL}/ngsi-ld/v1/entities/{urn}/attrs",
            json={'profileCode': {'type': 'Property', 'value': code},
                  'sensorType': {'type': 'Property', 'value': code}},
            headers=inject_fiware_headers({}, tenant=tenant, has_context_in_body=False), timeout=10)
        orion_ok = resp.status_code in (200, 204)
    except Exception as exc:
        logger.error("profile patch failed: device=%s error=%s", external_id, type(exc).__name__)
        orion_ok = False
    if not orion_ok:
        if old_ms is not None:
            _run_undo(lambda: iot_agent.update_device_attributes(
                iota_url(), iota_headers(tenant), external_id,
                iot_agent.build_iota_attributes(old_ms)), 'restore_iota_attributes')
        else:
            logger.critical("INCONSISTENCY: device %s keeps IoT Agent attributes of profile %s "
                            "but the entity still has profile %s (previous profile not readable)",
                            external_id, code, old_code)
        raise ApiError(502, 'Entity update failed', 'orion_profile')


def reprovision_profile_devices(tenant: str, code: str, measurements: list) -> dict:
    """Push a profile's new attributes to the IoT Agent device of every device on it.

    Source of truth for "device is on this profile": the Orion `profileCode` of the
    tenant's linked devices (the attribute `change_device_profile` writes), restricted to
    devices with a datalogger link (those are the ones that own an IoT Agent device).
    Best effort per device: a failure is logged and listed, never raised.
    """
    out = {'reprovisioned': [], 'failed': []}
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT datalogger_id, device_urn, external_id FROM iot_datalogger_devices "
                    "WHERE tenant_id = %s ORDER BY external_id", (tenant,))
        links = cur.fetchall()
    if not links:
        return out
    entities = _orion_devices(tenant, [d['device_urn'] for d in links])
    for link in links:
        urn = link['device_urn']
        if _prop(entities.get(urn), 'profileCode') != code:
            continue
        try:
            attrs = iot_agent.build_iota_attributes(measurements)
            iot_agent.update_device_attributes(iota_url(), iota_headers(tenant),
                                               link['external_id'], attrs)
            out['reprovisioned'].append(urn)
        except Exception as exc:
            logger.error("profile re-provision failed: device=%s profile=%s error=%s",
                         link['external_id'], code, type(exc).__name__)
            out['failed'].append(urn)
    return out
