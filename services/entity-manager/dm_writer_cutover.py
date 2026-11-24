"""Move devices between the two writers of their DeviceMeasurement entities.

    python -m dm_writer_cutover --tenant T [--device URN] --to iota|em [--shape legacy|sdm]
                                (--dry-run | --apply) [--verify]
    python -m dm_writer_cutover --tenant T [--device URN] --verify

`--to iota` makes the IoT Agent write the DeviceMeasurement entities of a device in the
given `--shape` (required); `--to em` gives them back to the entity-manager (no shape).
`--verify` is read-only and may run alone: it reads the DeviceMeasurement entities of the
tenant from the broker WITHOUT a Link header (full type IRI, `count=true`, paginated; a short
type name without Link counts zero) and runs two separate checks:
  (a) no entity lacks both `numValue` and `textValue`;
  (b) every entity of an `iota` device has exactly the attribute set its shape and profile
      call for, nothing under the default vocabulary and no `TimeInstant` attribute.
The term to IRI mapping comes from the platform context document at CONTEXT_URL (required;
without it, or without a term, the verification fails). `--device` narrows check (b).

Exit codes: 0 done, 1 a validation, device or verification failure, 2 bad arguments.

Order of the writes. A moment where two writers write is acceptable (a reading may appear
twice; the worker's dedup removes it when the message carries `TimeInstant`); a moment where
nobody writes is not.
  * to iota: PUT the new mappings, update the row, PUT again. Between the first PUT and the
    commit both write. The second PUT, after the commit, re-reads the profile and rebuilds the
    mappings: a profile change that ran `push_iota_attributes` while the row still said `em`
    would otherwise leave Device-only mappings on a device the entity-manager now skips.
  * to em: update the row, then PUT the Device-only mappings (profile re-read after the
    commit). Between the commit and the PUT both write. The other order would leave a gap in
    which the agent has stopped and the entity-manager still skips the device. A concurrent
    `push_iota_attributes` that read `iota` before the commit re-reads the state after its
    push, sees `em` and pushes again, so it converges to Device-only.
  * If the row update fails after the first PUT (to iota), the previous mappings are put back,
    but only when the previous state is confirmed by a re-read: when the commit may have gone
    through, restoring would risk the gap, so the new mappings stay and the device is listed.
  * A failure of the PUT after the commit is its own outcome: the row is already updated, the
    device is listed as failed with that text, and a rerun is not a no-op for it (the agent's
    own device document is compared with the expected mappings, so it pushes again).

Never run inside the serving entity-manager pod (it starts no Flask app). Nothing here prints
or logs a key or a secret; only ids.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from psycopg2.extras import RealDictCursor

import dataloggers
from common import iot_agent
from common.device_measurement_shape import SHAPES, measurement_channels
from common.ngsi_headers import inject_fiware_headers
from db_helper import get_db_connection_with_tenant

logger = logging.getLogger("dm_writer_cutover")

PAGE = 1000
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


def set_state(tenant: str, urn: str, state: Tuple[str, Optional[str]]) -> None:
    """Update and commit the row; raises when the row is not there."""
    with get_db_connection_with_tenant(tenant) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE iot_datalogger_devices SET dm_writer = %s, dm_shape = %s "
                    "WHERE tenant_id = %s AND device_urn = %s", (state[0], state[1], tenant, urn))
        if cur.rowcount != 1:
            conn.rollback()
            raise RuntimeError(f"row of {urn} not updated")
        conn.commit()


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
        item.code = dataloggers._prop(entity, 'profileCode')
        if not item.code:
            item.problems.append("profileCode missing")
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

def _push_fresh(tenant: str, row: Row) -> None:
    """Re-read the profile and push what the row's committed state calls for."""
    entity = dataloggers._orion_devices(tenant, [row.urn]).get(row.urn)
    code = dataloggers._prop(entity, 'profileCode') if entity else None
    measurements = dataloggers._profile_measurements(tenant, code) if code else None
    if measurements is None:
        raise RuntimeError("profile not readable")
    dataloggers.push_iota_attributes(tenant, row.urn, row.external_id, code, measurements)


def _put(tenant: str, external_id: str, attrs: list) -> None:
    iot_agent.update_device_attributes(dataloggers.iota_url(), dataloggers.iota_headers(tenant),
                                       external_id, attrs)


def _fail(row: Row, text: str) -> Outcome:
    logger.error("dm_cutover failed: device=%s %s", row.external_id, text)
    return Outcome(row.urn, 'failed', text)


def _move_to_iota(tenant: str, item: Item, target) -> Outcome:
    row = item.row
    try:
        new_attrs = _build(tenant, row, target, item.code, item.measurements)
        _put(tenant, row.external_id, new_attrs)
    except Exception as exc:
        return _fail(row, f"first PUT failed ({type(exc).__name__}); nothing changed")
    try:
        set_state(tenant, row.urn, target)
    except Exception as exc:
        return _after_db_failure(tenant, item, target, exc)
    try:
        _push_fresh(tenant, row)
    except Exception as exc:
        return _fail(row, f"row already updated to {target} but the final PUT failed "
                          f"({type(exc).__name__}); the first mappings stay in the agent; rerun")
    return Outcome(row.urn, 'moved', f"{row.state} -> {target}")


