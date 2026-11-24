#!/usr/bin/env python3
"""Live conformance of the IoT Agent as writer of `DeviceMeasurement` (legacy shape).

Creates ONE temporary device (id `conf-iota-dm-<random>`) in a test tenant, gives it the
mappings the branch's own generator produces (`measurement_channels` + `device_attributes`),
sends a fixed series of southbound messages and, after each one, compares every
`DeviceMeasurement` the agent wrote with what `build_measurements` (the entity-manager path)
makes of the Device the agent wrote. Every difference is printed and classified:

  declared    one of the differences of the design (spec 5.6): `observedAt` on the attributes;
              `timeSource` `server` instead of `device` when the message carries no
              `TimeInstant`; `"1e3"` (Device 1, DM 1000); `"0x10"` (Device 16, no DM)
  UNDECLARED  anything else; the run exits 1

Also checked per message: no DM lacks both `numValue` and `textValue`; the DMs of channels the
message does not carry keep their `modifiedAt` and no new DM appears for them. Once, after the
mappings are put: the agent's own device document compared with what was sent, with the
normalisation the cutover command uses (tells whether a rerun of the cutover is a no-op).

Everything the run creates (agent device, Device entity, DM entities) is deleted in a
`finally`; what is left is then counted WITHOUT a Link header by full type IRI (a short type
name without Link counts zero) and printed. `--cleanup-only <device id>` does just that for an
interrupted run. The script never touches a database: the ids and the instants it used are
printed so that telemetry rows can be removed by hand, bounded by `observed_at`.

    python3 iota_dm_conformance.py --tenant <test tenant> --south-url <agent southbound URL>
                                   [--record <path>] [--cleanup-only <device id>]

Environment (all required): IOT_AGENT_URL (agent north), ORION_URL, CONTEXT_URL.
Nothing prints the agent API key. Exit codes: 0 conforms, 1 findings or failure, 2 bad usage.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import secrets
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, NamedTuple, Optional, Sequence, Set, Tuple

_HERE = Path(__file__).resolve().parent
for _p in (_HERE.parent, _HERE.parent.parent):
    if (_p == _HERE.parent or (_p / "common").is_dir()) and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import requests  # noqa: E402

from blueprints.measurements import SDM_SHAPE_FLAG, build_measurements  # noqa: E402
from common.device_measurement_shape import Channel, measurement_channels  # noqa: E402
from common.iot_agent import (  # noqa: E402
    IotAgentError, _check_entity_types, delete_device, get_device, get_or_create_service_group,
    update_device_attributes)
from common.iota_dm_mappings import device_attributes  # noqa: E402
from common.ngsi_headers import inject_fiware_headers  # noqa: E402

DEVICE_PREFIX = "conf-iota-dm-"
PROFILE_CODE = "conf-iota-dm-profile"
SHAPE = "legacy"
CLOCK_TOLERANCE_S = 5.0
OBSERVED_TOLERANCE_S = 60.0
CHANGE_TIMEOUT_S = 20.0
_TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SKIP = frozenset({"id", "type", "@context", "createdAt", "modifiedAt"})
_SYS_SUB = frozenset({"createdAt", "modifiedAt"})

# A synthetic profile: statistics, alias + canonical, a scaling expression whose key is the
# attribute name, text, and a channel with neither unit nor valueType (decided at runtime).
LIVE_MEASUREMENTS: List[Dict[str, Any]] = [
    {"type": "t", "incomingKey": "t", "sdmAttribute": "airTemperature", "unit": "°C",
     "statistics": ["mean", "min", "max"]},
    {"type": "p", "incomingKey": "p", "sdmAttribute": "pressure", "unit": "hPa"},
    {"type": "hum", "incomingKey": "humidity", "sdmAttribute": "humidity", "unit": "%"},
    {"type": "hum", "incomingKey": "relativeHumidity", "sdmAttribute": "relativeHumidity",
     "unit": "%"},
    {"type": "sm", "incomingKey": "soilMoisture", "sdmAttribute": "soilMoisture", "unit": "%",
     "expression": "soilMoisture * 10"},
    {"type": "stat", "incomingKey": "stat", "sdmAttribute": "status", "valueType": "Text"},
    {"type": "rt", "incomingKey": "rt", "sdmAttribute": "windSpeed"},
]
LIVE_PROFILE = {"mapping": {"measurements": LIVE_MEASUREMENTS}}


class Abort(Exception):
    """A step failed; the run stops, cleans up and exits 1."""


# -- pure: ids, channels, hash --------------------------------------------------------------

def device_urn(tenant: str, device_id: str) -> str:
    return f"urn:ngsi-ld:Device:{tenant}:{device_id}"


def dm_prefix(tenant: str, device_id: str) -> str:
    return f"urn:ngsi-ld:DeviceMeasurement:{tenant}:{device_id}:"


def live_channels(tenant: str, device_id: str, shape: str = SHAPE) -> List[Channel]:
    return measurement_channels(
        LIVE_MEASUREMENTS, tenant=tenant, external_id=device_id,
        device_urn=device_urn(tenant, device_id), profile_code=PROFILE_CODE, shape=shape)


def generator_hash(common_dir: Optional[Path] = None) -> str:
    """sha256 of the two generator modules (name and bytes, in a fixed order)."""
    base = Path(common_dir) if common_dir else Path(inspect.getfile(device_attributes)).parent
    digest = hashlib.sha256()
    for name in ("iota_dm_mappings.py", "device_measurement_shape.py"):
        data = (base / name).read_bytes().replace(b"\r\n", b"\n")   # checkout line endings must not matter
        digest.update(name.encode() + b"\0" + data + b"\0")
    return digest.hexdigest()


def absent_dm_ids(channels: Sequence[Channel], message: Mapping[str, Any]) -> FrozenSet[str]:
    """Ids of the DM no key of the message can write."""
    touched = {c.dm_id for c in channels if c.key in message}
    return frozenset({c.dm_id for c in channels} - touched)


def keys_by_dm(channels: Sequence[Channel]) -> Dict[str, Tuple[str, ...]]:
    out: Dict[str, List[str]] = {}
    for c in channels:
        out.setdefault(c.dm_id, []).append(c.key)
    return {k: tuple(v) for k, v in out.items()}


# -- pure: the cases --------------------------------------------------------------------------

KEY_DM = {"t": "airTemperature", "tMin": "airTemperatureMin", "tMax": "airTemperatureMax",
          "p": "pressure", "humidity": "relativeHumidity", "relativeHumidity": "relativeHumidity",
          "soilMoisture": "soilMoisture", "stat": "status", "rt": "windSpeed"}


@dataclass(frozen=True)
class Case:
    """`writes`: names of the DM that must change (and no other); `must_change`: something
    (the Device, at least) must change. A case that must change nothing leaves every DM and the
    Device with the same `modifiedAt`."""
    name: str
    message: Dict[str, Any]
    writes: FrozenSet[str] = frozenset()
    must_change: bool = False
    device_may_change: bool = False   # the Device may take the instant; no DM may change
    ok_statuses: FrozenSet[int] = frozenset({200})   # southbound answers that are not a finding
    device_required: bool = True   # a changing case that writes DMs must also change the Device

    @property
    def has_time_instant(self) -> bool:
        return bool(self.message.get("TimeInstant"))


def build_cases(ti: Callable[[], str], val: Callable[[], float]) -> List[Case]:
    """The message series. `val()` must return a new number each call so that a stale read
    cannot pass; `ti()` a new instant."""
    cases: List[Case] = []

    def add(name: str, message: Dict[str, Any], time_instant: bool = True,
            writes: Optional[Sequence[str]] = None, must_change: Optional[bool] = None,
            device_may_change: bool = False, ok_statuses: Sequence[int] = (200,),
            device_required: bool = True) -> None:
        if time_instant:
            message = {**message, "TimeInstant": ti()}
        names = frozenset(KEY_DM[k] for k in message if k in KEY_DM) if writes is None else frozenset(writes)
        cases.append(Case(name, message, names, bool(names) if must_change is None else must_change,
                          device_may_change, frozenset(ok_statuses), device_required))

    # Partial message WITH TimeInstant while nothing else exists: no DM may appear.
    add("partial-with-time-first", {"p": val()})
    add("all-channels", {"t": val(), "tMin": val(), "tMax": val(), "p": val(),
                         "relativeHumidity": val(), "soilMoisture": val(), "stat": f"ok-{val()}",
                         "rt": val()})
    add("partial-with-time", {"tMax": val()})
    add("partial-without-time", {"p": val()}, time_instant=False)
    add("statistics", {"t": val(), "tMin": val(), "tMax": val()})
    add("statistics-without-time", {"t": val(), "tMin": val(), "tMax": val()}, time_instant=False)
    add("runtime-number", {"rt": val()})
    add("runtime-text", {"rt": f"calm-{val()}"})
    add("runtime-number-without-time", {"rt": val()}, time_instant=False)
    add("runtime-text-without-time", {"rt": f"calm-{val()}"}, time_instant=False)
    add("alias-only", {"humidity": val()})
    add("canonical-only", {"relativeHumidity": val()})
    add("alias-and-canonical", {"humidity": val(), "relativeHumidity": val()})
    add("scaling-key-is-attribute-name", {"soilMoisture": val()})
    add("text", {"stat": f"ok-{val()}"})
    # Unusable values write no DM. Without a TimeInstant the agent may still stamp the Device.
    # `""` is refused by the broker as a null attribute: the agent answers 207 (BatchOperationResult).
    for label, bad in (("null", None), ("ERR", "ERR"), ("12abc", "12abc"), ("empty", "")):
        statuses = (200, 207) if bad == "" else (200,)
        add(f"p-prime-{label}", {"p": val()})
        add(f"p-invalid-{label}", {"p": bad}, time_instant=False, writes=(), device_may_change=True,
            ok_statuses=statuses)
    # The same unusable values WITH a device time: the Device may take the instant, but no DM
    # may be touched or created (the case where a DM used to appear empty).
    for label, bad in (("null", None), ("ERR", "ERR"), ("12abc", "12abc"), ("empty", "")):
        statuses = (200, 207) if bad == "" else (200,)
        add(f"p-prime-{label}-with-time", {"p": val()})
        add(f"p-invalid-{label}-with-time", {"p": bad}, writes=(), device_may_change=True,
            ok_statuses=statuses)
    add("unknown-key-with-time", {"zz": val()}, writes=(), device_may_change=True)
    # A refused `""` next to valid channels: the other channels' DM are written, `p`'s is not.
    add("p-prime-empty-mixed", {"p": val()})
    add("empty-with-valid-others", {"p": "", "tMax": val(), "rt": val()},
        writes=("airTemperatureMax", "windSpeed"), ok_statuses=(200, 207), device_required=False)
    add("p-prime-zero", {"p": val()})
    add("p-invalid-zero", {"p": 0})
    add("p-prime-1e3", {"p": val()})
    add("p-invalid-1e3", {"p": "1e3"}, writes=("pressure",))            # Device 1, DM 1000
    add("p-prime-0x10", {"p": val()})
    add("p-invalid-0x10", {"p": "0x10"}, writes=(), must_change=True)  # Device 16, DM untouched
    return cases


# -- pure: comparison -------------------------------------------------------------------------

class Context(NamedTuple):
    """`server_stamped`: DM whose last write came from a message without `TimeInstant`
    (the agent stamped them with its own clock)."""
    sent: Mapping[str, Any]
    server_stamped: FrozenSet[str]
    keys_by_dm: Mapping[str, Tuple[str, ...]]
    unchanged: FrozenSet[str] = frozenset()   # DM with the same attributes and modifiedAt as before
    device_changed: bool = False
    # per (DM, attribute): (TimeInstant, send time) of the last message required to write it
    last_write: Mapping[Tuple[str, str], Tuple[Optional[str], Optional[datetime]]] = {}


@dataclass(frozen=True)
class Diff:
    dm_id: str
    attr: str
    aspect: str
    expected: Any
    actual: Any
    classification: str          # declared | UNDECLARED
    label: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"dm": self.dm_id, "attr": self.attr, "aspect": self.aspect,
                "expected": self.expected, "actual": self.actual,
                "classification": self.classification, "label": self.label}


def strip_sys(entity: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in entity.items():
        if k in ("createdAt", "modifiedAt"):
            continue
        out[k] = {kk: vv for kk, vv in v.items() if kk not in _SYS_SUB} if isinstance(v, dict) else v
    return out


def _parse_instant(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sent_by(ctx: Context, dm_id: str, wanted: str) -> bool:
    return any(isinstance(ctx.sent.get(k), str) and ctx.sent.get(k) == wanted
               for k in ctx.keys_by_dm.get(dm_id, ()))


def _value(entity: Optional[Mapping[str, Any]], attr: str) -> Any:
    a = (entity or {}).get(attr)
    return a.get("value") if isinstance(a, dict) else None


def _classify(dm_id: str, attr: str, aspect: str, exp: Any, act: Any, ctx: Context,
              expected: Optional[Mapping[str, Any]] = None) -> Tuple[str, str]:
    if aspect == "observedAt" and exp is None and isinstance(act, str):
        # The agent stamps observedAt with the instant of the message that wrote the attribute: its
        # TimeInstant, or (none sent) its own clock, close to the moment the script sent it.
        instant, sent_at = ctx.last_write.get((dm_id, attr), (None, None))
        seen = _parse_instant(act)
        if seen is not None and instant is not None and seen == _parse_instant(instant):
            return "declared", "observedAt"
        if (seen is not None and instant is None and sent_at is not None
                and abs((seen - sent_at).total_seconds()) <= OBSERVED_TOLERANCE_S):
            return "declared", "observedAt"
    if (attr == "timeSource" and aspect == "value" and exp == "device" and act == "server"
            and dm_id in ctx.server_stamped):
        return "declared", "timeSource-server"
    if attr == "dateObserved" and aspect == "value" and dm_id in ctx.server_stamped:
        a, b = _parse_instant(exp), _parse_instant(act)
        if a and b and abs((a - b).total_seconds()) <= CLOCK_TOLERANCE_S:
            return "declared", "server-clock"
    if (attr == "numValue" and aspect == "value" and _sent_by(ctx, dm_id, "1e3")
            and exp == 1 and act == 1000 and not isinstance(act, bool)):
        return "declared", "1e3"
    return "UNDECLARED", ""


def compare_dms(expected: Sequence[Mapping[str, Any]], actual: Sequence[Mapping[str, Any]],
                ctx: Context) -> List[Diff]:
    """Attribute-by-attribute differences between the DM the entity-manager path would build
    (`expected`) and the ones the agent wrote (`actual`)."""
    exp_by = {e["id"]: e for e in expected}
    act_by = {a["id"]: a for a in actual}
    diffs: List[Diff] = []
    for dm_id in sorted(set(exp_by) | set(act_by)):
        mine: List[Diff] = []
        e, a = exp_by.get(dm_id), act_by.get(dm_id)
        if e is None or a is None:
            mine.append(Diff(dm_id, "<entity>", "missing" if a is None else "unexpected",
                             "present" if e else None, "present" if a else None, "UNDECLARED"))
        else:
            if e.get("type") != a.get("type"):
                mine.append(Diff(dm_id, "type", "value", e.get("type"), a.get("type"), "UNDECLARED"))
            for attr in sorted((set(e) | set(a)) - _SKIP):
                ea, aa = e.get(attr), a.get(attr)
                if ea is None or aa is None:
                    mine.append(Diff(dm_id, attr, "attribute", ea, aa, "UNDECLARED"))
                    continue
                ea = ea if isinstance(ea, dict) else {"value": ea}
                aa = aa if isinstance(aa, dict) else {"value": aa}
                for sub in sorted((set(ea) | set(aa)) - _SYS_SUB):
                    if ea.get(sub) == aa.get(sub):
                        continue
                    cls, label = _classify(dm_id, attr, sub, ea.get(sub), aa.get(sub), ctx, e)
                    mine.append(Diff(dm_id, attr, sub, ea.get(sub), aa.get(sub), cls, label))
        if mine and _sent_by(ctx, dm_id, "0x10") and ctx.device_changed and dm_id in ctx.unchanged:
            # Device 16, DM not written (same attributes and modifiedAt): the declared difference
            # is the value and the observation time of that reading; any other attribute that
            # differs keeps its own classification.
            mine = [Diff(d.dm_id, d.attr, d.aspect, d.expected, d.actual, "declared", "0x10")
                    if (d.attr in ("<entity>", "numValue", "dateObserved", "timeSource")
                        or d.aspect == "observedAt") else d
                    for d in mine]
        diffs.extend(mine)
    return diffs


def group_diffs(diffs: Sequence[Diff]) -> List[Dict[str, Any]]:
    """Report form: the declared `observedAt` of one entity is one line (it is on every
    attribute of every DM the agent writes); every other difference stays on its own."""
    out: List[Dict[str, Any]] = []
    grouped: Dict[str, Dict[str, Any]] = {}
    for d in diffs:
        if d.classification == "declared" and d.label == "observedAt":
            g = grouped.get(d.dm_id)
            if g is None:
                g = grouped[d.dm_id] = {"dm": d.dm_id, "attr": "*", "aspect": "observedAt", "attrs": [],
                                        "expected": None, "actual": d.actual,
                                        "classification": "declared", "label": "observedAt"}
                out.append(g)
            g["attrs"].append(d.attr)
        else:
            out.append(d.as_dict())
    return out


def status_violations(case: Case, status: int) -> List[str]:
    if status in case.ok_statuses:
        return []
    return [f"southbound HTTP {status}, expected one of {sorted(case.ok_statuses)}"]


def merge_expected(previous: Optional[Mapping[str, Any]], new: Mapping[str, Any]) -> Dict[str, Any]:
    """What the broker holds after the entity-manager path upserts `new` over `previous`
    (options=update: attributes merge, an old `numValue` stays next to a new `textValue`)."""
    return {**(previous or {}), **new}


def summarize_undeclared(diffs: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Each distinct UNDECLARED difference once, with how many times it appeared and in which
    case it first did. Distinct = (DM, attribute, aspect, expected, actual)."""
    rows: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    for d in diffs:
        if d["classification"] != "UNDECLARED":
            continue
        key = (d["dm"], d["attr"], d["aspect"], json.dumps(d["expected"], sort_keys=True),
               json.dumps(d["actual"], sort_keys=True))
        row = rows.get(key)
        if row is None:
            rows[key] = {"dm": d["dm"], "attr": d["attr"], "aspect": d["aspect"], "expected": d["expected"],
                         "actual": d["actual"], "count": 1, "first_case": d.get("case")}
        else:
            row["count"] += 1
    return list(rows.values())


