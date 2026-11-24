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
_TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SKIP = frozenset({"id", "type", "@context", "createdAt", "modifiedAt"})
_SYS_SUB = frozenset({"createdAt", "modifiedAt"})

# A synthetic profile: statistics, alias + canonical, a scaling expression whose key is the
# attribute name, text, and a channel with neither unit nor valueType (decided at runtime).
LIVE_MEASUREMENTS: List[Dict[str, Any]] = [
    {"type": "t", "incomingKey": "t", "sdmAttribute": "airTemperature", "unit": "°C",
     "statistics": ["mean", "min", "max"]},
    {"type": "p", "incomingKey": "p", "sdmAttribute": "pressure", "unit": "hPa"},
    {"type": "hum_alias", "incomingKey": "humidity", "sdmAttribute": "humidity", "unit": "%"},
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
        digest.update(name.encode() + b"\0" + (base / name).read_bytes() + b"\0")
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

@dataclass(frozen=True)
class Case:
    name: str
    message: Dict[str, Any]

    @property
    def has_time_instant(self) -> bool:
        return bool(self.message.get("TimeInstant"))


def build_cases(ti: Callable[[], str], val: Callable[[], float]) -> List[Case]:
    """The message series. `val()` must return a new number each call so that a stale read
    cannot pass; `ti()` a new instant."""
    cases: List[Case] = []

    def add(name: str, message: Dict[str, Any], time_instant: bool = True) -> None:
        if time_instant:
            message = {**message, "TimeInstant": ti()}
        cases.append(Case(name, message))

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
    for label, bad in (("null", None), ("ERR", "ERR"), ("12abc", "12abc"), ("empty", ""),
                       ("zero", 0), ("1e3", "1e3"), ("0x10", "0x10")):
        add(f"p-prime-{label}", {"p": val()})
        add(f"p-invalid-{label}", {"p": bad})
    return cases


# -- pure: comparison -------------------------------------------------------------------------

class Context(NamedTuple):
    """`server_stamped`: DM whose last write came from a message without `TimeInstant`
    (the agent stamped them with its own clock)."""
    sent: Mapping[str, Any]
    server_stamped: FrozenSet[str]
    keys_by_dm: Mapping[str, Tuple[str, ...]]


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


def _classify(dm_id: str, attr: str, aspect: str, exp: Any, act: Any, ctx: Context) -> Tuple[str, str]:
    if aspect == "observedAt" and exp is None and isinstance(act, str):
        return "declared", "observedAt"
    if (attr == "timeSource" and aspect == "value" and exp == "device" and act == "server"
            and dm_id in ctx.server_stamped):
        return "declared", "timeSource-server"
    if attr == "dateObserved" and aspect == "value" and dm_id in ctx.server_stamped:
        a, b = _parse_instant(exp), _parse_instant(act)
        if a and b and abs((a - b).total_seconds()) <= CLOCK_TOLERANCE_S:
            return "declared", "server-clock"
    if attr == "numValue" and aspect == "value" and _sent_by(ctx, dm_id, "1e3"):
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
                    cls, label = _classify(dm_id, attr, sub, ea.get(sub), aa.get(sub), ctx)
                    mine.append(Diff(dm_id, attr, sub, ea.get(sub), aa.get(sub), cls, label))
        if mine and _sent_by(ctx, dm_id, "0x10"):
            # Device 16, no DM: the whole entity is the declared difference of this message.
            mine = [Diff(d.dm_id, d.attr, d.aspect,
                         d.expected, d.actual, "declared", "0x10") for d in mine]
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


def settle(read: Callable[[], Dict[str, str]], before: Dict[str, str], *,
           sleep: Callable[[float], None], now: Callable[[], float], poll: float = 0.5,
           quiet: float = 1.5, first_change: float = 4.0, deadline: float = 10.0
           ) -> Tuple[Dict[str, str], bool]:
    """Poll until the snapshot differs from `before` and then stays put for `quiet` seconds.

    Gives up after `first_change` seconds without any change (a message the agent drops
    changes nothing) and after `deadline` seconds in all cases. Returns (snapshot, changed).
    """
    start = now()
    last, changed, last_change = before, False, 0.0
    while True:
        sleep(poll)
        current = read()
        elapsed = now() - start
        if current != last:
            last, last_change = current, elapsed
            if current != before:
                changed = True
        if changed and elapsed - last_change >= quiet:
            break
        if not changed and elapsed >= first_change:
            break
        if elapsed >= deadline:
            break
    return last, changed


# -- live run ---------------------------------------------------------------------------------

class Conformance:
    def __init__(self, *, http: Any, tenant: str, device_id: str, iota_url: str,
                 south_url: Optional[str], orion_url: str, context_url: str, type_iri: str,
                 say: Callable[[str], None], sleep: Callable[[float], None],
                 now: Callable[[], float]):
        self.http, self.tenant, self.device_id = http, tenant, device_id
        self.iota, self.south, self.orion = iota_url.rstrip("/"), (south_url or "").rstrip("/"), orion_url.rstrip("/")
        self.type_iri, self.say, self.sleep, self.now = type_iri, say, sleep, now
        self.context_url = context_url
        self.urn = device_urn(tenant, device_id)
        self.prefix = dm_prefix(tenant, device_id)
        self._apikey: Optional[str] = None
        self.server_stamped: Set[str] = set()
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

    def provision(self) -> list:
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
        snap = {e["id"]: e.get("modifiedAt", "") for e in dms}
        if device:
            snap[device["id"]] = device.get("modifiedAt", "")
        return snap

    def send(self, message: Mapping[str, Any]) -> None:
        resp = self._call("southbound message", lambda: self.http.post(
            f"{self.south}/iot/json", params={"k": self._apikey, "i": self.device_id},
            json=dict(message), timeout=15))
        if resp.status_code not in (200, 201, 204):
            raise Abort(f"southbound message: HTTP {resp.status_code} {self._redact(resp.text)}")

    def run_case(self, case: Case) -> Dict[str, Any]:
        before = self.state()
        before_snap = self.snapshot(before)
        self.send(case.message)
        snap, changed = settle(lambda: self.snapshot(self.state()), before_snap,
                               sleep=self.sleep, now=self.now)
        device, dms = self.state()
        if device is None:
            raise Abort("the Device entity disappeared")
        entity = strip_sys(device)
        entity["profileCode"] = {"type": "Property", "value": PROFILE_CODE}
        expected = build_measurements(entity, LIVE_PROFILE)
        before_dms = {e["id"]: e.get("modifiedAt", "") for e in before[1]}
        dm_after = {e["id"]: e.get("modifiedAt", "") for e in dms}
        for dm_id in dm_after:
            if before_dms.get(dm_id) != dm_after[dm_id]:   # written by this message
                (self.server_stamped.discard if case.has_time_instant else self.server_stamped.add)(dm_id)
        ctx = Context(case.message, frozenset(self.server_stamped), keys_by_dm(self.channels))
        diffs = compare_dms(expected, [strip_sys(d) for d in dms], ctx)
        absent = absent_dm_ids(self.channels, case.message)
        touched = {c.dm_id for c in self.channels} - set(absent)
        result = {
            "name": case.name, "sent": case.message, "changed": changed,
            "diffs": group_diffs(diffs),
            "without_value": dms_without_value(dms),
            "untouched_violations": untouched_violations(before_dms, dm_after, set(absent), touched),
        }
        self._print_case(result)
        return result

    def _print_case(self, r: Dict[str, Any]) -> None:
        self.say(f"== {r['name']}: sent={json.dumps(r['sent'])} changed={r['changed']}")
        observed = [d for d in r["diffs"] if d["attr"] == "*"]
        if observed:
            self.say("   [declared:observedAt] on every attribute of: "
                     + ", ".join(d["dm"].rsplit(":", 1)[-1] for d in observed))
        for d in r["diffs"]:
            if d["attr"] == "*":
                continue
            tag = f"[{d['classification']}{':' + d['label'] if d['label'] else ''}]"
            where = f"{d['dm'].rsplit(':', 1)[-1]}.{d['attr']}.{d['aspect']}"
            self.say(f"   {tag} {where} expected={json.dumps(d['expected'])} actual={json.dumps(d['actual'])}")
        for dm_id in r["without_value"]:
            self.say(f"   [FAIL] DM without value: {dm_id}")
        for v in r["untouched_violations"]:
            self.say(f"   [FAIL] absent channel: {v}")

    # cleanup -------------------------------------------------------------------------------
    def cleanup(self) -> Dict[str, Any]:
        """Delete what exists under this device id, then count what is left."""
        problems: List[str] = []

        def attempt(what: str, fn: Callable[[], bool]) -> None:
            try:
                if not fn():
                    problems.append(what)
            except Exception as exc:
                problems.append(f"{what}: {type(exc).__name__}")

        attempt("agent device not deleted", lambda: delete_device(self.iota, self._iota_headers(), self.device_id))
        try:
            ids = sorted({e["id"] for e in self._list_dms(link=False)}
                         | {e["id"] for e in self._list_dms(link=True)})
        except Exception as exc:
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
        except Abort as exc:
            problems.append(f"count after delete: {exc}")
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
    return p


def _resolve_type_iri() -> str:
    from dm_writer_cutover import load_terms
    return load_terms()["DeviceMeasurement"]


def main(argv: Optional[Sequence[str]] = None, *, http: Any = requests, sleep: Callable[[float], None] = time.sleep,
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
        if not args.cleanup_only.startswith(DEVICE_PREFIX) or not re.fullmatch(r"[A-Za-z0-9_-]+", args.cleanup_only):
            say(f"refused: --cleanup-only takes a device id starting with {DEVICE_PREFIX}")
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
                      say=say, sleep=sleep, now=now)
    if args.cleanup_only:
        say(f"cleanup-only: {device_id}")
        left = run.cleanup()
        say(json.dumps({"device_id": device_id, "left": left}))
        return 0 if left["clean"] else 1

    started = utcnow()
    results: List[Dict[str, Any]] = []
    agent_eq: Optional[bool] = None
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
        mappings = run.provision()
        doc = run._agent_device()
        agent_eq = agent_document_equal(doc, mappings)
        say(f"check agent-document-equal: {agent_eq}")
        if not agent_eq:
            say("   the agent's document differs from the mappings sent (a cutover rerun would push again)")
        counter = iter(range(1, 10_000))
        base = started - timedelta(minutes=10)
        cases = build_cases(
            ti=lambda: (base + timedelta(seconds=next(counter))).strftime("%Y-%m-%dT%H:%M:%SZ"),
            val=lambda: 100 + next(counter) * 1.25)
        for case in cases:
            if case.message.get("TimeInstant"):
                instants.append(case.message["TimeInstant"])
            results.append(run.run_case(case))
    except Abort as exc:
        failure = str(exc)
        say(f"ABORT: {failure}")
    finally:
        left = run.cleanup()
    finished = utcnow()

    flat = [d for r in results for d in r["diffs"]]
    undeclared = [d for d in flat if d["classification"] == "UNDECLARED"]
    check_failures = [r["name"] for r in results if r["without_value"] or r["untouched_violations"]]
    ok = (failure is None and agent_eq is True and not undeclared and not check_failures
          and left["clean"] and len(results) > 0)
    say(f"differences: {len(flat)} ({len(undeclared)} UNDECLARED); check failures: {check_failures or 'none'}")
    say(f"DM ids used: {', '.join(c.dm_id for c in run.channels)}")
    say(f"Device entity: {run.urn}")
    say(f"TimeInstant first/last: {min(instants) if instants else None} / {max(instants) if instants else None}; "
        f"run window (server time): {started.isoformat()} / {finished.isoformat()}")
    record = {"generator_hash": generator_hash(), "agent_version": version,
              "live_run": finished.strftime("%Y-%m-%dT%H:%M:%SZ")}
    summary = {
        "ok": ok, "generator_hash": record["generator_hash"], "agent_version": version, "shape": SHAPE,
        "device_id": device_id, "failure": failure, "agent_document_equal": agent_eq,
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