def _after_db_failure(tenant: str, item: Item, target, exc: Exception) -> Outcome:
    row = item.row
    try:
        current = dataloggers.device_dm_state(tenant, row.urn)
    except Exception:
        current = None
    if current == target:
        logger.warning("dm_cutover: update reported %s but the row is in %s: device=%s",
                       type(exc).__name__, target, row.external_id)
        try:
            _push_fresh(tenant, row)
        except Exception as exc2:
            return _fail(row, f"row already updated to {target} but the final PUT failed "
                              f"({type(exc2).__name__}); rerun")
        return Outcome(row.urn, 'moved', f"{row.state} -> {target}")
    if current != row.state:
        logger.critical("INCONSISTENCY: row update failed and the state cannot be confirmed: "
                        "device=%s urn=%s previous=%r target=%r; the new mappings were left "
                        "in the agent (no gap, duplicates possible); check the row and rerun",
                        row.external_id, row.urn, row.state, target)
        return _fail(row, "row update failed and the state could not be confirmed; the new "
                          "mappings were left in the agent; check the row, then rerun")
    try:
        if item.old_attrs is None:
            raise RuntimeError("previous mappings not buildable")
        _put(tenant, row.external_id, item.old_attrs)
    except Exception as exc2:
        logger.critical("INCONSISTENCY: row update failed and the previous mappings were not "
                        "restored: device=%s urn=%s previous=%r target=%r error=%s",
                        row.external_id, row.urn, row.state, target, type(exc2).__name__)
        return _fail(row, "row update failed and the previous mappings could NOT be restored; "
                          "the agent holds the new ones; rerun")
    return _fail(row, f"row update failed ({type(exc).__name__}); previous mappings restored")


def _move_to_em(tenant: str, item: Item, target) -> Outcome:
    row = item.row
    try:
        set_state(tenant, row.urn, target)
    except Exception as exc:
        return _fail(row, f"row update failed ({type(exc).__name__}); the agent was not touched; "
                          f"if the commit went through the agent still has the DM mappings "
                          f"(duplicates, no gap); rerun")
    try:
        _push_fresh(tenant, row)
    except Exception as exc:
        return _fail(row, f"row already updated to {target} but the PUT failed "
                          f"({type(exc).__name__}); the agent still has the DM mappings "
                          f"(duplicate writes, no gap); rerun")
    return Outcome(row.urn, 'moved', f"{row.state} -> {target}")


def apply_item(tenant: str, item: Item, to: str, shape: Optional[str]) -> Outcome:
    row = item.row
    target = _target(to, shape)
    if item.action == 'unchanged':
        return Outcome(row.urn, 'unchanged', item.reason)
    if item.action == 'repair':
        try:
            _push_fresh(tenant, row)
        except Exception as exc:
            return _fail(row, f"PUT failed ({type(exc).__name__}); rerun")
        return Outcome(row.urn, 'repaired', item.reason)
    return _move_to_iota(tenant, item, target) if to == 'iota' else _move_to_em(tenant, item, target)


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
                                    'offset': offset}, timeout=30)
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


def _is_default_vocabulary(key: str) -> bool:
    return key.startswith(DEFAULT_VOCABULARY) or ':' not in key


def _is_time_instant(key: str) -> bool:
    return key == 'TimeInstant' or key.endswith('/TimeInstant')


