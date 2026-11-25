"""Move devices between the two writers of their DeviceMeasurement entities.

    python -m dm_writer_cutover --tenant T [--device URN] --to iota|em [--shape legacy|sdm]
                                [--settle SECONDS] (--dry-run | --apply)
    python -m dm_writer_cutover --tenant T [--device URN] --verify --since TIME

`--to iota` makes the IoT Agent write the DeviceMeasurement entities of a device in the
given `--shape` (required); `--to em` gives them back to the entity-manager (no shape).
After a move to `iota` that moved at least one device, `--apply` prints the line to run later:
`verify in a few minutes with: --tenant T [--device URN] --verify --since TIME` (TIME = UTC, right
after the last device moved, rounded up to the second).

`--verify` runs alone (refused with `--apply`, `--dry-run`, `--to`, `--shape`, `--settle`), is
read-only and takes `--since TIME`: ISO 8601 with `Z` or an offset, not later than now + 5 s (naive,
date-only, unparseable or future: exit 2). It reads the DeviceMeasurement entities of the tenant
from the broker WITHOUT a Link header, with `options=sysAttrs` (full type IRI, `count=true`,
paginated; a short type name without Link counts zero). An entity is "reported" when a value
attribute (`numValue`/`textValue`) has its OWN `modifiedAt` at `TIME` or later; the entity's
`modifiedAt` alone never counts (another attribute may have changed) and neither does
`dateObserved` (a logger may send old times). A missing or unreadable `modifiedAt` of the entity or
of a value attribute is a failure. An entity with no new value is listed (`not reported since` or
`value not updated since`), not judged. The printed time is the clock of the machine that ran the
move; if it is not the broker's, add the difference to `--since`. Checks:
  (a) no entity, reported or not, lacks both `numValue` and `textValue`; it also runs with
      `--device` on a device that is not on `iota` (which is itself a failure);
  (b) every REPORTED entity of an `iota` device has exactly the attribute set its shape and profile
      call for, nothing under the default vocabulary and no `TimeInstant` attribute; an entity
      with no channel in the current profile (orphan) is judged, when reported, for the keys any
      channel may carry. An `iota` device with no entity in the broker fails, and so does one
      with no reported entity at all (`delivered NOTHING since TIME`); with no `iota` device (b)
      is NOT RUN. A channel of the current profile of an `iota` device with no entity in the
      broker is listed (`no entity yet`), not a failure by itself. Entities are matched to devices by the id prefix (longest wins). An entity not
      reported yet is listed (`not reported since`), not judged and not a failure: a slow channel
      is not a fault;
  (c) the IoT Agent's own document of every `iota` device matches the expected mappings;
  (d) the `numValue`/`textValue` of reported entities carry `observedAt`.
A reported entity is judged by (b) and (d) with its NEW value attributes only; every old one is
`stale value`, listed and left out (so a numeric channel with an old `numValue` fails (b)). Any
other stale attribute is judged by (b) as usual. With no `iota` device the last line says only (a) ran.
Listings show at most 50 lines per heading and `... and N more`; the counts are
complete. The last line is `verify: OK` or `verify: FAILED (...)`.
The term to IRI mapping comes from the platform context document at CONTEXT_URL (required;
without it, or without a term, the verification fails). `--device` narrows checks (b) to (d).

`--apply` takes the exclusive writer-move lock of the tenant BEFORE it reads the plan and holds it
until the last row is committed (one dedicated connection; every row is committed on it, with
a compare-and-set on the state the plan read). A profile change or another move that holds the
lock makes it stop with exit 1 and nothing written. `--dry-run` and `--verify` take no lock and never write.
`--apply --to iota` is refused for a tenant with no `tenants` row or with `deleted_at` set;
`--to em` is the way back and is always allowed. The tenant must be given in canonical form.
`--apply --to iota` itself is still disabled (refused with exit 2, see MOVE_TO_IOTA_ENABLED);
`--dry-run`, `--verify` and `--to em --apply` work. Each outcome is printed when it happens.

Exit codes: 0 done, 1 a validation, device or verification failure, 2 bad arguments.

Order of the writes. A moment where two writers write is acceptable (a reading may appear
twice; the worker drops repeated readings only when the message carries its own time
(`TimeInstant`), without it a duplicate row is possible and accepted); a moment where nobody
writes is not.
  * to iota, per batch: PUT the new mappings of every selected device, wait ONCE (`--settle`,
    120-600 s, default 120), then per device update the row and PUT again. The wait is there because
    a notification of a message the entity-manager handled before the first PUT can still be queued
    when the row commits; the entity-manager would then skip it (a reading lost) or handle it twice.
    Right before each commit the agent's own document is read once and must still be the mappings the
    first PUT sent (a late PUT of a profile change, or a device deleted and registered again, may
    have replaced them with Device-only ones); different or unreadable: that device is not
    committed and is undone ("the agent no longer holds the first mappings").
    The second PUT, after the commit, re-reads the profile and rebuilds the mappings: a profile
    change that ran `push_iota_attributes` while the row still said `em` would otherwise leave
    Device-only mappings on a device the entity-manager now skips.
  * to em: update the row, then PUT the Device-only mappings (profile re-read after the commit).
    Between the commit and the PUT both write. The other order would leave a gap in which the agent
    has stopped and the entity-manager still skips the device. A profile change cannot overlap the
    move (it needs the shared lock); one that lost its lock may still send a late PUT built for
    `iota`, which the final PUT here corrects. There is nothing to undo: an interrupt reads the row of
    the device in flight (`em`: push; otherwise the agent was not touched) and lists the rest.

Undo (`_undo`), the only way mappings are put back. It needs the exclusive lock, checked right
before every PUT of the command. A lock found lost is replaced ONCE per run; after a loss nothing
more is committed and no first PUT is sent, the batch ends and every device with a first PUT and no
outcome is undone. The row is read through the lock and decides:
  * the state the plan read: the mappings of THAT state (an `iota` shape included, never
    Device-only) are put back, rebuilt from a fresh profile read (plan-time ones as the fallback,
    and the outcome says so);
  * the target state: the commit went through, the final PUT is sent and the device is `moved`;
  * anything else, no row, an unreadable row or no lock: the new mappings stay in the agent
    (duplicates, no gap), CRITICAL, the device is listed and the text says rerun.
SIGINT, SIGTERM and SIGHUP (unless it was ignored, as under `nohup`) during the apply phase never
raise: the handler sets a flag and writes one line to stderr. The batch looks at the flag before each device's first PUT, during the wait (slices of at
most 0.5 s), before each commit and between the devices of `--to em`; a step in progress finishes and
a committed row always gets its final PUT. After a stop (or any unexpected error) the final undo runs
to the end and every device gets exactly one outcome; exit code 1. Printing never stops the batch.
Before the apply phase (lock wait, plan) a signal is an ordinary interrupt: nothing was written.
A PUT that failed after the commit is its own outcome: the row is already updated, the device is
listed as failed with that text, and a rerun is not a no-op for it (the agent's own device document
is compared with the expected mappings, so it pushes again).

Never run inside the serving entity-manager pod (it starts no Flask app). Nothing here prints
or logs a key or a secret; only ids.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import signal
import sys
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from psycopg2.extras import RealDictCursor

import dataloggers
import writer_move_lock
from common import iot_agent
from common.tenant_utils import normalize_tenant_id
from common.device_measurement_shape import SHAPES, measurement_channels
from common.ngsi_headers import inject_fiware_headers
from db_helper import get_db_connection_with_tenant
from device_profile import entity_profile_code

logger = logging.getLogger("dm_writer_cutover")

PAGE = 1000
DEFAULT_SETTLE = 120.0
# A PUT of a profile change started under a live lock is expected to take at most about 30 s (its
# connect, send and read timeouts) plus the DNS lookup, none of which is bounded as a whole; the wait
# before a commit is a multiple of that so that such a PUT lands first. Not covered: a PUT that the
# agent applies late, after its own calls.
MIN_SETTLE = 120.0
MAX_SETTLE = 600.0
# Moving devices to the IoT Agent is switched off in the source until a move has been run and
# verified against a live broker; only a code change turns it on (no flag, no environment).
# Profile changes and profile saves take the shared writer-move lock, so a move waits for them and
# they are refused (409) while it runs; the windows that remain are described above.
MOVE_TO_IOTA_ENABLED = False
_sleep = time.sleep
SYSTEM_KEYS = frozenset({"id", "type", "@context", "createdAt", "modifiedAt"})
DEFAULT_VOCABULARY = "https://uri.etsi.org/ngsi-ld/default-context/"
_CONTEXT_TERMS = ("refDevice", "controlledProperty", "measurementType", "deviceType", "numValue",
                  "textValue", "dateObserved", "timeSource", "name", "DeviceMeasurement")
_AGENT_FIELDS = ("name", "object_id", "type", "entity_name", "entity_type", "expression",
                 "metadata")


# -- data ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    urn: str
    external_id: str
    state: Tuple[str, Optional[str]]


@dataclass
class Item:
    row: Row
    code: Optional[str] = None
    measurements: Optional[list] = None
    action: str = 'move'            # move | repair | unchanged
    reason: str = ''
    old_attrs: Optional[list] = None
    first_attrs: Optional[list] = None      # the mappings the first PUT of a move to iota sent
    problems: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Outcome:
    urn: str
    status: str                     # moved | repaired | unchanged | failed
    text: str


def _target(to: str, shape: Optional[str]) -> Tuple[str, Optional[str]]:
    return ('iota', shape) if to == 'iota' else ('em', None)


def _say(text: str) -> None:
    print(text, flush=True)


def _exclusive_lock(tenant: str):
    """The seam tests replace: the exclusive writer-move lock of the tenant (a context manager)."""
    return writer_move_lock.ExclusiveLock(tenant)


# -- database -----------------------------------------------------------------------------

def list_rows(tenant: str, urn: Optional[str] = None) -> List[Row]:
    sql = ("SELECT device_urn, external_id, dm_writer, dm_shape FROM iot_datalogger_devices "
           "WHERE tenant_id = %s")
    params: Tuple[Any, ...] = (tenant,)
    if urn:
        sql += " AND device_urn = %s"
        params += (urn,)
    sql += " ORDER BY external_id"
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [Row(r['device_urn'], r['external_id'], (r['dm_writer'], r['dm_shape'])) for r in rows]


def tenant_suspended(tenant: str) -> bool:
    """True when the tenant has no row or a `deleted_at`; raises on a read error."""
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT deleted_at FROM tenants WHERE tenant_id = %s", (tenant,))
        row = cur.fetchone()
    return row is None or row['deleted_at'] is not None


# -- mappings -----------------------------------------------------------------------------

def _normalise(attributes: Sequence[dict]) -> List[Dict[str, Any]]:
    """The agent's attribute list as compared: fixed fields, in order, absent == empty."""
    out = []
    for a in attributes or []:
        out.append({k: a[k] for k in _AGENT_FIELDS if a.get(k) not in (None, '', {}, [])})
    return out


