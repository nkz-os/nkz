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
import time
import uuid
from contextlib import ExitStack, contextmanager
from functools import wraps
from typing import Callable, List, Optional

import requests
from psycopg2.extras import RealDictCursor

from common import iot_agent, mqtt_dynsec, mqtt_endpoint
from common.context_aliases import canonical_attribute
from common.device_measurement_shape import SHAPES, measurement_channels
from common.iota_dm_mappings import device_attributes
from common.ngsi_headers import inject_fiware_headers
from common.window_statistics import gateway_statistics
from db_helper import get_db_connection_with_tenant
from device_profile import entity_profile_code
from device_urn import DEVICE_TYPES, EXTERNAL_ID_RE, external_id_of, is_device_type  # noqa: F401
from helpers import ORION_URL
import writer_move_lock

logger = logging.getLogger(__name__)

KINDS = ('datak', 'generic_mqtt')
_CLIENT_NOT_FOUND = 'Client not found'
# Roles that may set up dataloggers and the sensors on them; a Farmer may not.
INSTALLER_ROLES = frozenset({'PlatformAdmin', 'TenantAdmin', 'TechnicalConsultant'})


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


def require_installer(roles) -> None:
    """403 unless the caller holds one of INSTALLER_ROLES."""
    if not INSTALLER_ROLES.intersection(roles or ()):
        raise ApiError(403, 'TenantAdmin, PlatformAdmin or TechnicalConsultant required')


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
    external_id = external_id_of(tenant, urn)
    if external_id is None:
        raise ApiError(404, 'Device not found')
    return external_id


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

def _lock_ingestion(cur, tenant: str) -> None:
    """Accounts of a tenant change one operation at a time. Released with the transaction."""
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"ingestion:{tenant}",))


def require_active_tenant(cur, tenant: str) -> None:
    """409 for a suspended tenant: its accounts stay as the suspension left them."""
    _lock_ingestion(cur, tenant)
    cur.execute("SELECT 1 FROM tenants WHERE tenant_id = %s AND deleted_at IS NOT NULL", (tenant,))
    if cur.fetchone():
        raise ApiError(409, 'Tenant is suspended', 'tenant_suspended')


def set_tenant_ingestion(tenant: str, enabled: bool) -> dict:
    """Enable or disable every account of the tenant that its user has not revoked.

    The rows are not written: `revoked` belongs to the user, and restoring must
    find the accounts as they were. Idempotent; a failure on one account does
    not stop the others.
    """
    client = _dynsec()
    change = mqtt_dynsec.enable if enabled else mqtt_dynsec.disable
    changed, failed = 0, []
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _lock_ingestion(cur, tenant)
        # A late call must not undo a newer one: what is asked has to match the tenant.
        cur.execute("SELECT deleted_at IS NOT NULL AS suspended FROM tenants WHERE tenant_id = %s",
                    (tenant,))
        row = cur.fetchone()
        if not row or row['suspended'] == enabled:
            conn.rollback()
            raise ApiError(409, 'The tenant is not in that state', 'stale')
        cur.execute("SELECT mqtt_username FROM iot_dataloggers "
                    "WHERE tenant_id = %s AND status = 'active' ORDER BY mqtt_username", (tenant,))
        for username in [r['mqtt_username'] for r in cur.fetchall()]:
            try:
                change(client, username)
                changed += 1
            except Exception as exc:
                # A client the broker lost cannot connect anyway: closing it is already true.
                if not enabled and _broker_error(exc) == _CLIENT_NOT_FOUND:
                    changed += 1
                    continue
                logger.error("ingestion change failed: tenant=%s datalogger=%s enabled=%s error=%s",
                             tenant, username, enabled, type(exc).__name__)
                failed.append(username)
        conn.commit()
    return {'changed': changed, 'failed': failed}


def create_datalogger(tenant: str, name: str, kind: str) -> dict:
    username = 'dl_' + secrets.token_hex(8)
    password = secrets.token_urlsafe(24)
    client = _dynsec()
    # Before any write: a bad MQTT_EXTERNAL_PORT must not leave a row and an account
    # behind with the one-time password lost.
    endpoint = mqtt_endpoint_for_devices()
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        require_active_tenant(cur, tenant)
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
            f"&type={','.join(DEVICE_TYPES)}&attrs=name,profileCode,refDeviceModel&limit=1000",
            headers=headers, timeout=10)
        if resp.status_code != 200:
            raise ApiError(502, 'Could not read the devices from the context broker', 'orion_read')
        for ent in resp.json():
            found[ent.get('id')] = ent
    return found


