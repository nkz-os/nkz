"""Parcel activation — quota (tier_quotas, fail-open), DB state, internal dispatch.

State machine per (tenant, parcel, module): setup_status pending -> ok | error.
Retry path: activation POST is idempotent — re-POST re-dispatches.
"""

import json
import logging
import os

import psycopg2
import psycopg2.extras
import requests

from common.tier_quotas import LEVEL_TO_TIER, quotas_for_tier

logger = logging.getLogger(__name__)

POSTGRES_URL = os.getenv("POSTGRES_URL", "")
INTERNAL_SERVICE_SECRET = os.getenv("INTERNAL_SERVICE_SECRET", "")
DISPATCH_TIMEOUT_S = 5  # short: this runs inside a gunicorn worker


def _get_db():
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL not configured")
    return psycopg2.connect(POSTGRES_URL)


def _max_parcels_for_tenant(tenant_id: str):
    """max_parcels from tier_quotas (None = unlimited). Raises on DB error."""
    conn = _get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT plan_level FROM tenants WHERE tenant_id = %s", (tenant_id,)
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    level = (row or {}).get("plan_level", 0) or 0
    tier = LEVEL_TO_TIER.get(level, "free")
    return quotas_for_tier(tier).get("max_parcels", 0)


def _count_active_parcels(tenant_id: str, module_id: str) -> int:
    conn = _get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM tenant_parcel_modules"
            " WHERE tenant_id = %s AND module_id = %s AND enabled = true",
            (tenant_id, module_id),
        )
        count = cur.fetchone()[0]
        cur.close()
        return count
    finally:
        conn.close()


def check_parcel_limit(tenant_id: str, module_id: str) -> tuple:
    """(ok: bool, reason: str). Fail-open on infrastructure errors (platform convention)."""
    try:
        max_parcels = _max_parcels_for_tenant(tenant_id)
        if max_parcels is None:  # unlimited tier
            return True, ""
        count = _count_active_parcels(tenant_id, module_id)
        if count >= max_parcels:
            return False, f"Parcel limit reached ({count}/{max_parcels})"
        return True, ""
    except Exception as e:
        logger.error("Quota check failed (fail-open): %s", e)
        return True, ""


def is_module_installed(tenant_id: str, module_id: str) -> bool:
    try:
        conn = _get_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM tenant_installed_modules"
                " WHERE tenant_id = %s AND module_id = %s AND is_enabled = true",
                (tenant_id, module_id),
            )
            found = cur.fetchone() is not None
            cur.close()
            return found
        finally:
            conn.close()
    except Exception as e:
        logger.error("Installed-module check failed (fail-open): %s", e)
        return True


def _get_setup_url(module_id: str):
    """setup_parcel_url from marketplace_modules.metadata. None if absent."""
    try:
        conn = _get_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT metadata->>'setup_parcel_url' FROM marketplace_modules"
                " WHERE id = %s",
                (module_id,),
            )
            row = cur.fetchone()
            cur.close()
            return row[0] if row and row[0] else None
        finally:
            conn.close()
    except Exception as e:
        logger.error("Failed to read setup_parcel_url for %s: %s", module_id, e)
        return None


def dispatch_to_module(
    module_id: str,
    tenant_id: str,
    parcel_id: str,
    parcel_name: str = "",
    action: str = "activate",
) -> tuple:
    """POST to the module's internal setup-parcel endpoint.

    The URL MUST be declared in marketplace_modules.metadata.setup_parcel_url.
    No convention-based fallback — fail fast with an actionable error.
    """
    url = _get_setup_url(module_id)
    if not url:
        return 502, {
            "error": (
                f"Module '{module_id}' has no setup_parcel_url in"
                " marketplace_modules.metadata — register it before activating"
            )
        }
    payload = {
        "parcel_id": parcel_id,
        "tenant_id": tenant_id,
        "parcel_name": parcel_name,
        "action": action,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Internal-Service-Secret": INTERNAL_SERVICE_SECRET,
    }
    try:
        resp = requests.post(
            url, json=payload, headers=headers, timeout=DISPATCH_TIMEOUT_S
        )
        body = resp.json() if resp.content else {}
        return resp.status_code, body
    except requests.RequestException as e:
        logger.error("Dispatch to %s failed: %s", url, e)
        return 503, {"error": str(e)}


def persist_activation(
    tenant_id: str,
    parcel_id: str,
    module_id: str,
    enabled: bool,
    setup_status: str,
    last_error: str | None = None,
) -> bool:
    try:
        conn = _get_db()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO tenant_parcel_modules
                    (tenant_id, parcel_id, module_id, enabled, setup_status, last_error)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, parcel_id, module_id)
                DO UPDATE SET enabled = EXCLUDED.enabled,
                              setup_status = EXCLUDED.setup_status,
                              last_error = EXCLUDED.last_error,
                              updated_at = NOW()
                """,
                (tenant_id, parcel_id, module_id, enabled, setup_status, last_error),
            )
            conn.commit()
            cur.close()
            return True
        finally:
            conn.close()
    except Exception as e:
        logger.error("Failed to persist activation: %s", e)
        return False


def get_activated_modules(tenant_id: str, parcel_id: str) -> list:
    try:
        conn = _get_db()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "SELECT module_id, enabled, setup_status, last_error, updated_at"
                " FROM tenant_parcel_modules"
                " WHERE tenant_id = %s AND parcel_id = %s",
                (tenant_id, parcel_id),
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            for r in rows:
                if r.get("updated_at"):
                    r["updated_at"] = r["updated_at"].isoformat()
            return rows
        finally:
            conn.close()
    except Exception as e:
        logger.error("Failed to list activated modules: %s", e)
        return []