def _build(tenant: str, row: Row, state, code: Optional[str], measurements: list) -> list:
    return dataloggers._attributes_for_state(state, tenant, row.urn, row.external_id, code,
                                             measurements)


def _agent_matches(tenant: str, external_id: str, expected: list) -> bool:
    try:
        doc = iot_agent.get_device(dataloggers.iota_url(), dataloggers.iota_headers(tenant),
                                   external_id)
    except Exception as exc:
        logger.warning("agent document of %s not readable: %s", external_id, type(exc).__name__)
        return False
    return _normalise(doc.get('attributes')) == _normalise(expected)


def _channel_conflicts(tenant: str, row: Row, code: str, measurements: list, shape: str) -> List[str]:
    channels = measurement_channels(measurements, tenant=tenant, external_id=row.external_id,
                                    device_urn=row.urn, profile_code=code, shape=shape)
    by_id: Dict[str, list] = {}
    for ch in channels:
        by_id.setdefault(ch.dm_id, []).append(ch)
    out = []
    for dm_id, group in by_id.items():
        sigs = {(c.unit_code, c.kind, c.expression, json.dumps(dict(c.fixed), sort_keys=True))
                for c in group}
        if len(sigs) > 1:
            out.append(f"channels of {dm_id} differ in unit, kind, expression or fixed attributes")
    return out


# -- plan ---------------------------------------------------------------------------------

def build_plan(tenant: str, rows: List[Row], to: str, shape: Optional[str]) -> List[Item]:
    """Validate every device and decide its action; writes nothing. Problems are on the items."""
    target = _target(to, shape)
    entities = dataloggers._orion_devices(tenant, [r.urn for r in rows])
    items = []
    for row in rows:
        item = Item(row)
        items.append(item)
        entity = entities.get(row.urn)
        if not entity:
            item.problems.append("Orion entity not found")
            continue
        item.code = entity_profile_code(tenant, entity)
        if not item.code:
            item.problems.append("no profile on the Orion entity")
            continue
        try:
            item.measurements = dataloggers._profile_measurements(tenant, item.code)
        except Exception as exc:
            item.problems.append(f"profile {item.code} not readable: {type(exc).__name__}")
            continue
        if item.measurements is None:
            item.problems.append(f"profile {item.code} not found")
            continue
        try:
            expected = _build(tenant, row, target, item.code, item.measurements)
            if to == 'iota':
                item.problems.extend(_channel_conflicts(tenant, row, item.code,
                                                        item.measurements, shape))
        except (ValueError, KeyError, dataloggers.ApiError) as exc:
            detail = getattr(exc, 'message', None) or str(exc)
            item.problems.append(f"mapping generation failed for {target}: "
                                 f"{type(exc).__name__}: {detail}")
            continue
        if item.problems:
            continue
        try:
            item.old_attrs = _build(tenant, row, row.state, item.code, item.measurements)
        except Exception:
            item.old_attrs = None
        if row.state == target:
            if _agent_matches(tenant, row.external_id, expected):
                item.action, item.reason = 'unchanged', 'already in the target state, agent mappings match'
            else:
                item.action, item.reason = 'repair', 'target state, agent mappings differ or unreadable'
        else:
            item.action, item.reason = 'move', f"{row.state} -> {target}"
    return items


# -- apply --------------------------------------------------------------------------------

_STOPPING = b"stopping after the current step; please wait\n"
_LOCK_LOST_LEFT = ("lock lost; the new mappings were left in the agent (no gap, duplicates "
                   "possible); rerun")


class _Signals:
    """SIGINT, SIGTERM and SIGHUP (a dropped terminal) during the apply phase: the handler never
    raises. It sets a flag and writes one line with `os.write` (a `print` from a handler that
    interrupted a `print` raises: stdout is not re-entrant). The batch reads the flag at defined points (`stop_requested`). The previous
    handlers are restored on exit."""

    def __init__(self):
        self._stop = False
        self._previous: Dict[int, Any] = {}

    def __enter__(self) -> "_Signals":
        try:
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                if sig == signal.SIGHUP and signal.getsignal(sig) == signal.SIG_IGN:
                    continue            # `nohup`: a hang-up was ignored on purpose
                self._previous[sig] = signal.signal(sig, self._handle)
        except BaseException:
            self._restore()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._restore()
        return False

    def _restore(self) -> None:
        previous, self._previous = self._previous, {}
        for sig, old in previous.items():
            signal.signal(sig, signal.SIG_DFL if old is None else old)

    def _handle(self, signum, frame) -> None:
        self._stop = True
        try:
            os.write(2, _STOPPING)
        except Exception:
            pass

    def stop_requested(self) -> bool:
        return self._stop


class _LockLost(Exception):
    """No exclusive lock right before a PUT: nothing was sent."""