def verify(tenant: str, rows: List[Row], only_urn: Optional[str]) -> bool:
    """Run both checks; prints the result of each; True when both pass."""
    try:
        terms = load_terms()
        dms = read_dms(tenant, terms['DeviceMeasurement'])
    except RuntimeError as exc:
        _say(f"verify FAILED: {exc}")
        return False
    _say(f"verify: read {len(dms)} DeviceMeasurement entities")

    bad_a = []
    for e in dms:
        if terms['numValue'] not in e and terms['textValue'] not in e:
            bad_a.append(e.get('id'))
    _say("check (a): ok" if not bad_a else
         f"check (a): FAILED ({len(bad_a)} without numValue and textValue)")
    for i in bad_a:
        _say(f"  {i}")

    by_ext = {r.external_id: r for r in rows if r.state[0] == 'iota'
              and (only_urn is None or r.urn == only_urn)}
    profiles: Dict[str, Optional[Dict[str, Any]]] = {}
    problems: List[str] = []
    judged = 0

    def channels_of(row: Row):
        if row.urn in profiles:
            return profiles[row.urn]
        result = None
        try:
            ent = dataloggers._orion_devices(tenant, [row.urn]).get(row.urn)
            code = dataloggers._prop(ent, 'profileCode') if ent else None
            ms = dataloggers._profile_measurements(tenant, code) if code else None
            if ms is not None:
                chans = measurement_channels(ms, tenant=tenant, external_id=row.external_id,
                                             device_urn=row.urn, profile_code=code,
                                             shape=row.state[1])
                result = {c.dm_id: c for c in chans}   # canonical channel last: it wins
        except Exception as exc:
            logger.warning("verify: profile of %s not readable: %s", row.external_id,
                           type(exc).__name__)
        profiles[row.urn] = result
        return result

    for e in dms:
        parts = str(e.get('id', '')).split(':', 5)
        row = by_ext.get(parts[4]) if len(parts) == 6 else None
        if row is None:
            continue
        judged += 1
        keys = {k for k in e if k not in SYSTEM_KEYS}
        issues = []
        offending = sorted(k for k in keys if _is_default_vocabulary(k))
        if offending:
            issues.append(f"default vocabulary keys {offending}")
        ti = sorted(k for k in keys if _is_time_instant(k))
        if ti:
            issues.append(f"TimeInstant attribute {ti}")
        chans = channels_of(row)
        channel = chans.get(e['id']) if chans else None
        if chans is None:
            issues.append("expected attributes unknown (profile not readable)")
        elif channel is None:
            issues.append("no channel of the current profile; only forbidden keys judged")
            if not (offending or ti):
                issues = []
        else:
            required, allowed, either = _expected_keys(channel, row.state[1], terms)
            extra = sorted(k for k in keys - allowed if k not in offending and k not in ti)
            missing = sorted(required - keys)
            if either and not (either & keys):
                missing.append(" or ".join(sorted(either)))
            if extra:
                issues.append(f"unexpected keys {extra}")
            if missing:
                issues.append(f"missing keys {missing}")
        if issues:
            problems.append(f"  {e['id']}: " + "; ".join(issues))
    _say("check (b): ok" + f" ({judged} entities of iota devices judged)" if not problems else
         f"check (b): FAILED ({len(problems)} of {judged} entities)")
    for line in problems:
        _say(line)
    return not bad_a and not problems


# -- command ------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m dm_writer_cutover",
                                description=__doc__.split("\n\n")[0])
    p.add_argument("--tenant", required=True)
    p.add_argument("--device", help="device URN (default: every linked device of the tenant)")
    p.add_argument("--to", choices=("iota", "em"))
    p.add_argument("--shape", choices=SHAPES)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate and print the plan; write nothing")
    mode.add_argument("--apply", action="store_true")
    p.add_argument("--verify", action="store_true", help="read-only check of the broker's DM entities")
    return p


def _args(argv: Optional[List[str]]) -> argparse.Namespace:
    p = _parser()
    a = p.parse_args(argv)
    if a.dry_run or a.apply:
        if not a.to:
            p.error("--to is required with --dry-run/--apply")
        if a.to == 'iota' and not a.shape:
            p.error("--shape is required with --to iota")
        if a.to == 'em' and a.shape:
            p.error("--shape is refused with --to em (the entity-manager has no shape)")
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


def main(argv: Optional[List[str]] = None) -> int:
    args = _args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    tenant = args.tenant
    try:
        rows = list_rows(tenant, args.device)
    except Exception as exc:
        _say(f"cannot read the devices of the tenant: {type(exc).__name__}")
        return 1
    if args.device and not rows:
        _say(f"{args.device} is not linked to tenant {tenant}")
        return 1
    if not rows:
        _say(f"tenant {tenant} has no devices")
        if not args.verify:
            return 0
    ok = True
    if args.dry_run or args.apply:
        try:
            items = build_plan(tenant, rows, args.to, args.shape)
        except Exception as exc:
            _say(f"cannot validate: {type(exc).__name__}: {getattr(exc, 'message', exc)}")
            return 1
        failures = [it for it in items if it.problems]
        if failures:
            _say(f"validation FAILED for {len(failures)} of {len(items)} device(s); nothing was written")
            for it in failures:
                for problem in it.problems:
                    _say(f"  {it.row.urn}: {problem}")
            return 1
        _report_plan(items, dry=args.dry_run)
        if args.apply:
            outcomes = []
            for it in items:
                out = apply_item(tenant, it, args.to, args.shape)
                outcomes.append(out)
                if out.status != 'unchanged':
                    _say(f"{out.urn}: {'FAILED' if out.status == 'failed' else out.status} - {out.text}")
            failed = [o for o in outcomes if o.status == 'failed']
            _say(f"done: {len(outcomes) - len(failed)} ok, {len(failed)} failed")
            ok = not failed
    if args.verify:
        try:
            rows = list_rows(tenant)
        except Exception as exc:
            _say(f"verify FAILED: cannot read the devices: {type(exc).__name__}")
            return 1
        ok = verify(tenant, rows, args.device) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
