"""The gateway repair job: converge every gateway, then remove what no row owns.

    python -m gateway_reconcile [--tenant T] [--dry-run]

Run every 15 minutes. It DELETES in the context broker and the IoT Agent, so it is conservative.

Per tenant (the tenants of `tenants` with no `deleted_at` that `NKZ_GATEWAY_DEVICES` names; every
query runs under that tenant's RLS context, `iot_dataloggers` has no cross-tenant read):
  1. read the table of dataloggers; every row goes through `gateways.project_gateway_detailed`,
     one after the other in this thread (a row with no gateway id gets one);
  2. read the table AGAIN (ids assigned in step 1 are in it), list the network Devices in Orion and
     the devices of the IoT Agent, and remove the ones whose id is `gw-<12 lowercase hex>` and is
     not in the table: agent device, the gateway's DeviceMeasurements, then the entity (R13).

Deletion safety, all of it required:
  * the id must match `gw-[0-9a-f]{12}` exactly (`fullmatch`), the Orion entity must be a `Device`
    whose id is the gateway URN of THIS tenant and whose `deviceCategory` holds `network`; an agent
    device must not say it is anything but a `Device`. A VPN gateway, a sensor, a machine or
    `gw-XYZ` never qualifies. The Orion entity is removed only when it was seen as such;
  * nothing is removed for a tenant whose table could not be read (twice: before and after the
    projections), and an error is never read as "no rows". A listing that fails or does not add up
    removes nothing on its own side;
  * at most `MAX_REMOVALS` removals per run, across tenants, in id order; a failed removal counts.
    The rest is logged as `deferred` and the next run takes it;
  * right before removing, under `pg_advisory_xact_lock(hashtext('gateway-orphan:<id>'))`, the table
    is read once more: an id that appeared in it since the listing is NOT removed, and neither is
    anything of a tenant that was suspended meanwhile. The projection commits a new id before it
    creates anything in Orion, so an entity that exists always has its row committed first.
    That lock is the only one held during the removal (no ingestion or row lock, R1b).

One job lock, `pg_try_advisory_lock(hashtext('gateway-reconcile'))` on a dedicated connection: a
second run exits 0 at once. `--dry-run` reads and prints what it would do; it writes nothing.
Exit codes: 0 done (also when another run holds the lock), 1 any tenant or gateway failed, 2 bad
arguments. Each action is one log line (tenant, ids, outcome); exception TYPES only, never a
message, a DSN or an apikey.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import psycopg2
import requests

import dataloggers
import gateways
from common.active_tenants import ACTIVE_TENANT_IDS_SQL

logger = logging.getLogger("gateway_reconcile")

JOB_LOCK = "gateway-reconcile"
MAX_REMOVALS = 50
AGENT_PAGE = 200
AGENT_TIMEOUT = 10
LOCK_TIMEOUT = "30s"
_CONNECT_TIMEOUT = 10


class _Budget:
    """The removals left in this run."""

    def __init__(self, limit: int):
        self.left = limit

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


# ── IO seams (the tests replace them) ────────────────────────────────────────────────────

def list_active_tenants() -> List[str]:
    url = os.environ.get("POSTGRES_URL")
    if not url:
        raise RuntimeError("POSTGRES_URL is not set")
    conn = psycopg2.connect(url, connect_timeout=_CONNECT_TIMEOUT)
    try:
        cur = conn.cursor()
        cur.execute(ACTIVE_TENANT_IDS_SQL)
        rows = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.rollback()
        return sorted(rows)
    finally:
        conn.close()


@contextmanager
def job_lock() -> Iterator[bool]:
    """Yields whether this run holds the job lock; it is held while the block runs.

    A session lock on a dedicated connection that stays open (and idle, not in a transaction):
    closing it releases the lock. Raises when the lock cannot be asked for.
    """
    url = os.environ.get("POSTGRES_URL")
    if not url:
        raise RuntimeError("POSTGRES_URL is not set")
    conn = psycopg2.connect(url, connect_timeout=_CONNECT_TIMEOUT, keepalives=1, keepalives_idle=30,
                            keepalives_interval=10, keepalives_count=3)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (JOB_LOCK,))
            got = bool(cur.fetchone()[0])
        conn.commit()
        yield got
    finally:
        conn.close()


def read_table(tenant: str) -> List[Tuple[str, Optional[str]]]:
    """(datalogger id, gateway external id or None) of every row of the tenant; raises on any error."""
    with dataloggers.get_db_connection_with_tenant(tenant) as conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, gateway_external_id FROM iot_dataloggers "
                        "WHERE tenant_id = %s ORDER BY created_at, id", (tenant,))
            rows = [(str(r[0]), r[1]) for r in cur.fetchall()]
        finally:
            conn.rollback()
    return rows


def list_orion_devices(tenant: str) -> List[dict]:
    """Every `Device` of the tenant, read with the platform context; raises unless it adds up."""
    import device_models
    return device_models.list_entities(tenant, "Device")


def list_agent_devices(tenant: str) -> List[dict]:
    """The IoT Agent's devices of the tenant (`GET /iot/devices`, paginated); raises unless it adds up."""
    url, headers = dataloggers.iota_url(), dataloggers.iota_headers(tenant)
    found: Dict[str, dict] = {}
    offset, total = 0, None
    while total is None or offset < total:
        resp = requests.get(f"{url}/iot/devices", headers=headers,
                            params={"limit": AGENT_PAGE, "offset": offset}, timeout=AGENT_TIMEOUT)
        if resp.status_code != 200:
            raise RuntimeError(f"agent listing status {resp.status_code}")
        try:
            body = resp.json()
            total, page = int(body["count"]), body["devices"]
            if not isinstance(page, list) or (offset < total and not page):
                raise ValueError("page")
            found.update({d["device_id"]: d for d in page})
        except (ValueError, KeyError, TypeError):
            raise RuntimeError("agent listing is not a device listing") from None
        offset += AGENT_PAGE
    if len(found) != total:
        raise RuntimeError(f"agent reports {total} devices and listed {len(found)}")
    return list(found.values())