def _prop(ent: dict, key: str):
    v = (ent or {}).get(key)
    return v.get('value') if isinstance(v, dict) else v


def _config_measurement(m: dict) -> dict:
    entry = {'key': m.get('incomingKey') or m.get('type'),
             'sdmAttribute': (canonical_attribute(m['sdmAttribute']) if m.get('sdmAttribute') else None),
             'unit': m.get('unit')}
    stats = gateway_statistics(m)
    if stats is not None:
        entry['statistics'] = stats
    return entry


@in_step('db', 'Database error')
def get_datalogger_config(tenant: str, dl_id: str) -> dict:
    dl_id = _dl_id(dl_id)
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        row = _row(cur, tenant, dl_id)
        devices = _device_rows(cur, tenant, dl_id)
    apikey = get_apikey(tenant)
    entities = _orion_devices(tenant, [d['device_urn'] for d in devices]) if devices else {}
    profile_of = {urn: entity_profile_code(tenant, e) for urn, e in entities.items()}
    codes = sorted({code for code in profile_of.values() if code})
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
        mapping = mappings.get(profile_of.get(d['device_urn'])) or {}
        ms = mapping.get('measurements', [])
        out_devices.append({
            'externalId': d['external_id'],
            'name': _prop(ent, 'name') or d['external_id'],
            'topic': topic_for(apikey, d['external_id']),
            'measurements': [_config_measurement(m) for m in ms],
        })
        if mapping.get('windowSeconds'):
            out_devices[-1]['windowSeconds'] = mapping['windowSeconds']
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
        require_active_tenant(cur, tenant)
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
        if not revoke:
            require_active_tenant(cur, tenant)
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


@in_step('db', 'Database error')
def device_dm_state(tenant: str, urn: str) -> tuple:
    """`(dm_writer, dm_shape)` of a device; no row means `("em", None)` (the default)."""
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT dm_writer, dm_shape FROM iot_datalogger_devices "
                    "WHERE tenant_id = %s AND device_urn = %s", (tenant, urn))
        row = cur.fetchone()
    if not row:
        return 'em', None
    return row['dm_writer'], row['dm_shape']


_VALIDATION_URN = 'urn:ngsi-ld:Device:validation:validation'


def validate_profile_mappings(measurements: list) -> None:
    """Build the mappings of a profile in every shape with placeholder ids; nothing is written.

    Raises ValueError (invalid key, unsupported channel, unknown unit) or KeyError (a
    measurement with neither incomingKey nor type); callers answer 400.
    """
    for shape in SHAPES:
        channels = measurement_channels(
            measurements, tenant='validation', external_id='validation',
            device_urn=_VALIDATION_URN, profile_code='validation', shape=shape)
        device_attributes(measurements, channels, device_urn=_VALIDATION_URN)


def _attributes_for_state(state: tuple, tenant: str, urn: str, external_id: str,
                          profile_code: Optional[str], measurements: list) -> list:
    writer, shape = state
    if writer == 'em':
        return iot_agent.build_iota_attributes(measurements)
    if writer != 'iota' or shape not in SHAPES:
        logger.error("device %s has dm_writer=%r dm_shape=%r", external_id, writer, shape)
        raise ApiError(500, 'Device measurement writer state is incomplete', 'dm_state')
    channels = measurement_channels(
        measurements, tenant=tenant, external_id=external_id, device_urn=urn,
        profile_code=profile_code, shape=shape)
    return device_attributes(measurements, channels, device_urn=urn)


def iota_attributes_for(tenant: str, urn: str, external_id: str, profile_code: Optional[str],
                        measurements: list) -> list:
    """The IoT Agent attributes of a device: the only builder used by the entity-manager.

    `em` devices get the Device mappings only. `iota` devices also get the
    DeviceMeasurement mappings of their provisioned shape: the entity-manager skips them,
    so a Device-only list would drop every reading. A state that cannot be read or is
    incomplete is an error, never a guess.
    """
    return _attributes_for_state(device_dm_state(tenant, urn), tenant, urn, external_id,
                                 profile_code, measurements)