def dms_without_value(actual: Sequence[Mapping[str, Any]]) -> List[str]:
    bad = []
    for e in actual:
        has = any(isinstance(e.get(k), dict) and e[k].get("value") is not None
                  for k in ("numValue", "textValue"))
        if not has:
            bad.append(e["id"])
    return bad


def untouched_violations(before: Mapping[str, str], after: Mapping[str, str],
                         untouched: Set[str] | FrozenSet[str],
                         touched: Optional[Set[str]] = None) -> List[str]:
    """DM the message cannot write must keep `modifiedAt` and must not appear."""
    out = []
    for dm_id in sorted(untouched):
        if dm_id not in after:
            continue
        if dm_id not in before:
            out.append(f"{dm_id}: created")
        elif after[dm_id] != before[dm_id]:
            out.append(f"{dm_id}: modifiedAt changed")
    if touched is not None:
        for dm_id in sorted(set(after) - set(before) - set(touched) - set(untouched)):
            out.append(f"{dm_id}: created")
    return out


def agent_document_equal(doc: Any, expected: list) -> bool:
    """Same comparison the cutover command uses to decide whether a push is needed."""
    from dm_writer_cutover import _normalise
    if not isinstance(doc, dict):
        return False
    return _normalise(doc.get("attributes")) == _normalise(expected)