@contextmanager
def orphan_guard(tenant: str, external_id: str) -> Iterator[Optional[str]]:
    """Holds `gateway-orphan:<id>` and says whether the id may still be removed.

    Yields None when it may, else the reason (it is in the table now, or the tenant is suspended).
    Raises when it cannot decide: the caller then removes nothing. The connection stays inside the
    block, so the lock covers the removal; it is released by the rollback.
    """
    with dataloggers.get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT set_config('lock_timeout', %s, true)", (LOCK_TIMEOUT,))
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"gateway-orphan:{external_id}",))
            cur.execute("SELECT 1 FROM tenants WHERE tenant_id = %s AND deleted_at IS NOT NULL", (tenant,))
            if cur.fetchone():
                verdict: Optional[str] = "tenant suspended"
            else:
                cur.execute("SELECT 1 FROM iot_dataloggers WHERE tenant_id = %s AND gateway_external_id = %s",
                            (tenant, external_id))
                verdict = "now in the table" if cur.fetchone() else None
            yield verdict
        finally:
            conn.rollback()


def run_removal(tenant: str, external_id: str, entity: bool) -> List[str]:
    """R13's order; returns the steps that failed (each is tried whatever the ones before did)."""
    failed: List[str] = []
    for step, call in gateways.removal_steps(tenant, external_id, entity=entity):
        try:
            call()
        except Exception as exc:
            logger.warning("gateway repair: removal step failed: tenant=%s id=%s step=%s error=%s",
                           tenant, external_id, step, type(exc).__name__)
            failed.append(step)
    return failed


# ── what may be removed ──────────────────────────────────────────────────────────────────

def _categories(entity: dict) -> list:
    attr = entity.get("deviceCategory")
    value = attr.get("value") if isinstance(attr, dict) else None
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def orion_candidates(tenant: str, entities: Sequence[Any]) -> List[str]:
    """External ids of the network `Device` entities that are gateways of this tenant."""
    found = []
    for entity in entities:
        if not isinstance(entity, dict) or entity.get("type") != "Device":
            continue
        entity_id = entity.get("id")
        if not isinstance(entity_id, str):
            continue
        ext = entity_id.rsplit(":", 1)[-1]
        if not gateways.is_gateway_external_id(ext) or entity_id != gateways.gateway_urn(tenant, ext):
            continue
        if "network" in _categories(entity):
            found.append(ext)
    return found


def agent_candidates(docs: Sequence[Any]) -> List[str]:
    """External ids of the agent devices shaped like a gateway that do not claim to be anything else."""
    found = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        ext = doc.get("device_id")
        if gateways.is_gateway_external_id(ext) and doc.get("entity_type") in (None, "Device"):
            found.append(ext)
    return found


# ── one tenant ───────────────────────────────────────────────────────────────────────────

def _project_all(tenant: str, rows: Sequence[Tuple[str, Optional[str]]], dry_run: bool) -> bool:
    ok = True
    for dl_id, ext in rows:
        if dry_run:
            logger.info("gateway repair: would project: tenant=%s datalogger=%s id=%s", tenant, dl_id,
                        ext or "(none yet)")
            continue
        try:
            outcome = gateways.project_gateway_detailed(tenant, dl_id)[0]
        except Exception as exc:
            outcome = f"failed:{type(exc).__name__}"
        logger.info("gateway repair: projected: tenant=%s datalogger=%s outcome=%s", tenant, dl_id, outcome)
        if outcome not in (gateways.DONE, gateways.OFF):
            ok = False
    return ok