class WriterStateError(ApiError):
    """A push after the first one failed or the writer state could not be confirmed.

    The first push has been applied, so the caller must restore the previous attributes.
    Always HTTP 502, step `dm_state`. `last_state` is the last `(dm_writer, dm_shape)` the
    pushed attributes were built from: a restore that cannot read the state builds the previous
    attributes from it. `push_iota_attributes` always sets it. It is None only when a caller
    raises this error without having built attributes; a restore then has nothing to build from
    and puts nothing back blindly.
    """

    def __init__(self, message: str, last_state: Optional[tuple] = None):
        super().__init__(502, message, 'dm_state')
        self.last_state = last_state


_PUSH_ROUNDS = 3


def _late_failure(exc: Exception, external_id: str, what: str, state: tuple) -> WriterStateError:
    logger.error("IoT Agent push: %s failed after an earlier push: device=%s error=%s",
                 what, external_id, type(exc).__name__, exc_info=exc)
    return WriterStateError(f'Device writer state could not be confirmed ({what})', state)


def _no_check() -> None:
    return None


def push_iota_attributes(tenant: str, urn: str, external_id: str, profile_code: Optional[str],
                         measurements: list, before_put: Callable[[], None] = _no_check) -> tuple:
    """Replace a device's IoT Agent attributes and make sure they match its writer state.

    The state is read, the attributes built from it, then pushed; the state is read again
    afterwards. A move to `iota` committed between the first read and the push would leave
    Device-only mappings on a device the entity-manager skips (every reading lost), so a
    changed state means build and push again (at most `_PUSH_ROUNDS` pushes). The cutover
    command does the other half (push, update the row, push again after the commit).

    Failure handling: building errors (KeyError, ValueError, ApiError) and a failure of the
    first push propagate as they are (nothing was applied). A first push that raises after
    the agent applied it (a timeout) is not detected here either. Once the first push went
    out, ANY later failure (re-read, rebuild, another push, or a state that never settles)
    is a WriterStateError, so the caller restores the previous attributes. Returns the
    state the attributes were finally built from.

    `before_put` is called right before EVERY PUT. Before the first one its exception propagates
    as it is (nothing was applied); before a later one it is a late failure, like a failing push.
    """
    state = device_dm_state(tenant, urn)
    attrs = _attributes_for_state(state, tenant, urn, external_id, profile_code, measurements)
    for push in range(_PUSH_ROUNDS):
        if push == 0:
            before_put()
            iot_agent.update_device_attributes(iota_url(), iota_headers(tenant), external_id,
                                               attrs)
        else:
            try:
                before_put()
                iot_agent.update_device_attributes(iota_url(), iota_headers(tenant),
                                                   external_id, attrs)
            except Exception as exc:
                raise _late_failure(exc, external_id, 'push', state) from exc
        try:
            current = device_dm_state(tenant, urn)
        except Exception as exc:
            raise _late_failure(exc, external_id, 're-read', state) from exc
        if current == state:
            return state
        state = current
        if push == _PUSH_ROUNDS - 1:
            break
        try:
            attrs = _attributes_for_state(state, tenant, urn, external_id, profile_code,
                                          measurements)
        except Exception as exc:
            raise _late_failure(exc, external_id, 'rebuild', state) from exc
    raise WriterStateError('Device writer state kept changing', state)


def check_entity_type(entity_type: str) -> None:
    """400 for a main entity type the IoT Agent may not be given (before any write)."""
    if entity_type not in DEVICE_TYPES:
        raise ApiError(400, f'Entity type "{entity_type}" is not allowed for IoT Agent devices')


