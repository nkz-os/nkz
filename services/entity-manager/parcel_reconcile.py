"""Parcel lifecycle reconcile engine.

Diffs live AgriParcel ids in Orion against tenant_parcel_modules rows and
known derived-entity types, then provisions / retries / tears down / sweeps.
Truth is Orion; tenant_parcel_modules is convergence state.

HARD RULE (false-zero): if the live-parcel query is not HTTP 200, the tenant
is SKIPPED. We never interpret an error as "zero parcels" — that would tear
down the whole tenant (incident 2026-06-16).
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from parcel_activation import _get_db, dispatch_to_module

logger = logging.getLogger(__name__)

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
RECONCILE_INTERVAL_S = int(os.getenv("RECONCILE_INTERVAL_S", "25"))
BACKSTOP_ENABLED = os.getenv("RECONCILE_BACKSTOP_ENABLED", "true").lower() == "true"
ORION_TIMEOUT_S = 15
PAGE_SIZE = 1000


def _orion_headers(tenant_id: str) -> dict:
    """NGSI-LD + FIWARE headers via the platform's canonical injector.

    entity-manager does NOT bundle nkz-platform-sdk, so per CLAUDE.md directive 3
    ("For services that cannot import the SDK, use inject_fiware_headers() from
    common/auth_middleware.py") we use the canonical injector. It sets NGSILD-Tenant
    + Fiware-Service (canonically normalized) + Fiware-ServicePath, and adds the
    @context Link from CONTEXT_URL (Content-Type application/json) — exactly what
    AgriParcel type-expansion and the false-zero guard require.

    No silent fallback: in production common.auth_middleware always imports; if it
    ever failed we want a hard error here rather than a degraded (@context-less)
    header set that could trigger the false-zero catastrophe.
    """
    from common.auth_middleware import inject_fiware_headers

    headers: dict = {}
    inject_fiware_headers(headers, tenant=tenant_id)
    return headers


def get_live_parcel_ids(tenant_id: str):
    """Set of live AgriParcel URNs for the tenant, or None on ANY query error.

    None means 'unknown' -> caller MUST skip teardown/backstop for this tenant.
    """
    headers = _orion_headers(tenant_id)
    ids: set[str] = set()
    offset = 0
    try:
        while True:
            resp = requests.get(
                f"{ORION_URL}/ngsi-ld/v1/entities",
                params={"type": "AgriParcel", "limit": PAGE_SIZE,
                        "offset": offset, "attrs": "id"},
                headers=headers,
                timeout=ORION_TIMEOUT_S,
            )
            if resp.status_code != 200:
                logger.error(
                    "Live-parcel query for %s returned %s — SKIP tenant",
                    tenant_id, resp.status_code,
                )
                return None
            batch = resp.json()
            if not isinstance(batch, list):
                logger.error("Live-parcel query for %s gave non-list — SKIP", tenant_id)
                return None
            for e in batch:
                if e.get("id"):
                    ids.add(e["id"])
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return ids
    except requests.RequestException as exc:
        logger.error("Live-parcel query for %s failed: %s — SKIP tenant", tenant_id, exc)
        return None


def get_active_tenants() -> list[str]:
    conn = _get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT tenant_id FROM tenants WHERE tenant_id IS NOT NULL")
        rows = [r[0] for r in cur.fetchall()]
        cur.close()
        return rows
    finally:
        conn.close()


def get_auto_provision_modules() -> list[str]:
    conn = _get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM marketplace_modules"
            " WHERE metadata->>'auto_provision' = 'true'"
        )
        rows = [r[0] for r in cur.fetchall()]
        cur.close()
        return rows
    finally:
        conn.close()


def get_rows(tenant_id: str) -> list[dict]:
    conn = _get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT tenant_id, parcel_id, module_id, setup_status,"
            " retry_count, next_retry_at"
            " FROM tenant_parcel_modules WHERE tenant_id = %s",
            (tenant_id,),
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        return rows
    finally:
        conn.close()


_BACKOFF = [30, 120, 600, 3600]


def backoff_seconds(retry_count: int) -> int:
    idx = min(retry_count, len(_BACKOFF) - 1)
    return _BACKOFF[idx]


def is_due_for_retry(row: dict, now: datetime) -> bool:
    nxt = row.get("next_retry_at")
    return nxt is None or nxt <= now


def mark_ok(tenant_id: str, parcel_id: str, module_id: str) -> None:
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tenant_parcel_modules SET setup_status='ok',"
                " last_error=NULL, retry_count=0, next_retry_at=NULL, updated_at=NOW()"
                " WHERE tenant_id=%s AND parcel_id=%s AND module_id=%s",
                (tenant_id, parcel_id, module_id),
            )
        conn.commit()
    finally:
        conn.close()


def mark_error(tenant_id: str, parcel_id: str, module_id: str,
               err: str, retry_count: int) -> None:
    nxt = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds(retry_count))
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tenant_parcel_modules SET setup_status='error',"
                " last_error=%s, retry_count=%s, next_retry_at=%s, updated_at=NOW()"
                " WHERE tenant_id=%s AND parcel_id=%s AND module_id=%s",
                (err[:1000], retry_count + 1, nxt, tenant_id, parcel_id, module_id),
            )
        conn.commit()
    finally:
        conn.close()


def insert_pending(tenant_id: str, parcel_id: str, module_id: str) -> None:
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenant_parcel_modules"
                " (tenant_id, parcel_id, module_id, enabled, setup_status, next_retry_at)"
                " VALUES (%s,%s,%s,true,'pending',NOW())"
                " ON CONFLICT (tenant_id, parcel_id, module_id) DO NOTHING",
                (tenant_id, parcel_id, module_id),
            )
        conn.commit()
    finally:
        conn.close()


def delete_row(tenant_id: str, parcel_id: str, module_id: str) -> None:
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tenant_parcel_modules"
                " WHERE tenant_id=%s AND parcel_id=%s AND module_id=%s",
                (tenant_id, parcel_id, module_id),
            )
        conn.commit()
    finally:
        conn.close()