def await_outcome(read: Callable[[], Dict[str, str]], done: Callable[[Dict[str, str]], bool], *,
                  sleep: Callable[[float], None], now: Callable[[], float], poll: float = 0.5,
                  quiet: float = 1.5, deadline: float = CHANGE_TIMEOUT_S
                  ) -> Tuple[Dict[str, str], Optional[float]]:
    """Poll until `done(snapshot)`, then until the snapshot has been still for `quiet` seconds.

    Returns (last snapshot, seconds from the start until `done` first held, None if it never
    did within `deadline`)."""
    start = now()
    prev: Optional[Dict[str, str]] = None
    done_at: Optional[float] = None
    last_change = 0.0
    while True:
        sleep(poll)
        cur = read()
        elapsed = now() - start
        if prev is not None and cur != prev:
            last_change = elapsed
        prev = cur
        if done_at is None and done(cur):
            done_at, last_change = elapsed, elapsed
        if done_at is not None and elapsed - last_change >= quiet:
            break
        if elapsed >= deadline:
            break
    return cur, done_at


def hold(read: Callable[[], Dict[str, str]], wait: float, *, sleep: Callable[[float], None],
         now: Callable[[], float], poll: float = 0.5, since_change: bool = False,
         cap: float = CHANGE_TIMEOUT_S) -> Dict[str, str]:
    """Watch for a fixed time and return the last snapshot. With `since_change` the time runs
    from the last observed change (the Device may still be moving), at most `cap` more seconds."""
    start = last_change = now()
    prev: Optional[Dict[str, str]] = None
    while True:
        sleep(poll)
        cur = read()
        t = now()
        if prev is not None and cur != prev:
            last_change = t
        prev = cur
        if since_change:
            if t - last_change >= wait or t - start >= wait + cap:
                return cur
        elif t - start >= wait:
            return cur