def provision_iota_device(tenant: str, apikey: str, external_id: str, urn: str,
                          entity_type: str, measurements: list,
                          profile_code: Optional[str] = None) -> None:
    check_entity_type(entity_type)
    try:
        attributes = iota_attributes_for(tenant, urn, external_id, profile_code, measurements)
    except ValueError as exc:
        raise ApiError(400, str(exc))
    try:
        iot_agent.provision_device(
            iota_url(), iota_headers(tenant), apikey=apikey, device_id=external_id,
            entity_name=urn, entity_type=entity_type, attributes=attributes)
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
def read_profile(tenant: str, code: str) -> Optional[dict]:
    """The `sensor_profiles` row of `code`: the tenant's own, else the global one, else None."""
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, code, name, sdm_entity_type, sdm_device_category, mapping, "
                    "manufacturer_name, brand_name, model_name "
                    "FROM sensor_profiles WHERE code = %s "
                    "AND (tenant_id IS NULL OR tenant_id = %s) ORDER BY tenant_id NULLS LAST LIMIT 1",
                    (code, tenant))
        return cur.fetchone()


def _measurements_of(profile: Optional[dict]) -> Optional[list]:
    if not profile:
        return None
    return (profile.get('mapping') or {}).get('measurements', [])


def _profile_measurements(tenant: str, code: str) -> Optional[list]:
    return _measurements_of(read_profile(tenant, code))


# ── the writer move lock, shared, for profile changes ──────────────────

# seconds: a PUT started under a live lock is EXPECTED to land before a move that took the lock
# afterwards can commit (the move waits at least MIN_SETTLE). Not covered: the DNS lookup, the
# separate connect/send/read timeouts of a request, and an agent that applies a PUT late.
_FALLBACK_WINDOW = 30


@contextmanager
def profile_change_lock(tenant: str):
    """The shared writer-move lock of the tenant, held for the whole body; yields the SharedLock.

    A move in progress is 409, a lock that cannot be taken or queried is 502 `db`. Exceptions of
    the body pass unchanged.
    """
    with ExitStack() as stack:
        try:
            lock = stack.enter_context(writer_move_lock.shared(tenant))
        except writer_move_lock.MoveInProgress:
            raise ApiError(409, 'Sensors are being moved; try again in a few minutes',
                           'writer_move') from None
        except writer_move_lock.LockUnavailable:
            raise ApiError(502, 'Database error', 'db') from None
        yield lock


def _lock_alive(lock) -> bool:
    try:
        return bool(lock.alive())
    except Exception as exc:  # a lock that cannot say it is alive is lost
        logger.error("writer move lock check failed (%s)", type(exc).__name__)
        return False


def _guard(lock) -> Callable[[], None]:
    """A `before_put`: raises ApiError(502, db) when the lock is no longer alive."""
    def check() -> None:
        if not _lock_alive(lock):
            raise ApiError(502, 'Database error', 'db')
    return check


def _push_from_last_state(tenant: str, urn: str, external_id: str, old_code: Optional[str],
                          old_ms: list, last_state: tuple) -> None:
    """Put the old profile's attributes back, built from the last known writer state.

    For when the state cannot be read: leaving the new profile's mappings on the device while
    Orion keeps the old profile loses every reading.
    """
    logger.critical(
        "device=%s urn=%s: writer state unreadable while restoring the profile mappings; "
        "pushing the old profile's attributes built from the last known state %r; "
        "save the profile again once the database answers", external_id, urn, last_state)
    iot_agent.update_device_attributes(
        iota_url(), iota_headers(tenant), external_id,
        _attributes_for_state(last_state, tenant, urn, external_id, old_code, old_ms))


def change_device_profile(tenant: str, urn: str, code: str) -> None:
    """Re-provision the IoT Agent attributes, then write the profile on the entity; undo the first
    if the second fails. A Device's DeviceModel comes before both (idempotent, never undone): a
    failure of it leaves the agent untouched, and the mappings are built (a 400 leaves nothing
    behind) before it.

    The shared writer-move lock is held from before the first read until after the undo. Every
    IoT Agent PUT is preceded by a check that the lock is alive; a lost lock before the first PUT
    changes nothing but possibly the DeviceModel (idempotent, kept), a lost lock later is a failed write that is restored (see `restore`).

    For a Device the official attributes go with `profileCode`, after its DeviceModel. The one
    failure that is not undone is a leftover attribute that could not be deleted: IoT Agent and
    entity are both on the new profile by then, and repeating the call finishes the job.
    """
    external_id = parse_device_urn(tenant, urn)   # reads nothing
    with profile_change_lock(tenant) as lock:
        _change_device_profile_locked(tenant, urn, code, external_id, lock)