class _LockHolder:
    """The exclusive lock of the run. It may be replaced ONCE (through `_exclusive_lock`).

    `ensure()`: True while the lock is alive, or when the single re-take just succeeded; False
    otherwise and for ever after. `lost` is sticky: once the lock was found dead nothing more is
    committed and no first PUT is sent, even if the re-take worked (the plan is no longer
    protected). `generation` counts the replacements.
    """

    def __init__(self, tenant: str, lock):
        self.tenant = tenant
        self.current = lock
        self.lost = False
        self.generation = 0
        self._retaken = False
        self._dead = False
        self._second = None

    def _alive(self) -> bool:
        try:
            return bool(self.current.alive())
        except Exception as exc:
            logger.error("dm_cutover: lock check failed (%s): tenant=%s", type(exc).__name__,
                         self.tenant)
            return False

    def ensure(self) -> bool:
        if self._dead:
            return False
        if self._alive():
            return True
        self.lost = True
        if self._retaken:
            self._dead = True
            return False
        self._retaken = True
        logger.error("dm_cutover: the exclusive lock was lost; taking it once more: tenant=%s",
                     self.tenant)
        try:
            self.current.__exit__(None, None, None)    # a slow server may still hold the old session
        except Exception as exc:
            logger.warning("dm_cutover: closing the lost lock failed (%s)", type(exc).__name__)
        new = None
        try:
            new = _exclusive_lock(self.tenant)
            self._second = new                # closed by `close()` even when the enter fails
            new.__enter__()
        except Exception as exc:
            logger.critical("dm_cutover: the exclusive lock could not be taken again (%s): tenant=%s",
                            type(exc).__name__, self.tenant)
            self._dead = True
            return False
        self.current = new
        self.generation += 1
        return True

    def can_commit(self) -> bool:
        """A row is committed only while the original lock has never been lost."""
        if self.lost or self._dead:
            return False
        if not self._alive():
            self.lost = True
            return False
        return True

    def close(self) -> None:
        second, self._second = self._second, None
        if second is not None:
            try:
                second.__exit__(None, None, None)
            except Exception as exc:
                logger.warning("dm_cutover: closing the second lock failed (%s)", type(exc).__name__)


@contextmanager
def _holding(tenant: str, lock):
    """The holder of `lock` (a raw lock is wrapped; its owner closes the second lock)."""
    if isinstance(lock, _LockHolder):
        yield lock
        return
    holder = _LockHolder(tenant, lock)
    try:
        yield holder
    finally:
        holder.close()


class _Unreadable(Exception):
    pass


@dataclass(frozen=True)
class _NeedsUndo:
    why: str


def _join(*parts: str) -> str:
    return "; ".join(p for p in parts if p)


def _push_fresh(holder: _LockHolder, tenant: str, row: Row) -> None:
    """Re-read the profile and push what the row's committed state calls for.

    The lock is checked before the profile is read and again right before the PUT. When the
    second check had to re-take the lock, a profile change may have run in the gap: the profile is
    read again (twice at most); then nothing is sent (`_LockLost`)."""
    for _ in range(2):
        if not holder.ensure():
            raise _LockLost()
        generation = holder.generation
        entity = dataloggers._orion_devices(tenant, [row.urn]).get(row.urn)
        code = entity_profile_code(tenant, entity)
        measurements = dataloggers._profile_measurements(tenant, code) if code else None
        if measurements is None:
            raise RuntimeError("profile not readable")
        if not holder.ensure():
            raise _LockLost()
        if holder.generation != generation:
            continue

        def before_put() -> None:
            if not holder.ensure() or holder.generation != generation:
                raise _LockLost()

        dataloggers.push_iota_attributes(tenant, row.urn, row.external_id, code, measurements,
                                         before_put)
        return
    raise _LockLost()


def _frame(exc: BaseException) -> str:
    """File name and line of the last traceback entry: where it failed, never what it said."""
    tb = traceback.extract_tb(exc.__traceback__)
    return f"{os.path.basename(tb[-1].filename)}:{tb[-1].lineno}" if tb else "unknown"


def _say_safe(text: str) -> None:
    """Printing never stops a batch."""
    try:
        _say(text)
    except Exception as exc:
        logger.warning("dm_cutover: output failed (%s)", type(exc).__name__)


_SLICE = 0.5


def _wait(settle: float, signals: "_Signals") -> bool:
    """Sleep `settle` seconds in slices of at most `_SLICE`; False when a stop was requested."""
    slept = 0.0
    while slept < settle:
        if signals.stop_requested():
            return False
        step = min(_SLICE, settle - slept)
        _sleep(step)
        slept += step
    return True


def _put(tenant: str, external_id: str, attrs: list) -> None:
    iot_agent.update_device_attributes(dataloggers.iota_url(), dataloggers.iota_headers(tenant),
                                       external_id, attrs)


_moved_at: List[datetime] = []      # [time of the last `moved` outcome of this run]


def _report_outcome(out: Outcome) -> None:
    if out.status == 'moved':
        _moved_at[:] = [_utcnow()]
    if out.status != 'unchanged':
        _say_safe(f"{out.urn}: {'FAILED' if out.status == 'failed' else out.status} - {out.text}")


def _fail(row: Row, text: str) -> Outcome:
    logger.error("dm_cutover failed: device=%s %s", row.external_id, text)
    return Outcome(row.urn, 'failed', text)


def _critical(row: Row, target, what: str) -> None:
    logger.critical("INCONSISTENCY: %s: device=%s urn=%s previous=%r target=%r; check the row "
                    "and rerun", what, row.external_id, row.urn, row.state, target)


def _previous_mappings(tenant: str, item: Item) -> Tuple[Optional[list], bool]:
    """(mappings of the state the plan read, True when they are the plan-time ones).

    Rebuilt from a fresh profile read; the plan-time ones are the fallback."""
    row = item.row
    try:
        entity = dataloggers._orion_devices(tenant, [row.urn]).get(row.urn)
        code = entity_profile_code(tenant, entity)
        measurements = dataloggers._profile_measurements(tenant, code) if code else None
        if measurements is None:
            raise RuntimeError("profile not readable")
        return _build(tenant, row, row.state, code, measurements), False
    except Exception as exc:
        logger.warning("dm_cutover: fresh profile read for the restore failed (%s); using the "
                       "plan-time mappings: device=%s", type(exc).__name__, row.external_id)
        return item.old_attrs, True


def _read_row(holder: _LockHolder, urn: str) -> Optional[tuple]:
    """The state of the row through the lock; None when there is no row. One retry."""
    for attempt in (0, 1):
        try:
            return holder.current.read_state(urn)
        except Exception as exc:
            if attempt == 0 and holder.ensure():
                continue
            raise _Unreadable(type(exc).__name__) from None
    raise _Unreadable("unreadable")


_IOTA_STAYS = "the agent may not hold the new mappings; "


def _after_commit(holder: _LockHolder, tenant: str, row: Row, target, stays: str,
                  rerun: str = "rerun") -> Outcome:
    """The row is committed: send the final PUT. `stays` says what the agent holds if it fails."""
    try:
        _push_fresh(holder, tenant, row)
    except _LockLost:
        return _fail(row, f"row updated to {target}; final PUT not sent (lock lost); {stays}{rerun}")
    except Exception as exc:
        return _fail(row, f"row already updated to {target} but the final PUT failed "
                          f"({type(exc).__name__}); {stays}{rerun}")
    return Outcome(row.urn, 'moved', f"{row.state} -> {target}")


