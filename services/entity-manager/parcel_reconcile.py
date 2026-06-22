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
CONTEXT_URL = os.getenv("CONTEXT_URL", "")
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


def _tenant_has_agriparcel_type(tenant_id: str) -> bool:
    """True if the tenant's Orion store reports an AgriParcel type.

    Cross-check for the false-zero guard: if the live-parcel query returns 0
    but this says AgriParcel IS present, the query is lying — do NOT trust the
    empty result. Best-effort: any error returns False (the query-level guards
    remain the primary protection).
    """
    try:
        resp = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/types",
            headers=_orion_headers(tenant_id),
            timeout=ORION_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return False
        body = resp.json()
        type_list = body.get("typeList", body) if isinstance(body, dict) else body
        if not isinstance(type_list, list):
            return False
        return any("AgriParcel" in str(t) for t in type_list)
    except requests.RequestException:
        return False


def get_live_parcel_ids(tenant_id: str):
    """Set of live AgriParcel URNs for the tenant, or None on ANY query error.

    None means 'unknown' -> caller MUST skip teardown/backstop for this tenant.

    SEMANTIC false-zero guard: a context-less ``?type=AgriParcel`` query expands to
    the default vocab and returns HTTP 200 with an EMPTY list (the entities live
    under the expanded SAREF type). That false empty would pass the transport guard
    and trigger mass teardown. The @context Link is only added when CONTEXT_URL is
    set, so if it is missing we refuse to query and return None (skip the tenant)
    rather than risk a false zero (incident 2026-06-16).
    """
    if not CONTEXT_URL:
        logger.error(
            "CONTEXT_URL not set — refusing AgriParcel query for %s; a context-less"
            " query returns a FALSE empty list -> mass teardown. SKIP tenant.",
            tenant_id,
        )
        return None
    headers = _orion_headers(tenant_id)
    ids: set[str] = set()
    offset = 0
    try:
        while True:
            resp = requests.get(
                f"{ORION_URL}/ngsi-ld/v1/entities",
                # NB: do NOT send attrs=id. Orion-LD treats `attrs` as an
                # attribute filter, and since no AgriParcel has a property
                # literally named "id" it returns ZERO entities -> a
                # false-empty live set -> the backstop deletes every derived
                # entity of every parcel (incident 2026-06-22). We only read
                # e["id"], which is always present.
                params={"type": "AgriParcel", "limit": PAGE_SIZE,
                        "offset": offset},
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
        # Defense-in-depth false-zero guard: an EMPTY result is only trusted
        # when the tenant genuinely has no AgriParcel type. If the type IS
        # present but the query returned nothing, the query is lying (e.g. a
        # bad projection/context) — returning {} here would make the backstop
        # delete every derived entity. Return None (skip) instead.
        if not ids and _tenant_has_agriparcel_type(tenant_id):
            logger.error(
                "Live-parcel query for %s returned 0 but AgriParcel type IS present"
                " — suspected false-zero, SKIP tenant (no destructive ops)",
                tenant_id,
            )
            return None
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


# Backstop registry. ref must resolve to an AgriParcel-typed reference.
# Validate against `GET /ngsi-ld/v1/types` of the tenant before trusting it.
DERIVED_TYPE_REGISTRY = [
    {"type": "VegetationIndex", "ref_keys": ["hasAgriParcel"]},
    {"type": "EOProduct", "ref_keys": ["hasAgriParcel"]},
    {"type": "CropHealthAssessment", "ref_keys": ["hasAgriParcel"]},
    {"type": "CropHealthZoneAssessment", "ref_keys": ["hasAgriParcel"]},
    {"type": "RiskAssessment", "ref_keys": ["targetEntityId"],
     "require_target_type": "AgriParcel"},
    {"type": "DataProcessingJob", "ref_keys": ["hasAgriParcel", "refAgriParcel"]},
    {"type": "AgriParcelOperation", "ref_keys": ["hasAgriParcel", "refAgriParcel"]},
    {"type": "DigitalAsset", "ref_keys": ["hasAgriParcel", "refAgriParcel"]},
]


def _attr_scalar(node):
    """Extract object (Relationship) or value (Property) from a normalized attr."""
    if isinstance(node, dict):
        return node.get("object", node.get("value"))
    return node


def resolve_parcel_ref(entity: dict, spec: dict):
    """Return the parcel URN this entity references, or None if not parcel-typed."""
    req_type = spec.get("require_target_type")
    if req_type:
        tt = _attr_scalar(entity.get("targetEntityType"))
        if tt != req_type:
            return None
    for key in spec["ref_keys"]:
        if key in entity:
            ref = _attr_scalar(entity[key])
            if isinstance(ref, str) and ref:
                return ref
    return None


def query_entities(tenant_id: str, etype: str) -> list:
    """All entities of a type (normalized) for a tenant. [] on error (logged)."""
    headers = _orion_headers(tenant_id)
    out: list = []
    offset = 0
    try:
        while True:
            resp = requests.get(
                f"{ORION_URL}/ngsi-ld/v1/entities",
                params={"type": etype, "limit": PAGE_SIZE, "offset": offset},
                headers=headers,
                timeout=ORION_TIMEOUT_S,
            )
            if resp.status_code != 200:
                logger.error("query_entities %s/%s -> %s", tenant_id, etype, resp.status_code)
                return []
            batch = resp.json()
            if not isinstance(batch, list):
                return []
            out.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return out
    except requests.RequestException as exc:
        logger.error("query_entities %s/%s failed: %s", tenant_id, etype, exc)
        return []


def find_backstop_orphans(tenant_id: str, live_ids: set, owned_parcel_ids: set) -> list:
    """Ids of derived entities whose parcel-typed ref is neither live nor owned by a row."""
    orphans: list = []
    for spec in DERIVED_TYPE_REGISTRY:
        for e in query_entities(tenant_id, spec["type"]):
            ref = resolve_parcel_ref(e, spec)
            if ref is None or ref in live_ids or ref in owned_parcel_ids:
                continue
            orphans.append(e["id"])
    return orphans


def delete_entities(tenant_id: str, ids: list) -> int:
    """Batch-delete entities by id via entityOperations/delete. Returns count attempted."""
    if not ids:
        return 0
    headers = _orion_headers(tenant_id)
    headers["Content-Type"] = "application/json"
    done = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            resp = requests.post(
                f"{ORION_URL}/ngsi-ld/v1/entityOperations/delete",
                json=chunk, headers=headers, timeout=ORION_TIMEOUT_S,
            )
            if resp.status_code in (200, 204):
                done += len(chunk)
                logger.warning("Backstop deleted %d orphans for %s", len(chunk), tenant_id)
            elif resp.status_code == 207:
                # NGSI-LD batch partial success: some deleted, some failed (e.g.
                # already-gone). Count as attempted; surface the body for triage.
                done += len(chunk)
                logger.warning(
                    "Backstop partial delete (207) for %s: %s",
                    tenant_id, resp.text[:300],
                )
            else:
                logger.error("Backstop delete %s -> %s", tenant_id, resp.status_code)
        except requests.RequestException as exc:
            logger.error("Backstop delete failed for %s: %s", tenant_id, exc)
    return done


def reconcile_tenant(tenant_id: str) -> dict:
    """One convergence pass for a tenant. Idempotent."""
    metrics = {"tenant": tenant_id, "skipped": False, "provisioned": 0,
               "retried": 0, "torn_down": 0, "orphans_deleted": 0, "errors": 0}

    live = get_live_parcel_ids(tenant_id)
    if live is None:  # false-zero guard — do nothing destructive
        metrics["skipped"] = True
        logger.warning("Reconcile skipped for %s (live parcels unknown)", tenant_id)
        return metrics

    rows = get_rows(tenant_id)
    row_index = {(r["parcel_id"], r["module_id"]): r for r in rows}
    owned_parcel_ids = {r["parcel_id"] for r in rows}
    now = datetime.now(timezone.utc)

    # 3. PROVISION: auto_provision modules for live parcels with no row
    auto_modules = get_auto_provision_modules()
    for parcel_id in live:
        for module_id in auto_modules:
            if (parcel_id, module_id) in row_index:
                continue
            insert_pending(tenant_id, parcel_id, module_id)
            _dispatch(tenant_id, parcel_id, module_id, "activate", 0, metrics, "provisioned")

    # 4. RETRY: rows in error/pending that are due
    for r in rows:
        if r["parcel_id"] not in live:
            continue  # handled by teardown below
        if r["setup_status"] in ("error", "pending") and is_due_for_retry(r, now):
            _dispatch(tenant_id, r["parcel_id"], r["module_id"], "activate",
                      r.get("retry_count", 0), metrics, "retried")

    # 5. TEARDOWN: rows whose parcel is gone
    for r in rows:
        if r["parcel_id"] in live:
            continue
        status, _ = dispatch_to_module(
            module_id=r["module_id"], tenant_id=tenant_id,
            parcel_id=r["parcel_id"], action="teardown",
        )
        if status in (200, 201, 204):
            delete_row(tenant_id, r["parcel_id"], r["module_id"])
            metrics["torn_down"] += 1
        else:
            mark_error(tenant_id, r["parcel_id"], r["module_id"],
                       f"teardown {status}", r.get("retry_count", 0))
            metrics["errors"] += 1

    # 6. BACKSTOP: legacy orphans with no owning row
    if BACKSTOP_ENABLED:
        orphans = find_backstop_orphans(tenant_id, live, owned_parcel_ids)
        metrics["orphans_deleted"] = delete_entities(tenant_id, orphans)

    logger.info("Reconcile %s: %s", tenant_id, metrics)
    return metrics


def _dispatch(tenant_id, parcel_id, module_id, action, retry_count, metrics, key):
    status, result = dispatch_to_module(
        module_id=module_id, tenant_id=tenant_id, parcel_id=parcel_id, action=action,
    )
    if status in (200, 201, 204):
        mark_ok(tenant_id, parcel_id, module_id)
        metrics[key] += 1
    else:
        mark_error(tenant_id, parcel_id, module_id,
                   str(result)[:500], retry_count)
        metrics["errors"] += 1


def run_once() -> None:
    """One reconcile pass over all active tenants. Per-tenant isolated."""
    try:
        tenants = get_active_tenants()
    except Exception as exc:
        logger.error("Could not list tenants: %s", exc)
        return
    for tenant_id in tenants:
        try:
            reconcile_tenant(tenant_id)
        except Exception as exc:
            logger.error("Reconcile crashed for %s: %s", tenant_id, exc, exc_info=True)


def run_loop() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not CONTEXT_URL:
        logger.error(
            "CONTEXT_URL is not set — every tenant will be SKIPPED by the false-zero"
            " guard and NOTHING will reconcile. Set CONTEXT_URL on the worker."
        )
    logger.info("Parcel reconcile worker starting (interval=%ss, backstop=%s)",
                RECONCILE_INTERVAL_S, BACKSTOP_ENABLED)
    while True:
        run_once()
        time.sleep(RECONCILE_INTERVAL_S)


if __name__ == "__main__":
    run_loop()