def _change_device_profile_locked(tenant: str, urn: str, code: str, external_id: str,
                                  lock) -> None:
    import device_models  # it imports this module

    guard = _guard(lock)
    profile = read_profile(tenant, code)
    new_ms = _measurements_of(profile)
    if new_ms is None:
        raise ApiError(404, f'Profile "{code}" not found')
    entity = _orion_devices(tenant, [urn]).get(urn)
    if not entity:
        raise ApiError(404, 'Device not found')
    old_code = entity_profile_code(tenant, entity)
    old_ms = _profile_measurements(tenant, old_code) if old_code else None
    is_device = is_device_type(urn)
    if is_device:
        # 400 before anything is written. The Device would get a sensor DeviceModel made of the
        # profile of something else.
        if (profile.get('sdm_entity_type') or 'Device') != 'Device':
            raise ApiError(400, f'Profile "{code}" is not for a Device')
        device_models.profile_attrs(tenant, profile)
        try:
            # The mappings are built (for the device's writer state now) before anything is
            # written: a 400 leaves no DeviceModel and no PUT.
            iota_attributes_for(tenant, urn, external_id, code, new_ms)
        except KeyError:
            raise ApiError(400, 'Profile has measurements without sdmAttribute')
        except ValueError as exc:
            raise ApiError(400, str(exc))
        except ApiError:
            raise
        except Exception as exc:
            logger.error("IoT Agent update failed: device=%s error=%s", external_id,
                         type(exc).__name__)
            raise ApiError(502, 'IoT Agent device update failed', 'iota_device')

    def fallback(in_use, last_state: tuple) -> None:
        """The blind restore: only under a lock that answers now or answered within 30 s."""
        if _lock_alive(in_use) or time.monotonic() - in_use.last_alive <= _FALLBACK_WINDOW:
            _push_from_last_state(tenant, urn, external_id, old_code, old_ms, last_state)
        else:
            logger.critical(
                "INCONSISTENCY: the lock was lost more than %d s ago and the device state "
                "cannot be read; the IoT Agent attributes of device=%s urn=%s were not put back "
                "(profile %s -> %s); save the profile again once the database answers",
                _FALLBACK_WINDOW, external_id, urn, old_code, code)

    def restore(last_state: Optional[tuple]) -> None:
        if old_ms is not None:
            def push_old():
                try:
                    with ExitStack() as stack:
                        current = lock
                        if not _lock_alive(lock):
                            logger.error("profile change: the lock was lost; taking it again to "
                                         "restore: device=%s urn=%s", external_id, urn)
                            try:
                                current = stack.enter_context(writer_move_lock.shared(tenant))
                            except writer_move_lock.MoveInProgress:
                                # Only a move that includes this device puts its mappings right
                                # (its second PUT applies the entity's profile, still the old one);
                                # another device's move, or one that stops before this device, leaves
                                # the new profile's mappings. Waiting would hold the service's only
                                # worker for as long as the move runs.
                                logger.critical(
                                    "INCONSISTENCY: a move holds the lock; the IoT Agent attributes "
                                    "of device=%s urn=%s were not put back (profile %s -> %s); if the "
                                    "move does not include this device it keeps the new profile's "
                                    "mappings; save the profile again once the move ends",
                                    external_id, urn, old_code, code)
                                return
                            except writer_move_lock.LockUnavailable:
                                if last_state is None:
                                    raise
                                fallback(lock, last_state)
                                return
                        try:
                            push_iota_attributes(tenant, urn, external_id, old_code, old_ms,
                                                 _guard(current))
                        except ApiError as exc:
                            # The state cannot even be read: leaving the new profile's mappings
                            # on the device while Orion keeps the old profile loses every reading,
                            # so push the old attributes built from the last known state.
                            if isinstance(exc, WriterStateError) or exc.step != 'db' or last_state is None:
                                raise
                            fallback(current, last_state)
                except Exception:
                    logger.critical("INCONSISTENCY: could not restore IoT Agent attributes: "
                                    "device=%s urn=%s old_profile=%s new_profile=%s",
                                    external_id, urn, old_code, code)
                    raise
            _run_undo(push_old, 'restore_iota_attributes')
        else:
            logger.critical("INCONSISTENCY: device %s keeps IoT Agent attributes of profile %s "
                            "but the entity still has profile %s (previous profile not readable)",
                            external_id, code, old_code)

    if is_device:
        # The DeviceModel first: it is idempotent and never undone, so a failure here leaves the
        # agent untouched and there is nothing to restore; and the window between the agent push and
        # the Device write shrinks to one request.
        guard()
        device_models.ensure_device_model(tenant, profile)
    try:
        pushed_state = push_iota_attributes(tenant, urn, external_id, code, new_ms, guard)
    except KeyError:
        raise ApiError(400, 'Profile has measurements without sdmAttribute')
    except ValueError as exc:
        raise ApiError(400, str(exc))
    except WriterStateError as exc:
        restore(exc.last_state)
        raise
    except ApiError:
        raise
    except Exception as exc:
        logger.error("IoT Agent update failed: device=%s error=%s", external_id, type(exc).__name__)
        raise ApiError(502, 'IoT Agent device update failed', 'iota_device')
    failure = None
    profile_codes = {'profileCode': {'type': 'Property', 'value': code},
                     'sensorType': {'type': 'Property', 'value': code}}
    # Device only: `refDeviceModel`, `deviceCategory` and `controlledProperty` are attributes of the
    # official Device, not of ManufacturingMachine (whose own equivalent is another model), and
    # `installedAt` and `status` are OFFICIAL on ManufacturingMachine. A machine keeps exactly the
    # request it always had: do not unify the two branches.
    try:
        if is_device:
            guard()
            device_models.write_profile_attrs(tenant, urn, profile, extra=profile_codes)
        else:
            guard()
            resp = requests.patch(
                f"{ORION_URL}/ngsi-ld/v1/entities/{urn}/attrs", json=profile_codes,
                headers=inject_fiware_headers({}, tenant=tenant, has_context_in_body=False), timeout=10)
            if resp.status_code not in (200, 204):
                failure = ApiError(502, 'Entity update failed', 'orion_profile')
    except device_models.StaleAttributeError:
        raise
    except ApiError as exc:
        failure = exc
    except Exception as exc:
        logger.error("profile write failed: device=%s error=%s", external_id, type(exc).__name__)
        failure = ApiError(502, 'Entity update failed', 'orion_profile')
    if failure is not None:
        restore(pushed_state)
        raise failure