def _undo(holder: _LockHolder, tenant: str, item: Item, target, why: str = '') -> Outcome:
    """Settle a device of a move to iota whose first PUT went out and that has no outcome.

    Needs the exclusive lock (one re-take for the whole run); without it nothing is put back. The
    row, read through the lock, decides: the state the plan read, the mappings of THAT state are
    put back (never Device-only for an `iota` shape); the target, the commit went through and the
    final PUT is sent; anything else, no row or unreadable, the new mappings stay (duplicates, no
    gap). Never raises.
    """
    row = item.row
    try:
        return _undo_with_lock(holder, tenant, item, target, why)
    except Exception as exc:
        _critical(row, target, f"undo failed ({type(exc).__name__})")
        return _fail(row, _join(why, f"undo failed ({type(exc).__name__}); the new mappings may "
                                     "be in the agent; rerun"))


def _undo_with_lock(holder: _LockHolder, tenant: str, item: Item, target, why: str) -> Outcome:
    row = item.row

    def left(text: str) -> Outcome:
        _critical(row, target, text)
        return _fail(row, _join(why, text))

    for _ in range(2):          # a re-take between the read and the PUT means reading again
        if not holder.ensure():
            return left(_LOCK_LOST_LEFT)
        generation = holder.generation
        try:
            state = _read_row(holder, row.urn)
        except _Unreadable as exc:
            return left(f"the row could not be read ({exc}); the new mappings were left in the "
                        "agent (duplicates, no gap); rerun")
        if state is None:
            return left("the row does not exist; the new mappings were left in the agent "
                        "(duplicates, no gap); rerun")
        if state not in (row.state, target):
            return left(f"the row is now {state!r}, not the state the plan read; the new "
                        "mappings were left in the agent (duplicates, no gap); check the row, "
                        "then rerun")
        if state == target:
            if not holder.ensure():
                return left(_LOCK_LOST_LEFT)
            if holder.generation != generation:
                continue
            return _after_commit(holder, tenant, row, target, _IOTA_STAYS, "rerun now")
        attrs, from_plan = _previous_mappings(tenant, item)
        if attrs is None:
            return left("previous mappings not buildable; the new mappings were left in the "
                        "agent; rerun")
        if not holder.ensure():
            return left(_LOCK_LOST_LEFT)
        if holder.generation != generation:
            continue
        try:
            _put(tenant, row.external_id, attrs)
        except Exception as exc:
            _critical(row, target, f"the previous mappings were not restored "
                                   f"({type(exc).__name__})")
            return _fail(row, _join(why, "the previous mappings could NOT be restored; the agent "
                                         "holds the new ones; rerun"))
        text = ("previous mappings restored from the plan (the profile could not be read again)"
                if from_plan else "previous mappings restored")
        return _fail(row, _join(why, text))
    return left(_LOCK_LOST_LEFT)


def _first_put(holder: _LockHolder, tenant: str, item: Item, target):
    """First PUT of a move to iota. None: sent. An Outcome: nothing was sent (no lock).
    `_NeedsUndo`: the PUT raised and may still have been applied (a timeout)."""
    row = item.row
    try:
        attrs = _build(tenant, row, target, item.code, item.measurements)
        item.first_attrs = attrs
        if not holder.ensure() or holder.lost:
            return _fail(row, "lock lost; the first PUT was not sent; nothing was changed; rerun")
        _put(tenant, row.external_id, attrs)
        return None
    except Exception as exc:
        return _NeedsUndo(f"first PUT failed ({type(exc).__name__}); state unknown, rerun")


def _row_changed(row: Row, text: str) -> Outcome:
    logger.error("dm_cutover: the row changed since the plan, nothing was committed: device=%s "
                 "urn=%s planned=%r", row.external_id, row.urn, row.state)
    return _fail(row, f"the row changed since the plan; nothing was committed; {text}")


def _commit_and_finish(holder: _LockHolder, tenant: str, item: Item, target):
    """Check the agent, commit the row on the lock connection, then the final PUT. An Outcome, or
    `_NeedsUndo`.

    The agent's own document must still be the one the first PUT sent: during the wait a profile
    change that lost its lock, or a device deleted and registered again under the same external id,
    may have replaced it with Device-only mappings, and committing the row over those would leave a
    device nobody writes. Different or unreadable: nothing is committed and the device is undone."""
    row = item.row
    if item.first_attrs is None or not _agent_matches(tenant, row.external_id, item.first_attrs):
        return _NeedsUndo("the agent no longer holds the first mappings")
    try:
        holder.current.commit_state(row.urn, row.state, target)
    except writer_move_lock.RowChanged:
        logger.error("dm_cutover: the row changed since the plan, nothing was committed: "
                     "device=%s urn=%s planned=%r", row.external_id, row.urn, row.state)
        return _NeedsUndo("the row changed since the plan; nothing was committed")
    except Exception as exc:
        return _NeedsUndo(f"row update failed ({type(exc).__name__})")
    return _after_commit(holder, tenant, row, target, _IOTA_STAYS, "rerun now")


def apply_iota(lock, tenant: str, items: List[Item], shape: str, settle: float) -> List[Outcome]:
    """Move a batch to iota: first PUT of every device, ONE wait, then commit + final PUT each.

    `lock` is the held exclusive lock: every row is committed on its connection. One outcome per
    device, printed when it is known. Nothing escapes: a stop request, a lost lock or any
    unexpected error ends the batch, every device with a first PUT and no outcome goes through
    `_undo` and the devices never touched are listed "not started".
    """
    if (isinstance(settle, bool) or not isinstance(settle, (int, float))
            or not math.isfinite(settle) or not MIN_SETTLE <= settle <= MAX_SETTLE):
        raise ValueError(f"settle must be between {MIN_SETTLE:g} and {MAX_SETTLE:g} seconds")
    with _Signals() as signals, _holding(tenant, lock) as holder:
        return _apply_iota(holder, tenant, items, ('iota', shape), settle, signals)


def _suspension_stop(tenant: str) -> str:
    """'' while the tenant may still be committed; else the reason the batch stops. The check of the
    start is not enough: the wait lasts minutes, and a suspended tenant is not moved (a tenant that
    cannot be read counts as suspended: nothing is committed on a guess)."""
    try:
        if not tenant_suspended(tenant):
            return ''
    except Exception as exc:
        logger.error("dm_cutover: tenant state unreadable before the commits (%s): tenant=%s",
                     type(exc).__name__, tenant)
        return "tenant is suspended (or its state could not be read)"
    return "tenant is suspended"


def _stop_reason(signals: "_Signals", failure: Optional[Exception]) -> str:
    if failure is not None:
        return f"stopped by an unexpected {type(failure).__name__}"
    if signals.stop_requested():
        return "interrupted"
    return "the batch was stopped (lock lost)"


def _apply_iota(holder: _LockHolder, tenant: str, items: List[Item], target, settle: float,
                signals: _Signals) -> List[Outcome]:
    outcomes: Dict[str, Outcome] = {}
    touched: List[Item] = []        # added BEFORE the first PUT
    inflight_repair: Optional[Item] = None
    failure: Optional[Exception] = None
    stopped_for = ''

    def record(out: Outcome) -> None:
        outcomes[out.urn] = out
        _report_outcome(out)

    def halted() -> bool:
        return signals.stop_requested() or holder.lost

    try:
        for it in items:
            if it.action == 'unchanged':
                record(apply_item(holder, tenant, it, 'iota', target[1]))
                continue
            if halted():
                break
            if it.action == 'repair':
                inflight_repair = it
                record(apply_item(holder, tenant, it, 'iota', target[1]))
                inflight_repair = None
                continue
            touched.append(it)
            res = _first_put(holder, tenant, it, target)
            if isinstance(res, _NeedsUndo):
                record(_undo(holder, tenant, it, target, res.why))
            elif res is not None:
                record(res)
        pending = [it for it in touched if it.row.urn not in outcomes]
        if pending and not halted():
            _say_safe(f"waiting {settle:g} s for in-flight notifications before committing the rows")
            _wait(settle, signals)
            if not halted():
                stopped_for = _suspension_stop(tenant)
        for it in pending:
            if stopped_for or halted() or not holder.can_commit():
                break
            res = _commit_and_finish(holder, tenant, it, target)
            if isinstance(res, _NeedsUndo):
                record(_undo(holder, tenant, it, target, res.why))
            else:
                record(res)
    except Exception as exc:
        failure = exc
        logger.critical("dm_cutover: unexpected %s at %s in the batch; undoing what is not "
                        "settled: tenant=%s", type(exc).__name__, _frame(exc), tenant)
    why = stopped_for or _stop_reason(signals, failure)
    for it in [it for it in touched if it.row.urn not in outcomes]:
        record(_undo(holder, tenant, it, target, why))
    for it in items:
        if it.row.urn not in outcomes:
            record(_fail(it.row, "interrupted during the repair; rerun" if it is inflight_repair
                         else f"not started ({why})"))
    return [outcomes[it.row.urn] for it in items]