def _sweep(tenant: str, known: set, dry_run: bool, budget: _Budget) -> bool:
    ok = True
    entity_seen: set = set()
    agent_seen: set = set()
    try:
        entity_seen = set(orion_candidates(tenant, list_orion_devices(tenant)))
    except Exception as exc:
        logger.error("gateway repair: orion listing failed, nothing removed from it: tenant=%s error=%s",
                     tenant, type(exc).__name__)
        ok = False
    try:
        agent_seen = set(agent_candidates(list_agent_devices(tenant)))
    except Exception as exc:
        logger.error("gateway repair: agent listing failed, nothing removed from it: tenant=%s error=%s",
                     tenant, type(exc).__name__)
        ok = False
    for ext in sorted((entity_seen | agent_seen) - known):
        if not budget.take():
            logger.info("gateway repair: deferred: tenant=%s id=%s outcome=deferred", tenant, ext)
            continue
        entity = ext in entity_seen
        if dry_run:
            logger.info("gateway repair: would remove: tenant=%s id=%s orion=%s agent=%s",
                        tenant, ext, entity, ext in agent_seen)
            continue
        try:
            with orphan_guard(tenant, ext) as verdict:
                if verdict is not None:
                    logger.info("gateway repair: not removed: tenant=%s id=%s outcome=%s", tenant, ext, verdict)
                    continue
                failed = run_removal(tenant, ext, entity)
        except Exception as exc:
            logger.error("gateway repair: not removed, could not decide: tenant=%s id=%s error=%s",
                         tenant, ext, type(exc).__name__)
            ok = False
            continue
        if failed:
            logger.warning("gateway repair: removal incomplete: tenant=%s id=%s outcome=failed steps=%s",
                           tenant, ext, ",".join(failed))
            ok = False
        else:
            logger.info("gateway repair: removed: tenant=%s id=%s outcome=removed", tenant, ext)
    return ok


def reconcile_tenant(tenant: str, dry_run: bool, budget: _Budget) -> bool:
    """True when everything for the tenant went through. Never raises."""
    try:
        rows = read_table(tenant)
    except Exception as exc:
        logger.error("gateway repair: table unreadable, tenant skipped: tenant=%s error=%s",
                     tenant, type(exc).__name__)
        return False
    ok = _project_all(tenant, rows, dry_run)
    try:
        rows = read_table(tenant)  # again: the projections assigned ids
    except Exception as exc:
        logger.error("gateway repair: table unreadable, nothing removed: tenant=%s error=%s",
                     tenant, type(exc).__name__)
        return False
    known = {ext for _, ext in rows if ext}
    try:
        return _sweep(tenant, known, dry_run, budget) and ok
    except Exception as exc:  # a bug in the sweep must not stop the other tenants
        logger.error("gateway repair: sweep failed: tenant=%s error=%s", tenant, type(exc).__name__)
        return False


# ── the job ──────────────────────────────────────────────────────────────────────────────

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m gateway_reconcile",
                                description="Converge the gateway Devices and remove the orphans.")
    p.add_argument("--tenant", help="only this tenant")
    p.add_argument("--dry-run", action="store_true", help="read only: print what would be done")
    return p


def _args(argv: Optional[Sequence[str]]) -> Optional[argparse.Namespace]:
    try:
        args = _parser().parse_args(argv)
    except SystemExit:
        return None
    if args.tenant is not None and not args.tenant.strip():
        return None
    return args


def run(tenant_arg: Optional[str], dry_run: bool) -> int:
    if not os.environ.get(gateways.FLAG, "").strip():
        logger.info("gateway repair: flag %s is empty, nothing to do", gateways.FLAG)
        return 0
    try:
        with job_lock() as got:
            if not got:
                logger.info("gateway repair: another run holds the job lock, exiting")
                return 0
            tenants = list_active_tenants()
            if tenant_arg is not None:
                if tenant_arg not in tenants or not gateways.enabled(tenant_arg):
                    logger.error("gateway repair: tenant is not active or not enabled: tenant=%s", tenant_arg)
                    return 1
                tenants = [tenant_arg]
            budget = _Budget(MAX_REMOVALS)
            ok = True
            for tenant in tenants:
                if not gateways.enabled(tenant):
                    continue
                ok = reconcile_tenant(tenant, dry_run, budget) and ok
            return 0 if ok else 1
    except Exception as exc:
        logger.error("gateway repair: failed: error=%s", type(exc).__name__)
        return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _args(argv)
    if args is None:
        return 2
    return run(args.tenant, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