def is_on_profile(tenant: str, entity: Optional[dict], code: str) -> bool:
    """The one rule for "this device is on that profile" when a profile is edited."""
    return entity_profile_code(tenant, entity) == code


def links_on_profile(tenant: str, code: str) -> list:
    """The datalogger link rows of the tenant's devices on a profile.

    Only devices with a datalogger link: those are the ones that own an IoT Agent device.
    """
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT datalogger_id, device_urn, external_id FROM iot_datalogger_devices "
                    "WHERE tenant_id = %s ORDER BY external_id", (tenant,))
        links = cur.fetchall()
    if not links:
        return []
    entities = _orion_devices(tenant, [d['device_urn'] for d in links])
    return [d for d in links if is_on_profile(tenant, entities.get(d['device_urn']), code)]


def reprovision_profile_devices(tenant: str, code: str, measurements: list, lock=None) -> dict:
    """Push a profile's new attributes to the IoT Agent device of every device on it.

    Best effort per device: a failure is logged and listed, never raised. With a `lock` every PUT
    is guarded and, once the lock is lost, the remaining devices are listed in `failed` untouched.
    """
    out = {'reprovisioned': [], 'failed': []}
    guard = _guard(lock) if lock is not None else _no_check
    links = links_on_profile(tenant, code)
    for index, link in enumerate(links):
        urn = link['device_urn']
        if lock is not None and not _lock_alive(lock):
            logger.error("profile re-provision stopped, the writer move lock was lost: profile=%s "
                         "not_done=%d", code, len(links) - index)
            out['failed'].extend(rest['device_urn'] for rest in links[index:])
            break
        try:
            push_iota_attributes(tenant, urn, link['external_id'], code, measurements, guard)
            out['reprovisioned'].append(urn)
        except Exception as exc:
            logger.error("profile re-provision failed: device=%s profile=%s error=%s",
                         link['external_id'], code, type(exc).__name__)
            out['failed'].append(urn)
    return out