def _move_to_em(holder: _LockHolder, tenant: str, item: Item, target) -> Outcome:
    row = item.row
    if not holder.can_commit():
        return _fail(row, "lock lost; nothing was committed; the agent was not touched; rerun")
    try:
        holder.current.commit_state(row.urn, row.state, target)
    except writer_move_lock.RowChanged:
        return _row_changed(row, "the agent was not touched")
    except Exception as exc:
        return _fail(row, f"row update failed ({type(exc).__name__}); the agent was not touched; "
                          f"if the commit went through the agent still has the DM mappings "
                          f"(duplicates, no gap); rerun")
    try:
        _push_fresh(holder, tenant, row)
    except _LockLost:
        return _fail(row, f"row updated to {target}; the PUT was not sent (lock lost); the agent "
                          f"still has the DM mappings (duplicate writes, no gap); rerun")
    except Exception as exc:
        return _fail(row, f"row already updated to {target} but the PUT failed "
                          f"({type(exc).__name__}); the agent still has the DM mappings "
                          f"(duplicate writes, no gap); rerun")
    return Outcome(row.urn, 'moved', f"{row.state} -> {target}")


def apply_item(lock, tenant: str, item: Item, to: str, shape: Optional[str]) -> Outcome:
    """One device. A move to iota is refused here: its only path is `apply_iota`, with the wait."""
    if to == 'iota' and item.action == 'move':
        raise AssertionError("a move to iota goes through apply_iota")
    row = item.row
    target = _target(to, shape)
    if item.action == 'unchanged':
        return Outcome(row.urn, 'unchanged', item.reason)
    with _holding(tenant, lock) as holder:
        if item.action == 'repair':
            try:
                _push_fresh(holder, tenant, row)
            except _LockLost:
                return _fail(row, "PUT not sent (lock lost); rerun")
            except Exception as exc:
                return _fail(row, f"PUT failed ({type(exc).__name__}); rerun")
            return Outcome(row.urn, 'repaired', item.reason)
        return _move_to_em(holder, tenant, item, target)


def apply_em(lock, tenant: str, items: List[Item]) -> List[Outcome]:
    """Move a batch to em: per device the row first, then the PUT. There is nothing to undo
    (duplicates only). A stop request is looked at between devices; a failure reads the row of the
    device in flight (`em`: the PUT is sent; otherwise the agent was not touched) and lists the
    rest as not started."""
    with _Signals() as signals, _holding(tenant, lock) as holder:
        outcomes: Dict[str, Outcome] = {}
        inflight: Optional[Item] = None
        failure: Optional[Exception] = None

        def record(out: Outcome) -> None:
            outcomes[out.urn] = out
            _report_outcome(out)

        try:
            for it in items:
                if signals.stop_requested() or holder.lost:
                    break
                inflight = it
                record(apply_item(holder, tenant, it, 'em', None))
                inflight = None
        except Exception as exc:
            failure = exc
            logger.critical("dm_cutover: unexpected %s at %s in the batch: tenant=%s",
                            type(exc).__name__, _frame(exc), tenant)
        why = _stop_reason(signals, failure)
        if inflight is not None and inflight.row.urn not in outcomes:
            record(_settle_em(holder, tenant, inflight, why))
        for it in items:
            if it.row.urn not in outcomes:
                record(_fail(it.row, f"not started ({why})"))
        return [outcomes[it.row.urn] for it in items]


def _settle_em(holder: _LockHolder, tenant: str, item: Item, why: str) -> Outcome:
    """The device in flight when a move to em stopped: the row says how far it got. Never raises."""
    row = item.row
    try:
        if item.action != 'move':
            return _fail(row, _join(why, "during the repair; rerun"))
        if not holder.ensure():
            return _fail(row, _join(why, "lock lost; the row may be updated and the agent still "
                                         "has the DM mappings (duplicates, no gap); rerun"))
        state = _read_row(holder, row.urn)
        if state == ('em', None):
            return _after_commit(holder, tenant, row, state, "the agent still has the DM mappings "
                                                              "(duplicate writes, no gap); ")
        return _fail(row, _join(why, "the agent was not touched"))
    except Exception as exc:
        return _fail(row, _join(why, f"the row could not be read ({type(exc).__name__}); the agent "
                                     "may still have the DM mappings (duplicates, no gap); rerun"))


# -- verify -------------------------------------------------------------------------------

def _expand(value: Any, defs: Dict[str, Any]) -> Optional[str]:
    if isinstance(value, dict):
        value = value.get('@id')
    if not isinstance(value, str):
        return None
    if '://' in value:
        return value
    prefix, sep, rest = value.partition(':')
    base = defs.get(prefix)
    if isinstance(base, dict):
        base = base.get('@id')
    if sep and rest and isinstance(base, str) and '://' in base:
        return base + rest
    return None


def load_terms() -> Dict[str, str]:
    """Term -> IRI from the platform context document; raises RuntimeError (fail closed)."""
    url = os.getenv('CONTEXT_URL', '')
    if not url:
        raise RuntimeError("CONTEXT_URL is not set")
    try:
        resp = requests.get(url, headers={'Accept': 'application/ld+json, application/json'},
                            timeout=10)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        doc = resp.json()
    except RuntimeError as exc:
        raise RuntimeError(f"context document not readable: {exc}") from None
    except Exception as exc:
        raise RuntimeError(f"context document not readable: {type(exc).__name__}") from None
    ctx = doc.get('@context') if isinstance(doc, dict) else None
    entries = ctx if isinstance(ctx, list) else [ctx]
    defs: Dict[str, Any] = {}
    for entry in entries:
        if isinstance(entry, dict):
            defs.update(entry)
    terms, missing = {}, []
    for term in _CONTEXT_TERMS:
        iri = _expand(defs.get(term), defs)
        if iri:
            terms[term] = iri
        else:
            missing.append(term)
    if missing:
        raise RuntimeError(f"context document lacks terms: {', '.join(missing)}")
    return terms


def read_dms(tenant: str, type_iri: str) -> List[dict]:
    """Every DeviceMeasurement of the tenant, read without Link; raises RuntimeError."""
    headers = {'Accept': 'application/json'}
    headers = inject_fiware_headers(headers, tenant=tenant, has_context_in_body=False)
    for k in ('Link', 'Content-Type'):
        headers.pop(k, None)
    out: List[dict] = []
    count: Optional[int] = None
    offset = 0
    while True:
        resp = requests.get(f"{dataloggers.ORION_URL}/ngsi-ld/v1/entities", headers=headers,
                            params={'type': type_iri, 'count': 'true', 'limit': PAGE,
                                    'offset': offset, 'options': 'sysAttrs'}, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"broker answered HTTP {resp.status_code}")
        raw = resp.headers.get('NGSILD-Results-Count')
        try:
            count = int(raw)
        except (TypeError, ValueError):
            raise RuntimeError("broker did not return a results count") from None
        page = resp.json()
        if not isinstance(page, list):
            raise RuntimeError("broker answer is not a list")
        out.extend(page)
        offset += len(page)
        if not page or offset >= count:
            break
    if len(out) != count:
        raise RuntimeError(f"read {len(out)} entities but the broker counts {count}")
    return out