def outcome_violations(case: Case, *, device_changed: bool, changed_names: Set[str],
                       existing_names: Set[str], expected_existing: Set[str]) -> List[str]:
    out = []
    changed = (device_changed and not case.device_may_change) or bool(changed_names)
    if case.must_change and not changed:
        out.append("expected a change, none observed")
    if case.must_change and case.writes and case.device_required and not device_changed:
        out.append("the message writes DM but the Device did not change")
    if not case.must_change and changed:
        out.append(f"expected no change; changed: device={device_changed} DM={sorted(changed_names)}")
    if changed_names != set(case.writes):
        out.append(f"DM changed {sorted(changed_names)}, expected {sorted(case.writes)}")
    if existing_names != expected_existing:
        out.append(f"DM existing {sorted(existing_names)}, expected {sorted(expected_existing)}")
    return out


# -- live run ---------------------------------------------------------------------------------

class Conformance:
    def __init__(self, *, http: Any, tenant: str, device_id: str, iota_url: str,
                 south_url: Optional[str], orion_url: str, context_url: str, type_iri: str,
                 say: Callable[[str], None], sleep: Callable[[float], None],
                 now: Callable[[], float], quiet_wait: float = 10.0,
                 utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.http, self.tenant, self.device_id = http, tenant, device_id
        self.iota, self.south, self.orion = iota_url.rstrip("/"), (south_url or "").rstrip("/"), orion_url.rstrip("/")
        self.type_iri, self.say, self.sleep, self.now = type_iri, say, sleep, now
        self.context_url = context_url
        self.urn = device_urn(tenant, device_id)
        self.prefix = dm_prefix(tenant, device_id)
        self._apikey: Optional[str] = None
        self.server_stamped: Set[str] = set()
        self.quiet_wait = quiet_wait
        self.utcnow = utcnow
        self.last_write: Dict[Tuple[str, str], Tuple[Optional[str], datetime]] = {}
        self.expected: Dict[str, Dict[str, Any]] = {}
        self.seen_undeclared: Set[Tuple[str, ...]] = set()
        self.expected_existing: Set[str] = set()
        self.last_final: Optional[Dict[str, str]] = None
        self.prev_result: Optional[Dict[str, Any]] = None
        self.latencies: List[float] = []
        self.channels = live_channels(tenant, device_id)

    # headers / requests -------------------------------------------------------------------
    def _orion_headers(self, *, link: bool = True, body: Any = None) -> Dict[str, str]:
        headers = inject_fiware_headers({"Accept": "application/json"}, tenant=self.tenant, body=body)
        if not link:
            headers.pop("Link", None)
        return headers

    def _iota_headers(self) -> Dict[str, str]:
        return inject_fiware_headers({"Content-Type": "application/json"}, tenant=self.tenant)

    def _redact(self, text: Any) -> str:
        out = str(text)[:300]
        return out.replace(self._apikey, "<apikey>") if self._apikey else out

    def _call(self, what: str, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Abort:
            raise
        except Exception as exc:  # the message of a request error carries the URL (and the key)
            raise Abort(f"{what}: {type(exc).__name__}") from None

    def _get_entity(self, urn: str, *, link: bool = True) -> Optional[dict]:
        resp = self._call("read entity", lambda: self.http.get(
            f"{self.orion}/ngsi-ld/v1/entities/{urn}", headers=self._orion_headers(link=link),
            params={"options": "sysAttrs"}, timeout=15))
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise Abort(f"read entity: HTTP {resp.status_code}")
        return resp.json()

    def _list_dms(self, *, link: bool) -> List[dict]:
        """DM of this device. With `link=False` the full type IRI is used (the only form that
        counts correctly without a Link header)."""
        params = {"type": self.type_iri if not link else "DeviceMeasurement",
                  "idPattern": "^" + self.prefix, "count": "true", "limit": 1000,
                  "options": "sysAttrs"}
        resp = self._call("list DM", lambda: self.http.get(
            f"{self.orion}/ngsi-ld/v1/entities", headers=self._orion_headers(link=link),
            params=params, timeout=30))
        if resp.status_code != 200:
            raise Abort(f"list DM: HTTP {resp.status_code}")
        out = resp.json()
        if not isinstance(out, list):
            raise Abort("list DM: answer is not a list")
        count = resp.headers.get("NGSILD-Results-Count")
        if not link and (count is None or int(count) != len(out)):
            raise Abort(f"list DM: broker counts {count}, read {len(out)}")
        return out

    def _agent_device(self) -> Optional[dict]:
        try:
            return get_device(self.iota, self._iota_headers(), self.device_id)
        except IotAgentError as exc:
            if exc.status_code == 404:
                return None
            raise Abort(f"agent device read: HTTP {exc.status_code}") from None
        except Exception as exc:
            raise Abort(f"agent device read: {type(exc).__name__}") from None

    # start --------------------------------------------------------------------------------
    def agent_version(self) -> Optional[str]:
        try:
            resp = self.http.get(f"{self.iota}/iot/about", headers=self._iota_headers(), timeout=10)
            if resp.status_code != 200:
                return None
            doc = resp.json()
            parts = [str(doc[k]) for k in ("version", "libVersion") if isinstance(doc, dict) and doc.get(k)]
            return " / ".join(parts) or None
        except Exception:
            return None

    def preflight(self) -> None:
        """Refuse to start when anything with this id exists."""
        if self._agent_device() is not None:
            raise Abort("an agent device with this id exists")
        if self._get_entity(self.urn) is not None:
            raise Abort("a Device entity with this id exists")
        if self._list_dms(link=False):
            raise Abort("DeviceMeasurement entities with this id prefix exist")

    def create_entity(self) -> None:
        body = {"@context": self.context_url, "id": self.urn, "type": "Device",
                "profileCode": {"type": "Property", "value": PROFILE_CODE}}
        resp = self._call("create Device", lambda: self.http.post(
            f"{self.orion}/ngsi-ld/v1/entities", headers=self._orion_headers(body=body),
            json=body, timeout=15))
        if resp.status_code not in (201, 204):
            raise Abort(f"create Device: HTTP {resp.status_code}")

    def provision_device_only(self) -> None:
        self._apikey = get_or_create_service_group(self.tenant, self.iota, self._iota_headers(),
                                                   cbroker=self.orion)
        if not self._apikey:
            raise Abort("agent service group unavailable")
        device_only = device_attributes(LIVE_MEASUREMENTS)
        _check_entity_types("Device", device_only)
        body = {"devices": [{
            "device_id": self.device_id, "apikey": self._apikey, "entity_name": self.urn,
            "entity_type": "Device", "protocol": "IoTA-JSON", "transport": "HTTP",
            "explicitAttrs": True, "attributes": device_only, "lazy": [], "commands": [],
            "static_attributes": []}]}
        resp = self._call("provision device", lambda: self.http.post(
            f"{self.iota}/iot/devices", headers=self._iota_headers(), json=body, timeout=15))
        if resp.status_code != 201:
            raise Abort(f"provision device: HTTP {resp.status_code} {self._redact(resp.text)}")

    def put_mappings(self) -> list:
        mappings = device_attributes(LIVE_MEASUREMENTS, self.channels, device_urn=self.urn)
        try:
            update_device_attributes(self.iota, self._iota_headers(), self.device_id, mappings)
        except IotAgentError as exc:
            raise Abort(f"put mappings: HTTP {exc.status_code} {self._redact(exc.body)}") from None
        except Exception as exc:
            raise Abort(f"put mappings: {type(exc).__name__}") from None
        return mappings


    # per message ---------------------------------------------------------------------------
    def state(self) -> Tuple[Optional[dict], List[dict]]:
        return self._get_entity(self.urn), self._list_dms(link=True)

    @staticmethod
    def snapshot(state: Tuple[Optional[dict], List[dict]]) -> Dict[str, str]:
        device, dms = state
        snap = {}
        for e in list(dms) + ([device] if device else []):
            if not e.get("modifiedAt"):
                raise Abort(f"{e.get('id')}: the broker returned no modifiedAt (sysAttrs); cannot judge changes")
            snap[e["id"]] = e["modifiedAt"]
        return snap

    def send(self, message: Mapping[str, Any]) -> int:
        """The southbound HTTP status. Only a dead or refusing endpoint aborts; any other
        status is recorded by the caller (a 207 is an expected answer for some cases)."""
        resp = self._call("southbound message", lambda: self.http.post(
            f"{self.south}/iot/json", params={"k": self._apikey, "i": self.device_id},
            json=dict(message), timeout=15))
        if resp.status_code in (401, 403, 404) or resp.status_code >= 500:
            raise Abort(f"southbound message: HTTP {resp.status_code} {self._redact(resp.text)}")
        return resp.status_code

    def baseline_device_only(self, message: Mapping[str, Any]) -> Dict[str, Any]:
        """What the Device-only agent (today's behaviour) does with the mixed message; reported,
        not judged."""
        before_snap = self.snapshot(self.stable_state())
        status = self.send(message)
        read = lambda: self.snapshot(self.state())  # noqa: E731
        await_outcome(read, lambda snap: snap.get(self.urn) != before_snap.get(self.urn),
                      sleep=self.sleep, now=self.now)
        device = self.state()[0] or {}
        others = {k: v for k, v in message.items() if k in KEY_DM and v != ""}
        written = {k: (device.get(KEY_DM[k]) or {}).get("value") == v for k, v in others.items()}
        out = {"status": status, "device_written": device.get("modifiedAt") != before_snap.get(self.urn),
               "other_channels_written": bool(written) and all(written.values())}
        self.say(f"baseline device-only: status={status} device_written={out['device_written']} "
                 f"other_channels_written={out['other_channels_written']}")
        return out

    def stable_state(self, gap: float = 1.0) -> Tuple[Optional[dict], List[dict]]:
        """Read until two reads `gap` apart agree: a case never starts on a moving state."""
        start = self.now()
        cur = self.state()
        while True:
            self.sleep(gap)
            nxt = self.state()
            if self.snapshot(nxt) == self.snapshot(cur):
                return nxt
            cur = nxt
            if self.now() - start >= CHANGE_TIMEOUT_S:
                raise Abort("the state does not settle")

    def check_late_write(self, state: Tuple[Optional[dict], List[dict]]) -> None:
        """A write that lands after its case ended is a failure of that case."""
        if self.last_final is not None and self.snapshot(state) != self.last_final and self.prev_result:
            self.prev_result["late_write"] = True
            self.say(f"   [FAIL] {self.prev_result['name']}: a write landed after the case ended")

    def run_case(self, case: Case) -> Dict[str, Any]:
        before = self.stable_state()
        self.check_late_write(before)
        before_snap = self.snapshot(before)
        expected_ids = ({self.prefix + n for n in case.writes}
                        | ({self.urn} if case.must_change and case.device_required else set()))

        def done(snap: Dict[str, str]) -> bool:
            return all(snap.get(i) != before_snap.get(i) for i in expected_ids)

        sent_at = self.utcnow()
        status = self.send(case.message)
        read = lambda: self.snapshot(self.state())  # noqa: E731
        latency: Optional[float] = None
        if case.must_change:
            _, latency = await_outcome(read, done, sleep=self.sleep, now=self.now)
            if latency is not None:
                self.latencies.append(latency)
        else:
            hold(read, self.quiet_wait, sleep=self.sleep, now=self.now,
                 since_change=case.device_may_change)
        device, dms = self.state()
        if device is None:
            raise Abort("the Device entity disappeared")
        final_snap = self.snapshot((device, dms))
        before_by = {e["id"]: e for e in before[1]}
        after_by = {e["id"]: e for e in dms}
        names = lambda ids: {i.rsplit(":", 1)[-1] for i in ids}  # noqa: E731
        changed_ids = {i for i in set(before_by) | set(after_by)
                       if before_by.get(i, {}).get("modifiedAt") != after_by.get(i, {}).get("modifiedAt")}
        unchanged = frozenset(i for i in set(before_by) | set(after_by) | {c.dm_id for c in self.channels}
                              if before_by.get(i) == after_by.get(i))
        device_changed = (before[0] or {}).get("modifiedAt") != device.get("modifiedAt")
        for dm_id in changed_ids:   # written by this message
            (self.server_stamped.discard if case.has_time_instant else self.server_stamped.add)(dm_id)
        self.expected_existing |= set(case.writes)
        violations = outcome_violations(
            case, device_changed=device_changed, changed_names=names(changed_ids),
            existing_names=names(after_by), expected_existing=set(self.expected_existing))
        if case.must_change and latency is None:
            violations.append(f"the change did not complete within {CHANGE_TIMEOUT_S:.0f} s")
        entity = strip_sys(device)
        before_device = strip_sys(before[0] or {})
        entity["profileCode"] = {"type": "Property", "value": PROFILE_CODE}
        built = {e["id"]: e for e in build_measurements(entity, LIVE_PROFILE)}
        for dm_id, dm in built.items():
            # The entity-manager path writes a DM when its channel's Device attribute changed,
            # merging into what the broker already holds.
            name = dm_id.rsplit(":", 1)[-1]
            if before_device.get(name) != entity.get(name):
                self.expected[dm_id] = merge_expected(self.expected.get(dm_id), dm)
        for name in case.writes:   # the attributes this message was required to write
            for attr in built.get(self.prefix + name, {}):
                if attr not in ("id", "type"):
                    self.last_write[(self.prefix + name, attr)] = (case.message.get("TimeInstant"), sent_at)
        ctx = Context(case.message, frozenset(self.server_stamped), keys_by_dm(self.channels),
                      unchanged, device_changed, dict(self.last_write))
        diffs = compare_dms(list(self.expected.values()), [strip_sys(d) for d in dms], ctx)
        grouped = group_diffs(diffs)
        for d in grouped:
            d["case"] = case.name
        result = {
            "name": case.name, "sent": case.message, "must_change": case.must_change,
            "device_may_change": case.device_may_change,
            "writes": sorted(case.writes), "latency": latency, "quiet_wait": None if case.must_change else self.quiet_wait,
            "diffs": grouped, "without_value": dms_without_value(dms),
            "outcome_violations": violations, "late_write": False,
            "status": status, "ok_statuses": sorted(case.ok_statuses),
            "status_violations": status_violations(case, status), "device_written": device_changed,
        }
        self.last_final, self.prev_result = final_snap, result
        self._print_case(result)
        return result

    def finish(self) -> None:
        """After the last case: the state must still be the one that case left."""
        self.check_late_write(self.stable_state())

    def _print_case(self, r: Dict[str, Any]) -> None:
        wait = (f"latency={r['latency']:.1f}s" if r["latency"] is not None else "latency=NONE") if r["must_change"] \
            else f"{'no DM' if r['device_may_change'] else 'nothing'} may change; waited {r['quiet_wait']:.0f}s"
        self.say(f"== {r['name']}: sent={json.dumps(r['sent'])} {wait}")
        observed = [d for d in r["diffs"] if d["attr"] == "*"]
        if observed:
            self.say("   [declared:observedAt] on every attribute of: "
                     + ", ".join(d["dm"].rsplit(":", 1)[-1] for d in observed))
        for d in r["diffs"]:
            if d["attr"] == "*":
                continue
            if d["classification"] == "UNDECLARED":   # once; the summary has the counts
                key = (d["dm"], d["attr"], d["aspect"], json.dumps(d["expected"]), json.dumps(d["actual"]))
                if key in self.seen_undeclared:
                    continue
                self.seen_undeclared.add(key)
            tag = f"[{d['classification']}{':' + d['label'] if d['label'] else ''}]"
            where = f"{d['dm'].rsplit(':', 1)[-1]}.{d['attr']}.{d['aspect']}"
            self.say(f"   {tag} {where} expected={json.dumps(d['expected'])} actual={json.dumps(d['actual'])}")
        for v in r["status_violations"]:
            self.say(f"   [FAIL] status: {v}")
        if "empty" in r["name"] or r["name"] == "empty-with-valid-others":
            self.say(f"   status={r['status']}; Device written: {'yes' if r['device_written'] else 'no'}")
        for dm_id in r["without_value"]:
            self.say(f"   [FAIL] DM without value: {dm_id}")
        for v in r["outcome_violations"]:
            self.say(f"   [FAIL] outcome: {v}")

    # cleanup -------------------------------------------------------------------------------
    def cleanup(self) -> Dict[str, Any]:
        """Delete what exists under this device id, then count what is left. A second SIGTERM
        or Ctrl-C does not interrupt it."""
        saved = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                saved[sig] = signal.signal(sig, signal.SIG_IGN)
            except ValueError:   # not the main thread
                pass
        try:
            return self._cleanup()
        finally:
            for sig, handler in saved.items():
                signal.signal(sig, handler)

    def _cleanup(self) -> Dict[str, Any]:
        problems: List[str] = []

        def attempt(what: str, fn: Callable[[], bool]) -> None:
            try:
                if not fn():
                    problems.append(what)
            except (Exception, Terminated, KeyboardInterrupt) as exc:
                problems.append(f"{what}: {type(exc).__name__}")

        attempt("agent device not deleted", lambda: delete_device(self.iota, self._iota_headers(), self.device_id))
        try:
            ids = sorted({e["id"] for e in self._list_dms(link=False)}
                         | {e["id"] for e in self._list_dms(link=True)})
        except (Exception, Terminated, KeyboardInterrupt) as exc:
            ids = [c.dm_id for c in self.channels]
            problems.append(f"DM listing before delete: {type(exc).__name__}")
        for urn in ids + [self.urn]:
            attempt(f"entity not deleted: {urn}", lambda urn=urn: self.http.delete(
                f"{self.orion}/ngsi-ld/v1/entities/{urn}", headers=self._orion_headers(),
                timeout=15).status_code in (204, 404))
        left: Dict[str, Any] = {"agent_device": None, "device_entity": None, "dm_ids": None}
        try:
            left["agent_device"] = 0 if self._agent_device() is None else 1
            left["device_entity"] = 0 if self._get_entity(self.urn, link=False) is None else 1
            left["dm_ids"] = sorted(e["id"] for e in self._list_dms(link=False))
        except (Exception, Terminated, KeyboardInterrupt) as exc:
            problems.append(f"count after delete: {exc if isinstance(exc, Abort) else type(exc).__name__}")
        clean = (not problems and left["agent_device"] == 0 and left["device_entity"] == 0
                 and left["dm_ids"] == [])
        left["problems"], left["clean"] = problems, clean
        self.say(f"cleanup: agent device left={left['agent_device']} Device entity left="
                 f"{left['device_entity']} DM left (full IRI, no Link)="
                 f"{None if left['dm_ids'] is None else len(left['dm_ids'])}")
        for p in problems:
            self.say(f"   [FAIL] {p}")
        return left


# -- command ----------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Live conformance of the IoT Agent DeviceMeasurement writer.")
    p.add_argument("--tenant", required=True, help="test tenant id")
    p.add_argument("--south-url", help="IoT Agent southbound HTTP URL (required for a live run)")
    p.add_argument("--shape", default=SHAPE, help="only 'legacy' is accepted on a live run")
    p.add_argument("--cleanup-only", metavar="DEVICE_ID", help="delete what an interrupted run left")
    p.add_argument("--record", metavar="PATH", help="write the hash record file after a clean run")
    p.add_argument("--quiet-wait", type=float, default=10.0, metavar="SECONDS",
                   help="fixed wait after a message that must change nothing (default 10); the run "
                        "fails when it is under 3 times the longest latency measured")
    return p


def _resolve_type_iri() -> str:
    from dm_writer_cutover import load_terms
    return load_terms()["DeviceMeasurement"]


class Terminated(BaseException):
    """SIGTERM: unwind through the `finally` that cleans up."""


def _on_sigterm(signum: int, frame: Any) -> None:
    raise Terminated()


def main(argv: Optional[Sequence[str]] = None, **kwargs: Any) -> int:
    """Run the command; the process-wide shape flag and SIGTERM handler are put back after."""
    previous_flag = os.environ.get(SDM_SHAPE_FLAG)
    try:
        previous_handler = signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:   # not the main thread
        previous_handler = None
    try:
        return _main(argv, **kwargs)
    finally:
        if previous_flag is None:
            os.environ.pop(SDM_SHAPE_FLAG, None)
        else:
            os.environ[SDM_SHAPE_FLAG] = previous_flag
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, previous_handler)


def _main(argv: Optional[Sequence[str]] = None, *, http: Any = requests, sleep: Callable[[float], None] = time.sleep,
         now: Callable[[], float] = time.monotonic, say: Callable[[str], None] = None,
         environ: Optional[Mapping[str, str]] = None, new_id: Optional[Callable[[], str]] = None,
         utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> int:
    say = say or (lambda text: print(text, flush=True))
    env = os.environ if environ is None else environ
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)
    if not _TENANT_RE.match(args.tenant):
        say("bad --tenant")
        return 2
    if args.shape != SHAPE:
        say(f"refused: a live run writes the '{SHAPE}' shape only")
        return 2
    missing = [n for n in ("IOT_AGENT_URL", "ORION_URL", "CONTEXT_URL") if not env.get(n)]
    if missing:
        say("missing environment: " + ", ".join(missing))
        return 2
    if args.cleanup_only:
        if not re.fullmatch(re.escape(DEVICE_PREFIX) + r"[0-9a-f]{8}", args.cleanup_only):
            say(f"refused: --cleanup-only takes a device id of the form {DEVICE_PREFIX}<8 hex digits>")
            return 2
        device_id = args.cleanup_only
    else:
        if not args.south_url:
            say("--south-url is required for a live run")
            return 2
        device_id = (new_id or (lambda: DEVICE_PREFIX + secrets.token_hex(4)))()
    os.environ[SDM_SHAPE_FLAG] = "false"   # build_measurements must build the legacy shape
    try:
        type_iri = _resolve_type_iri()
    except Exception as exc:
        say(f"cannot resolve the DeviceMeasurement type IRI: {type(exc).__name__}")
        return 1
    run = Conformance(http=http, tenant=args.tenant, device_id=device_id, iota_url=env["IOT_AGENT_URL"],
                      south_url=args.south_url, orion_url=env["ORION_URL"], context_url=env["CONTEXT_URL"],
                      type_iri=type_iri,
                      say=say, sleep=sleep, now=now, quiet_wait=args.quiet_wait, utcnow=utcnow)
    if args.cleanup_only:
        say(f"cleanup-only: {device_id}")
        left = run.cleanup()
        say(json.dumps({"device_id": device_id, "left": left}))
        return 0 if left["clean"] else 1

    code_hash = generator_hash()   # the code that runs, read before anything else happens
    started = utcnow()
    results: List[Dict[str, Any]] = []
    agent_eq: Optional[bool] = None
    baseline: Optional[Dict[str, Any]] = None
    failure: Optional[str] = None
    version = run.agent_version()
    instants: List[str] = []
    try:
        run.preflight()
    except Abort as exc:   # nothing was created: nothing to clean up (and nothing that is ours to delete)
        say(f"refused: {exc}")
        return 1
    try:
        say(f"device {device_id} (agent version: {version})")
        run.create_entity()
        run.provision_device_only()
        counter = iter(range(1, 10_000))
        base = started - timedelta(minutes=10)
        ti = lambda: (base + timedelta(seconds=next(counter))).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        val = lambda: 100 + next(counter) * 1.25  # noqa: E731
        mixed = {"p": "", "tMax": val(), "rt": val(), "TimeInstant": ti()}
        instants.append(mixed["TimeInstant"])
        baseline = run.baseline_device_only(mixed)
        mappings = run.put_mappings()
        doc = run._agent_device()
        agent_eq = agent_document_equal(doc, mappings)
        say(f"check agent-document-equal: {agent_eq}")
        if not agent_eq:
            say("   the agent's document differs from the mappings sent (a cutover rerun would push again)")
        cases = build_cases(ti=ti, val=val)
        for case in cases:
            if case.message.get("TimeInstant"):
                instants.append(case.message["TimeInstant"])
            results.append(run.run_case(case))
        run.finish()
    except Abort as exc:
        failure = str(exc)
        say(f"ABORT: {failure}")
    except (Terminated, KeyboardInterrupt) as exc:
        failure = f"interrupted ({type(exc).__name__})"
        say(f"ABORT: {failure}")
    finally:
        left = run.cleanup()
    finished = utcnow()

    flat = [d for r in results for d in r["diffs"]]
    undeclared = [d for d in flat if d["classification"] == "UNDECLARED"]
    check_failures = [r["name"] for r in results
                      if r["without_value"] or r["outcome_violations"] or r["late_write"]
                      or r["status_violations"]]
    max_latency = max(run.latencies, default=0.0)
    quiet_ok = args.quiet_wait >= 3 * max_latency
    say(f"quiet-wait {args.quiet_wait:.0f}s; longest measured latency {max_latency:.1f}s; "
        f"{'ok' if quiet_ok else 'FAIL: quiet-wait is under 3 times the longest latency, rerun with a longer --quiet-wait'}")
    ok = (failure is None and agent_eq is True and not undeclared and not check_failures
          and quiet_ok and left["clean"] and len(results) > 0)
    summary_rows = summarize_undeclared(flat)
    say(f"cases: {len(results)}; differences: {len(flat)} ({len(undeclared)} UNDECLARED, "
        f"{len(summary_rows)} distinct); check failures: {check_failures or 'none'}")
    for row in summary_rows:
        say(f"   distinct UNDECLARED: {row['dm'].rsplit(':', 1)[-1]}.{row['attr']}.{row['aspect']} "
            f"expected={json.dumps(row['expected'])} actual={json.dumps(row['actual'])} "
            f"({row['count']} times, first in {row['first_case']})")
    say(f"DM ids used: {', '.join(c.dm_id for c in run.channels)}")
    say(f"Device entity: {run.urn}")
    say(f"TimeInstant first/last: {min(instants) if instants else None} / {max(instants) if instants else None}; "
        f"run window (server time): {started.isoformat()} / {finished.isoformat()}")
    record = {"generator_hash": code_hash, "agent_version": version,
              "live_run": finished.strftime("%Y-%m-%dT%H:%M:%SZ")}
    summary = {
        "ok": ok, "generator_hash": record["generator_hash"], "agent_version": version, "shape": SHAPE,
        "device_id": device_id, "failure": failure, "agent_document_equal": agent_eq,
        "baseline": baseline, "undeclared_summary": summary_rows, "quiet_wait": args.quiet_wait, "max_latency": max_latency, "quiet_wait_ok": quiet_ok,
        "dm_ids": [c.dm_id for c in run.channels], "device_entity": run.urn,
        "time_instant_first": min(instants) if instants else None,
        "time_instant_last": max(instants) if instants else None,
        "run_started": started.isoformat(), "run_finished": finished.isoformat(),
        "cases": results, "differences": flat, "left": left,
    }
    say(json.dumps(summary, sort_keys=True))
    if ok and args.record:
        Path(args.record).write_text(json.dumps(record, indent=2) + "\n")
        say(f"recorded {args.record}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
