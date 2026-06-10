"""Parcel activation logic — DB ops, internal dispatch, quota check."""

import json
import logging
import os

import psycopg2
import psycopg2.extras
import requests

logger = logging.getLogger(__name__)

POSTGRES_URL = os.getenv("POSTGRES_URL", "")


def _get_db():
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL not configured")
    conn = psycopg2.connect(POSTGRES_URL)
    return conn


def _get_limits_for_tenant(tenant_id: str) -> dict:
    """Get tenant limits from PostgreSQL."""
    try:
        conn = _get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT max_parcels, max_area_hectares, plan_level FROM tenants WHERE tenant_id = %s",
            (tenant_id,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else {}
    except Exception as e:
        logger.warning("Failed to get tenant limits: %s", e)
        return {}


def _check_parcel_limit(tenant_id: str, module_id: str) -> tuple:
    """Check if tenant can activate another parcel for this module.

    Returns (ok: bool, reason: str).
    """
    limits = _get_limits_for_tenant(tenant_id)
    max_parcels = limits.get("max_parcels", 10) or 10

    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM tenant_parcel_modules WHERE tenant_id = %s AND module_id = %s AND enabled = true",
            (tenant_id, module_id),
        )
        count = cur.fetchone()[0]
        cur.close()
        conn.close()

        if count >= max_parcels:
            return False, f"Parcel limit reached ({count}/{max_parcels})"
        return True, ""
    except Exception as e:
        logger.error("Error checking parcel limit: %s", e)
        return False, str(e)


def _dispatch_to_module(
    module_id: str,
    tenant_id: str,
    parcel_id: str,
    parcel_name: str = "",
    action: str = "activate",
) -> tuple:
    """POST to module's internal setup-parcel endpoint.

    Returns (status_code: int, body: dict).
    """
    internal_secret = os.getenv("INTERNAL_SERVICE_SECRET", "")

    # Try to get webhook URL from module metadata
    webhook_url = None
    try:
        conn = _get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT metadata FROM marketplace_modules WHERE id = %s",
            (module_id,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            metadata = row.get("metadata") or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            webhook_url = metadata.get("setup_parcel_url") or metadata.get("lifecycle_webhook_url")
    except Exception as e:
        logger.warning("Failed to fetch module metadata: %s", e)

    if not webhook_url:
        # Convention-based fallback: http://{module_id}-backend-service:8000/api/internal/setup-parcel
        webhook_url = f"http://{module_id}-backend-service:8000/api/internal/setup-parcel"

    payload = {
        "parcel_id": parcel_id,
        "tenant_id": tenant_id,
        "parcel_name": parcel_name,
        "action": action,
    }

    headers = {
        "Content-Type": "application/json",
        "X-Internal-Service-Secret": internal_secret,
    }

    try:
        resp = requests.post(webhook_url, json=payload, headers=headers, timeout=15)
        return resp.status_code, resp.json() if resp.content else {}
    except requests.RequestException as e:
        logger.error("Dispatch to %s failed: %s", webhook_url, e)
        return 503, {"error": str(e)}


def _persist_activation(
    tenant_id: str, parcel_id: str, module_id: str, enabled: bool = True
) -> bool:
    """Persist activation state to PostgreSQL."""
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tenant_parcel_modules (tenant_id, parcel_id, module_id, enabled)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (tenant_id, parcel_id, module_id)
            DO UPDATE SET enabled = %s, updated_at = NOW()
            """,
            (tenant_id, parcel_id, module_id, enabled, enabled),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to persist activation: %s", e)
        return False


def _get_activated_modules(tenant_id: str, parcel_id: str) -> list:
    """List enabled module IDs for a parcel."""
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT module_id FROM tenant_parcel_modules WHERE tenant_id = %s AND parcel_id = %s AND enabled = true",
            (tenant_id, parcel_id),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [r[0] for r in rows]
    except Exception as e:
        logger.error("Failed to list activated modules: %s", e)
        return []