def _expected_keys(channel, shape: str, terms: Dict[str, str]):
    """(required, allowed, either) key IRIs of the DM of a channel."""
    required = {terms[t] for t in ('refDevice', 'dateObserved', 'timeSource')}
    for name, value in channel.fixed.items():
        if value is not None:
            required.add(terms[name])
    num, text = terms['numValue'], terms['textValue']
    either = None
    if channel.kind == 'number':
        required.add(num)
    elif channel.kind == 'text':
        required.add(text)
    else:
        either = {num, text}
    allowed = set(required) | (either or set())
    return required, allowed, either


def _orphan_keys(shape: str, terms: Dict[str, str]):
    """(required, allowed) key IRIs of a DM no channel of the profile describes any more."""
    required = {terms[t] for t in ('refDevice', 'dateObserved', 'timeSource', 'controlledProperty')}
    allowed = required | {terms[t] for t in ('measurementType', 'deviceType', 'numValue', 'textValue')}
    if shape == 'sdm':
        allowed.add(terms['name'])
    return required, allowed


def _is_default_vocabulary(key: str) -> bool:
    return key.startswith(DEFAULT_VOCABULARY) or ':' not in key


def _is_time_instant(key: str) -> bool:
    return key == 'TimeInstant' or key.endswith('/TimeInstant')


LIST_CAP = 50                  # lines printed per heading; the counts are always complete
FUTURE_MARGIN = 5.0            # seconds a --since may be ahead of this machine's clock


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Any) -> Optional[datetime]:
    """An ISO 8601 string with `Z` or an offset as an aware datetime; None otherwise."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _parse_since(value: str) -> Optional[datetime]:
    """`--since`: aware, and not later than now + FUTURE_MARGIN; None when it is not."""
    parsed = _aware(value)
    if parsed is None or parsed > _utcnow() + timedelta(seconds=FUTURE_MARGIN):
        return None
    return parsed


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _round_up_second(moment: datetime) -> datetime:
    moment = moment.astimezone(timezone.utc)
    whole = moment.replace(microsecond=0)
    return whole + timedelta(seconds=1) if moment.microsecond else whole


def _verify_hint(tenant: str, device: Optional[str], moment: datetime) -> str:
    where = f"--tenant {tenant}" + (f" --device {device}" if device else "")
    return (f"verify in a few minutes with: {where} --verify "
            f"--since {_utc_text(_round_up_second(moment))}")


def _say_listing(heading: str, lines: Sequence[str]) -> None:
    """The heading, at most LIST_CAP lines and the rest as a count."""
    _say(heading)
    for line in lines[:LIST_CAP]:
        _say(line)
    if len(lines) > LIST_CAP:
        _say(f"  \u2026 and {len(lines) - LIST_CAP} more")


def _plain(attr: Any) -> str:
    value = attr.get('value') if isinstance(attr, dict) else attr
    return "-" if value is None else str(value)[:40]


def _describe(e: dict, terms: Dict[str, str]) -> str:
    """The three times of an entity: dateObserved, the value's observedAt, the entity's modifiedAt."""
    observed = []
    for term in ('numValue', 'textValue'):
        attr = e.get(terms[term])
        if isinstance(attr, dict) and attr.get('observedAt'):
            observed.append(_plain(attr.get('observedAt')))
    return (f"dateObserved={_plain(e.get(terms['dateObserved']))} "
            f"observedAt={'/'.join(observed) or '-'} modifiedAt={_plain(e.get('modifiedAt'))}")


class _Profiles:
    """What the current profile says about each device, read once per device."""

    def __init__(self, tenant: str):
        self.tenant = tenant
        self._data: Dict[str, Optional[tuple]] = {}
        self._channels: Dict[str, Optional[Dict[str, Any]]] = {}

    def data(self, row: Row):
        if row.urn not in self._data:
            result = None
            try:
                ent = dataloggers._orion_devices(self.tenant, [row.urn]).get(row.urn)
                code = entity_profile_code(self.tenant, ent)
                ms = dataloggers._profile_measurements(self.tenant, code) if code else None
                if ms is not None:
                    result = (code, ms)
            except Exception as exc:
                logger.warning("verify: profile of %s not readable: %s", row.external_id,
                               type(exc).__name__)
            self._data[row.urn] = result
        return self._data[row.urn]

    def channels(self, row: Row):
        if row.urn in self._channels:
            return self._channels[row.urn]
        result = None
        try:
            data = self.data(row)
            if data is not None:
                code, ms = data
                chans = measurement_channels(ms, tenant=self.tenant, external_id=row.external_id,
                                             device_urn=row.urn, profile_code=code,
                                             shape=row.state[1])
                result = {c.dm_id: c for c in chans}   # canonical channel last: it wins
        except Exception as exc:
            logger.warning("verify: profile of %s not readable: %s", row.external_id,
                           type(exc).__name__)
        self._channels[row.urn] = result
        return result


@dataclass
class _Verdict:
    """What check (b)/(d) found in one entity."""
    state: str                                  # unreadable | not_reported | reported
    orphan: bool = False
    stale: List[str] = field(default_factory=list)      # value terms left out
    issues: List[str] = field(default_factory=list)     # check (b)
    no_observed_at: List[str] = field(default_factory=list)   # check (d), value terms


def _value_times(e: dict, terms: Dict[str, str]):
    """(times, unreadable): the own `modifiedAt` of each value attribute present, by term, and the
    terms whose time is missing or unreadable."""
    times: Dict[str, datetime] = {}
    unreadable: List[str] = []
    for term in ('numValue', 'textValue'):
        attr = e.get(terms[term])
        if attr is None:
            continue
        stamp = _aware(attr.get('modifiedAt')) if isinstance(attr, dict) else None
        if stamp is None:
            unreadable.append(term)
        else:
            times[term] = stamp
    return times, unreadable


def _key_issues(keys: set, channel, shape: Optional[str], terms: Dict[str, str]) -> List[str]:
    """The attribute-set problems of an entity (check (b))."""
    issues = []
    offending = sorted(k for k in keys if _is_default_vocabulary(k))
    if offending:
        issues.append(f"default vocabulary keys {offending}")
    instants = sorted(k for k in keys if _is_time_instant(k))
    if instants:
        issues.append(f"TimeInstant attribute {instants}")
    if channel is None:
        required, allowed = _orphan_keys(shape, terms)
        either = {terms['numValue'], terms['textValue']}
    else:
        required, allowed, either = _expected_keys(channel, shape, terms)
    extra = sorted(k for k in keys - allowed if k not in offending and k not in instants)
    missing = sorted(required - keys)
    if either and not (either & keys):
        missing.append(" or ".join(sorted(either)))
    if extra:
        issues.append(f"unexpected keys {extra}")
    if missing:
        issues.append(f"missing keys {missing}")
    return issues


def _judge_entity(e: dict, row: Row, chans: Optional[Dict[str, Any]], terms: Dict[str, str],
                  since: datetime) -> _Verdict:
    """Reported = a value attribute with its OWN `modifiedAt` >= since; the entity's time never
    counts. An unreadable time (entity or value attribute) fails; an entity with no new value is
    listed only; a reported one is judged with its new values, every old one left out."""
    modified = _aware(e.get('modifiedAt'))
    times, unreadable = _value_times(e, terms)
    if modified is None or unreadable:
        what = ["modifiedAt missing or unreadable"] if modified is None else []
        what += [f"{t} modifiedAt missing or unreadable" for t in unreadable]
        return _Verdict('unreadable', issues=what)
    channel = chans.get(e.get('id')) if chans else None
    orphan = chans is not None and channel is None
    new = [t for t, stamp in times.items() if stamp >= since]
    if not new:
        return _Verdict('not_reported' if modified < since else 'not_updated', orphan=orphan)
    verdict = _Verdict('reported', orphan=orphan)
    verdict.stale = [t for t in times if t not in new]
    stale_iris = {terms[t] for t in verdict.stale}
    keys = {k for k in e if k not in SYSTEM_KEYS} - stale_iris
    if chans is None:
        verdict.issues.append("expected attributes unknown (profile not readable)")
    else:
        verdict.issues.extend(_key_issues(keys, channel, row.state[1], terms))
    for term in new:
        val = e.get(terms[term])
        if not (isinstance(val, dict) and val.get('observedAt')):
            verdict.no_observed_at.append(term)
    return verdict


def _check_agent(tenant: str, rows: List[Row], selected: set, profiles: _Profiles) -> List[str]:
    """Check (c): the agent's own document of every selected device against the expected mappings."""
    bad: List[str] = []
    for row in rows:
        if row.urn not in selected:
            continue
        data = profiles.data(row)
        if data is None:
            bad.append(f"  {row.urn}: expected mappings unknown (profile not readable)")
            continue
        try:
            expected = _build(tenant, row, row.state, data[0], data[1])
        except Exception as exc:
            bad.append(f"  {row.urn}: expected mappings not buildable ({type(exc).__name__})")
            continue
        if not _agent_matches(tenant, row.external_id, expected):
            bad.append(f"  {row.urn}: the IoT Agent's mappings differ from the expected ones "
                       "(or cannot be read)")
    return bad


@dataclass
class _Findings:
    """What the judgement of the entities of the `iota` devices collected."""
    unmatched: int = 0
    judged: int = 0
    not_reported: List[str] = field(default_factory=list)
    not_updated: List[str] = field(default_factory=list)
    orphans: List[str] = field(default_factory=list)
    stale: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)       # check (b)
    bad_d: List[str] = field(default_factory=list)          # check (d)
    seen: set = field(default_factory=set)                  # devices with an entity
    entity_ids: set = field(default_factory=set)            # ids of the entities of selected devices
    no_entity: List[str] = field(default_factory=list)      # channels of the profile with no entity
    reported: Dict[str, int] = field(default_factory=dict)  # device -> reported entities
    unreadable: set = field(default_factory=set)            # devices with an unreadable modifiedAt


def _judge_entities(dms: List[dict], rows: List[Row], selected: set, terms: Dict[str, str],
                    since: datetime, profiles: _Profiles, tenant: str) -> _Findings:
    prefix_of = {f"urn:ngsi-ld:DeviceMeasurement:{tenant}:{r.external_id}:": r for r in rows}
    prefixes = sorted(prefix_of, key=len, reverse=True)       # longest prefix wins
    found = _Findings()
    for e in dms:
        dm_id = str(e.get('id', ''))
        row = next((prefix_of[pre] for pre in prefixes if dm_id.startswith(pre)), None)
        if row is None:
            found.unmatched += 1
            continue
        found.seen.add(row.urn)
        if row.urn not in selected:
            continue
        found.judged += 1
        found.entity_ids.add(dm_id)
        v = _judge_entity(e, row, profiles.channels(row), terms, since)
        desc = _describe(e, terms)
        if v.state == 'unreadable':
            found.unreadable.add(row.urn)
        elif v.state == 'reported':
            found.reported[row.urn] = found.reported.get(row.urn, 0) + 1
        if v.orphan:
            label = {'reported': 'reported', 'not_updated': 'value not updated'}.get(
                v.state, 'not reported')
            found.orphans.append(f"  {dm_id}: {label}; {desc}")
        elif v.state == 'not_reported':
            found.not_reported.append(f"  {dm_id}: {desc}")
        elif v.state == 'not_updated':
            found.not_updated.append(f"  {dm_id}: {desc}")
        for term in v.stale:
            found.stale.append(f"  {dm_id}: stale value {term}; {desc}")
        if v.issues:
            found.problems.append(f"  {dm_id}: " + "; ".join(v.issues))
        found.bad_d.extend(f"  {dm_id}: {term} has no observedAt" for term in v.no_observed_at)
    for row in rows:
        if row.urn not in selected:
            continue
        for dm_id in sorted(profiles.channels(row) or {}):
            if dm_id not in found.entity_ids:
                found.no_entity.append(f"  {dm_id}: no entity yet")
        if row.urn not in found.seen:
            found.problems.append(f"  {row.urn}: no DeviceMeasurement in the broker (readings lost?)")
        elif not found.reported.get(row.urn) and row.urn not in found.unreadable:
            found.problems.append(f"  {row.urn}: delivered NOTHING since {_utc_text(since)}")
    return found


def _finish(failed: List[str]) -> bool:
    """The last line of a verification."""
    _say("verify: OK" if not failed else f"verify: FAILED ({', '.join(failed)})")
    return not failed


def verify(tenant: str, rows: List[Row], only_urn: Optional[str], since: datetime) -> bool:
    """Run the checks (a)-(d); prints the result of each; True when all pass.

    An entity is "reported" when one of its value attributes (`numValue`/`textValue`) has its OWN
    `modifiedAt` at `since` or later; the entity's own `modifiedAt` alone never counts. (a) judges
    every entity;
    (b) and (d) only the reported entities of the selected `iota` devices; (c) the agent's document
    of those devices. A `--device` that is not on `iota` is a failure, but (a) still runs.
    """
    not_iota = False
    if only_urn:
        chosen = next((r for r in rows if r.urn == only_urn), None)
        not_iota = chosen is None or chosen.state[0] != 'iota'
        if not_iota:
            _say(f"{only_urn} is not on iota")
    try:
        terms = load_terms()
        dms = read_dms(tenant, terms['DeviceMeasurement'])
    except RuntimeError as exc:
        _say(f"verify: FAILED ({exc})")
        return False
    _say(f"verify: read {len(dms)} DeviceMeasurement entities")

    failed: List[str] = []
    bad_a = [e.get('id') for e in dms if terms['numValue'] not in e and terms['textValue'] not in e]
    if bad_a:
        failed.append(f"check (a): {len(bad_a)}")
        _say_listing(f"check (a): FAILED ({len(bad_a)} without numValue and textValue)",
                     [f"  {i}" for i in bad_a])
    else:
        _say("check (a): ok")
    if not_iota:
        return _finish(failed + ["device not on iota"])

    selected = {r.urn for r in rows if r.state[0] == 'iota'
                and (only_urn is None or r.urn == only_urn)}
    profiles = _Profiles(tenant)
    found = _judge_entities(dms, rows, selected, terms, since, profiles, tenant)
    if found.unmatched:
        _say(f"verify: {found.unmatched} entities matching no linked device (not judged by check (b))")
    if found.not_reported:
        _say_listing(f"verify: {len(found.not_reported)} entities not reported since "
                     f"{_utc_text(since)} (listed, not judged)", found.not_reported)
    if found.not_updated:
        _say_listing(f"verify: {len(found.not_updated)} entities with a new modifiedAt but a value "
                     f"not updated since {_utc_text(since)} (listed, not judged)", found.not_updated)
    if found.no_entity:
        _say_listing(f"verify: {len(found.no_entity)} channel(s) of iota devices with no entity in "
                     "the broker yet (listed, not a failure by themselves)", found.no_entity)
    if found.orphans:
        _say_listing(f"verify: {len(found.orphans)} orphan entities with no channel in the current "
                     "profile (judged, for the keys any channel may carry, only when reported)",
                     found.orphans)
    if found.stale:
        _say_listing(f"verify: {len(found.stale)} stale value(s) older than {_utc_text(since)} "
                     "(left out of checks (b) and (d))", found.stale)
    if not selected:
        for name in ("(b)", "(c)", "(d)"):
            _say(f"check {name}: NOT RUN (no iota devices)")
        if failed:
            return _finish(failed)
        _say("verify: OK (no iota devices: only check (a) ran)")
        return True
    if found.problems:
        failed.append(f"check (b): {len(found.problems)}")
        _say_listing(f"check (b): FAILED ({len(found.problems)} problem(s), "
                     f"{found.judged} entities judged)", found.problems)
    else:
        _say(f"check (b): ok ({found.judged} entities of iota devices judged)")
    bad_c = _check_agent(tenant, rows, selected, profiles)
    if bad_c:
        failed.append(f"check (c): {len(bad_c)}")
        _say_listing(f"check (c): FAILED ({len(bad_c)} device(s))", bad_c)
    else:
        _say("check (c): ok (agent mappings match)")
    if found.bad_d:
        failed.append(f"check (d): {len(found.bad_d)}")
        _say_listing(f"check (d): FAILED ({len(found.bad_d)} value(s))", found.bad_d)
    else:
        _say("check (d): ok (observedAt on every reported value)")
    return _finish(failed)


# -- command ------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m dm_writer_cutover",
                                description=__doc__.split("\n\n")[0])
    p.add_argument("--tenant", required=True)
    p.add_argument("--device", help="device URN (default: every linked device of the tenant)")
    p.add_argument("--to", choices=("iota", "em"))
    p.add_argument("--shape", choices=SHAPES)
    p.add_argument("--settle", type=float, metavar="SECONDS",
                   help=f"--to iota: wait between the first PUTs and the row commits "
                        f"({MIN_SETTLE:g}-{MAX_SETTLE:g}, default {DEFAULT_SETTLE:g})")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate and print the plan; write nothing")
    mode.add_argument("--apply", action="store_true")
    p.add_argument("--verify", action="store_true",
                   help="read-only check of the broker's DM entities; alone, with --since")
    p.add_argument("--since", metavar="TIME",
                   help="--verify: ISO 8601 time with Z or an offset; an entity is reported when one of "
                        "its value attributes (numValue/textValue) has its own modifiedAt this or "
                        "later (use the time --apply printed)")
    return p


def _args(argv: Optional[List[str]]) -> argparse.Namespace:
    p = _parser()
    a = p.parse_args(argv)
    try:
        canonical = normalize_tenant_id(a.tenant)
    except ValueError:
        canonical = None
    if canonical != a.tenant:
        p.error("--tenant must be in canonical form (lowercase letters, digits and single hyphens)")
    if a.verify:
        if a.dry_run or a.apply:
            p.error("--verify runs alone, after the move (the --apply output says when and with what)")
        for flag, given in (("--to", a.to), ("--shape", a.shape), ("--settle", a.settle)):
            if given is not None:
                p.error(f"{flag} is refused with --verify")
        if not a.since:
            p.error("--verify needs --since TIME (ISO 8601 with Z or an offset)")
        a.since_at = _parse_since(a.since)
        if a.since_at is None:
            p.error("--since must be an ISO 8601 time with Z or an offset, not later than now")
    elif a.since is not None:
        p.error("--since only goes with --verify")
    if a.dry_run or a.apply:
        if not a.to:
            p.error("--to is required with --dry-run/--apply")
        if a.to == 'iota' and not a.shape:
            p.error("--shape is required with --to iota")
        if a.apply and a.to == 'iota' and not MOVE_TO_IOTA_ENABLED:
            p.error("moving devices to the IoT Agent is disabled in this build "
                    "(--dry-run, --verify and --to em still work)")
        if a.to == 'em' and a.shape:
            p.error("--shape is refused with --to em (the entity-manager has no shape)")
        if a.to == 'em' and a.settle is not None:
            p.error("--settle only applies to --to iota")
        if a.settle is not None and not (math.isfinite(a.settle)
                                         and MIN_SETTLE <= a.settle <= MAX_SETTLE):
            p.error(f"--settle must be between {MIN_SETTLE:g} and {MAX_SETTLE:g} seconds")
    elif not a.verify:
        p.error("one of --dry-run, --apply or --verify is required")
    return a


def _report_plan(items: List[Item], dry: bool) -> None:
    for it in items:
        if it.action == 'unchanged':
            _say(f"{it.row.urn}: unchanged ({it.reason})")
        else:
            verb = {'move': 'would move' if dry else 'move', 'repair': 'would repush' if dry else 'repush'}[it.action]
            _say(f"{it.row.urn}: {verb} ({it.reason})")


def _plan_and_apply(args: argparse.Namespace, tenant: str, lock,
                    progress: Optional[list] = None) -> Tuple[Optional[int], bool]:
    """Everything up to and including the writes. (exit code, ok): a code ends the command; None
    carries on to the verification. `lock` is the held exclusive lock with --apply, else None.
    `progress` gets an item when the apply phase starts (before that, nothing was written)."""
    if args.apply and args.to == 'iota':
        try:
            suspended = tenant_suspended(tenant)
        except Exception as exc:
            _say(f"cannot read the tenant: {type(exc).__name__}; nothing was written")
            return 1, False
        if suspended:
            _say("tenant is suspended; nothing was written")
            return 1, False
    try:
        rows = list_rows(tenant, args.device)
    except Exception as exc:
        _say(f"cannot read the devices of the tenant: {type(exc).__name__}")
        return 1, False
    if args.device and not rows:
        _say(f"{args.device} is not linked to tenant {tenant}")
        return 1, False
    if not rows:
        _say(f"tenant {tenant} has no devices")
        if not args.verify:
            return 0, True
    ok = True
    if args.dry_run or args.apply:
        try:
            items = build_plan(tenant, rows, args.to, args.shape)
        except Exception as exc:
            _say(f"cannot validate: {type(exc).__name__}: {getattr(exc, 'message', exc)}")
            return 1, False
        failures = [it for it in items if it.problems]
        if failures:
            _say(f"validation FAILED for {len(failures)} of {len(items)} device(s); nothing was written")
            for it in failures:
                for problem in it.problems:
                    _say(f"  {it.row.urn}: {problem}")
            return 1, False
        _report_plan(items, dry=args.dry_run)
        settle = DEFAULT_SETTLE if args.settle is None else args.settle
        if args.dry_run and args.to == 'iota' and any(it.action == 'move' for it in items):
            _say(f"would wait {settle:g} s between the first PUTs and the row commits")
        if args.apply:
            if progress is not None:
                progress.append(True)
            _moved_at.clear()
            if args.to == 'iota':
                outcomes = apply_iota(lock, tenant, items, args.shape, settle)
            else:
                outcomes = apply_em(lock, tenant, items)
            failed = [o for o in outcomes if o.status == 'failed']
            _say_safe(f"done: {len(outcomes) - len(failed)} ok, {len(failed)} failed")
            if args.to == 'iota' and _moved_at:
                _say_safe(_verify_hint(tenant, args.device, _moved_at[0]))
            ok = not failed
    return None, ok


def main(argv: Optional[List[str]] = None) -> int:
    args = _args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    tenant = args.tenant
    if args.apply:
        # The lock is taken BEFORE the plan is read, and held until the last row is committed.
        progress: list = []
        try:
            with _exclusive_lock(tenant) as lock:
                code, ok = _plan_and_apply(args, tenant, lock, progress)
        except KeyboardInterrupt:
            # Before the apply phase (lock wait, plan) nothing was written. During it the handler of
            # `_Signals` never raises; this is a signal in the instants around that phase.
            _say("interrupted" if progress else "interrupted; nothing was written")
            return 1
        except writer_move_lock.MoveInProgress:
            _say("a profile change or another move is running; nothing was written")
            return 1
        except writer_move_lock.LockUnavailable as exc:
            _say(f"cannot take the writer move lock ({exc}); nothing was written")
            return 1
    else:
        code, ok = _plan_and_apply(args, tenant, None)
    if code is not None:
        return code
    if args.verify:
        try:
            rows = list_rows(tenant)
        except Exception as exc:
            _say(f"verify: FAILED (cannot read the devices: {type(exc).__name__})")
            return 1
        ok = verify(tenant, rows, args.device, args.since_at) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    def _terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)
    sys.exit(main())
