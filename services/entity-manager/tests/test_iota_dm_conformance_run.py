"""End-to-end run of the conformance script against an in-memory broker and IoT Agent."""
import json
from datetime import datetime, timezone
import pathlib
import re
import sys
from unittest.mock import patch
from urllib.parse import urlparse

import pytest

from tests.telemetry_fakes import TENANT  # noqa: F401,I001  (stubs first)

import dm_writer_cutover  # noqa: E402,F401

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import iota_dm_conformance as conf  # noqa: E402
from blueprints.measurements import build_measurements  # noqa: E402

APIKEY = "KEY-NOT-FOR-OUTPUT"
CTX = "http://context.invalid/ctx.json"
IRI = "https://example.invalid/dm/DeviceMeasurement"
SERVER_NOW = "2026-01-01T12:00:00Z"
ENV = {"IOT_AGENT_URL": "http://iota.invalid:4041", "ORION_URL": "http://orion.invalid:1026",
       "CONTEXT_URL": CTX}
SOUTH = "http://south.invalid:7896"


def fake_headers(headers, tenant=None, body=None, has_context_in_body=False):
    headers = dict(headers)
    headers["NGSILD-Tenant"] = tenant
    if body is not None and "@context" in body:
        headers["Content-Type"] = "application/ld+json"
    else:
        headers["Link"] = f"<{CTX}>"
    return headers


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        return self._body


class World:
    """Just enough broker and agent to run the whole series."""

    def __init__(self, *, on_delete=None, no_modified_at=False, south_status=200, agent_extra=None, leave_dm_on_delete=False, noop=False,
                 defer=None, on_south=None, bad_ti=False):
        self.bad_ti = bad_ti
        self.south_ok = 200
        self.alias_device = False
        self.refuse_device = False
        self.baseline_device_wrote = True
        self.dm_value_shift = 0
        self.mapped = False
        self.on_delete, self.no_modified_at = on_delete, no_modified_at
        self.noop, self.defer, self.on_south, self.deferred = noop, defer, on_south, []
        self.entities, self.agent, self.log = {}, {}, []
        self.mod = 0
        self.south_status = south_status
        self.agent_extra = agent_extra
        self.leave_dm_on_delete = leave_dm_on_delete

    # -- helpers
    def _stamp(self, ent):
        self.mod += 1
        if not self.no_modified_at:
            ent["modifiedAt"] = f"2026-01-01T00:00:{self.mod:05d}Z"

    def _ids(self, params):
        pattern = params["idPattern"]
        return [e for i, e in self.entities.items() if re.match(pattern, i)]

    # -- http
    def get(self, url, headers=None, params=None, timeout=None):
        headers, params = headers or {}, params or {}
        path = urlparse(url).path
        if "/ngsi-ld/v1/entities/" in path:
            ent = self.entities.get(path.rsplit("/entities/", 1)[1])
            return Resp(200, json.loads(json.dumps(ent))) if ent else Resp(404, {})
        if path.endswith("/ngsi-ld/v1/entities"):
            typ = params["type"]
            has_link = "Link" in headers
            visible = (typ == "DeviceMeasurement") if has_link else (typ == IRI)
            found = [json.loads(json.dumps(e)) for e in self._ids(params)] if visible else []
            return Resp(200, found, {"NGSILD-Results-Count": str(len(found))})
        if path == "/iot/services":
            return Resp(200, {"services": [{"apikey": APIKEY}]})
        if path == "/iot/about":
            return Resp(200, {"version": "9.9.9", "libVersion": "1.2.3"})
        if path.startswith("/iot/devices/"):
            dev = self.agent.get(path.rsplit("/", 1)[1])
            if not dev:
                return Resp(404, {})
            doc = dict(dev)
            if self.agent_extra:
                doc["attributes"] = doc["attributes"] + [self.agent_extra]
            return Resp(200, doc)
        raise AssertionError(f"GET {url}")

    def post(self, url, headers=None, json=None, params=None, timeout=None):
        if url.endswith("/ngsi-ld/v1/entities"):
            assert headers["Content-Type"] == "application/ld+json" and "Link" not in headers
            ent = {k: v for k, v in json.items() if k != "@context"}
            self._stamp(ent)
            self.entities[ent["id"]] = ent
            return Resp(201)
        if url.endswith("/iot/devices"):
            dev = json["devices"][0]
            assert dev["transport"] == "HTTP" and dev["apikey"] == APIKEY
            self.agent[dev["device_id"]] = {"device_id": dev["device_id"], "attributes": dev["attributes"],
                                            "entity_name": dev["entity_name"]}
            return Resp(201)
        if url.endswith("/iot/json"):
            assert params["k"] == APIKEY
            if self.south_status != 200:
                return Resp(self.south_status, {"message": f"boom {params['k']}"})
            if self.on_south:
                self.on_south()
            for late in self.deferred:   # a write that lands one message late
                self.entities[late["id"]] = late
            self.deferred = []
            if "" in json.values() and self.refuse_device:
                # the Device is refused; a mapped agent still writes the DM of the valid channels
                if self.mapped and not self.noop:
                    self._write_dms_only(params["i"], json)
                elif not self.mapped and self.baseline_device_wrote and not self.noop:
                    self._apply(params["i"], {k: v for k, v in json.items() if v != ""})
                return Resp(207, {"name": "BatchOperationResult"})
            if "" in json.values():   # the broker refuses a null attribute: batch answer 207
                if not self.noop:
                    self._apply(params["i"], {k: v for k, v in json.items() if v != ""})
                return Resp(207, {"name": "BatchOperationResult"})
            if not self.noop:
                self._apply(params["i"], json)
            return Resp(self.south_ok)
        raise AssertionError(f"POST {url}")

    def put(self, url, headers=None, json=None, timeout=None):
        dev_id = url.rsplit("/", 1)[1]
        urn = self.agent[dev_id]["entity_name"]
        if urn not in self.entities:
            return Resp(200, {"name": "ENTITY_NOT_FOUND"})
        self.agent[dev_id]["attributes"] = json["attributes"]
        self.mapped = True
        return Resp(204)

    def delete(self, url, headers=None, timeout=None):
        if "/iot/devices/" in url:
            self.agent.pop(url.rsplit("/", 1)[1], None)
            return Resp(204)
        urn = url.rsplit("/entities/", 1)[1]
        if self.on_delete:
            self.on_delete(urn)
        if self.leave_dm_on_delete and ":DeviceMeasurement:" in urn:
            return Resp(204)
        return Resp(204) if self.entities.pop(urn, None) else Resp(404)

    def _write_dms_only(self, dev_id, message):
        urn = self.agent[dev_id]["entity_name"]
        parts = urn.split(":")
        at = message.get("TimeInstant") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        device = {k: v for k, v in self.entities[urn].items()}
        touched = set()
        for ch in conf.live_channels(parts[3], ":".join(parts[4:])):
            if ch.key in message and message[ch.key] not in ("", None):
                device[ch.name] = {"type": "Property", "value": message[ch.key], "observedAt": at}
                touched.add(ch.dm_id)
        built = build_measurements({**device, "profileCode": {"type": "Property", "value": conf.PROFILE_CODE}},
                                   conf.LIVE_PROFILE)
        for dm in built:
            if dm["id"] in touched:
                for k, v in dm.items():
                    if isinstance(v, dict):
                        v["observedAt"] = at
                if self.dm_value_shift and "numValue" in dm:
                    dm["numValue"]["value"] += self.dm_value_shift
                self._stamp(dm)
                self.entities[dm["id"]] = {**self.entities.get(dm["id"], {}), **dm}

    # -- the agent
    def _apply(self, dev_id, message):
        urn = self.agent[dev_id]["entity_name"]
        device = self.entities[urn]
        parts = urn.split(":")
        channels = conf.live_channels(parts[3], ":".join(parts[4:]))
        at = message.get("TimeInstant") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        touched, dirty, override = set(), False, {}
        for ch in channels:
            if ch.key not in message:
                continue
            raw = message[ch.key]
            if ch.expression:
                raw = eval(ch.expression, {}, {k: v for k, v in message.items()})
            if ch.kind == "number" and isinstance(raw, str) and raw.startswith("0x"):
                device[ch.name] = {"type": "Property", "value": float(int(raw, 16)), "observedAt": at}
                dirty = True   # the Device takes 16, the DM is not written
                continue
            if ch.kind == "number" and raw == "1e3":
                raw, override[ch.dm_id] = 1.0, 1000.0   # Device 1, DM 1000
            if ch.kind == "number":
                try:
                    if isinstance(raw, bool) or raw is None or raw == "":
                        continue
                    raw = float(raw)
                except (TypeError, ValueError):
                    continue
            elif raw is None or (ch.kind == "runtime" and isinstance(raw, bool)):
                continue
            key = "humidity" if self.alias_device and ch.name == "relativeHumidity" else ch.name
            device[key] = {"type": "Property", "value": raw, "observedAt": at}
            touched.add(ch.dm_id)
        if not touched and not dirty:
            if "TimeInstant" in message:   # the Device records the instant; no DM has a reading
                device["TimeInstant"] = {"type": "Property", "value": at}
                self._stamp(device)
                if self.bad_ti:   # the fault the cases look for: a DM touched or created empty
                    dm_id = f"urn:ngsi-ld:DeviceMeasurement:{parts[3]}:{':'.join(parts[4:])}:pressure"
                    dm = self.entities.setdefault(dm_id, {"id": dm_id, "type": "DeviceMeasurement"})
                    self._stamp(dm)
            return
        device["TimeInstant"] = {"type": "Property", "value": at}
        self._stamp(device)
        built = build_measurements({**device, "profileCode": {"type": "Property", "value": conf.PROFILE_CODE}},
                                   conf.LIVE_PROFILE)
        for dm in built:
            if not self.mapped or dm["id"] not in touched:   # a Device-only agent writes no DM
                continue
            for k, v in dm.items():
                if isinstance(v, dict):
                    v["observedAt"] = at
            if "TimeInstant" not in message:
                dm["timeSource"]["value"] = "server"
            if dm["id"] in override:
                dm["numValue"]["value"] = override[dm["id"]]
            self._stamp(dm)
            if self.defer and dm["id"].endswith(self.defer):
                self.deferred.append(dm)
            else:   # an upsert with options=update merges attributes into the stored entity
                self.entities[dm["id"]] = {**self.entities.get(dm["id"], {}), **dm}


@pytest.fixture(autouse=True)
def _patched(monkeypatch):
    monkeypatch.setattr(conf, "inject_fiware_headers", fake_headers)
    monkeypatch.setattr(conf, "_resolve_type_iri", lambda: IRI)
    monkeypatch.delenv(conf.SDM_SHAPE_FLAG, raising=False)


class Clock:
    t = 0.0

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t


def run(world, argv=None, environ=None):
    out, clock = [], Clock()
    # common.iot_agent talks to the agent through its own `requests` name
    with patch.object(sys.modules["common.iot_agent"], "requests", world):
        code = conf.main(argv or ["--tenant", "acme", "--south-url", SOUTH], http=world,
                         sleep=clock.sleep, now=clock.now, say=out.append, environ=environ or ENV,
                         new_id=lambda: "conf-iota-dm-0a1b2c3d")
    return code, out


def summary(out):
    return json.loads(out[-1] if not out[-1].startswith("recorded") else out[-2])


def test_clean_run_conforms_and_deletes_everything():
    world = World()
    code, out = run(world)
    s = summary(out)
    assert code == 0, "\n".join(out)
    assert s["ok"] and s["agent_document_equal"] is True and s["agent_version"] == "9.9.9 / 1.2.3"
    assert world.entities == {} and world.agent == {}
    assert s["left"]["clean"] and s["left"]["dm_ids"] == []
    assert {c["name"] for c in s["cases"]} >= {"partial-with-time-first", "p-invalid-0x10"}
    assert all(d["classification"] == "declared" for d in s["differences"])
    labels = {d["label"] for d in s["differences"]}
    assert "observedAt" in labels and "timeSource-server" in labels
    assert s["time_instant_first"] < s["time_instant_last"]
    assert s["dm_ids"] and all(i.startswith("urn:ngsi-ld:DeviceMeasurement:acme:conf-iota-dm-0a1b2c3d:")
                               for i in s["dm_ids"])


def test_api_key_is_never_printed():
    world = World(south_status=500)
    code, out = run(world)
    assert code == 1 and APIKEY not in "\n".join(out)
    assert any("ABORT" in line for line in out)


def test_failure_still_cleans_up():
    world = World(south_status=500)
    code, out = run(world)
    assert code == 1 and world.entities == {} and world.agent == {}
    assert summary(out)["left"]["clean"]


def test_leftover_entity_is_counted_without_link_and_fails_the_run():
    world = World(leave_dm_on_delete=True)
    code, out = run(world)
    s = summary(out)
    assert code == 1 and not s["left"]["clean"] and s["left"]["dm_ids"]


def test_undeclared_difference_exits_one():
    world = World()
    original = world._apply

    def corrupt(dev_id, message):
        original(dev_id, message)
        for e in world.entities.values():
            if e["type"] == "DeviceMeasurement" and "numValue" in e:
                e["numValue"]["unitCode"] = "ZZZ"

    world._apply = corrupt
    code, out = run(world)
    assert code == 1 and any(d["classification"] == "UNDECLARED" for d in summary(out)["differences"])


def test_agent_document_difference_is_its_own_failed_check():
    world = World(agent_extra={"object_id": "zz", "name": "zz", "type": "Text"})
    code, out = run(world)
    s = summary(out)
    assert code == 1 and s["agent_document_equal"] is False
    assert not [d for d in s["differences"] if d["classification"] == "UNDECLARED"]


def test_refuses_to_start_when_the_device_exists():
    world = World()
    world.entities["urn:ngsi-ld:Device:acme:conf-iota-dm-0a1b2c3d"] = {"id": "x", "type": "Device"}
    code, out = run(world)
    assert code == 1 and any("exists" in line for line in out)
    assert "urn:ngsi-ld:Device:acme:conf-iota-dm-0a1b2c3d" in world.entities  # not deleted by a refused run


def test_refuses_sdm_and_missing_arguments():
    world = World()
    assert run(world, ["--tenant", "acme", "--south-url", SOUTH, "--shape", "sdm"])[0] == 2
    assert run(world, ["--tenant", "acme"])[0] == 2
    assert run(world, ["--tenant", "acme", "--south-url", SOUTH], environ={"ORION_URL": "x"})[0] == 2
    assert run(world, ["--tenant", "Bad Tenant", "--south-url", SOUTH])[0] == 2


def test_cleanup_only_removes_leftovers_and_refuses_other_ids():
    world = World()
    run(world)  # leaves nothing
    urn = "urn:ngsi-ld:Device:acme:conf-iota-dm-0a1b2c3d"
    dm = "urn:ngsi-ld:DeviceMeasurement:acme:conf-iota-dm-0a1b2c3d:pressure"
    world.entities[urn] = {"id": urn, "type": "Device"}
    world.entities[dm] = {"id": dm, "type": "DeviceMeasurement"}
    other = "urn:ngsi-ld:DeviceMeasurement:acme:real-sensor:pressure"
    world.entities[other] = {"id": other, "type": "DeviceMeasurement"}
    code, out = run(world, ["--tenant", "acme", "--cleanup-only", "conf-iota-dm-0a1b2c3d"])
    assert code == 0 and list(world.entities) == [other]
    assert run(world, ["--tenant", "acme", "--cleanup-only", "real-sensor"])[0] == 2


def test_record_file_written_only_on_a_clean_run(tmp_path):
    path = tmp_path / "rec.json"
    code, _ = run(World(), ["--tenant", "acme", "--south-url", SOUTH, "--record", str(path)])
    rec = json.loads(path.read_text())
    assert code == 0 and rec["generator_hash"] == conf.generator_hash() and rec["agent_version"] == "9.9.9 / 1.2.3"
    assert rec["live_run"]
    bad = tmp_path / "bad.json"
    code, _ = run(World(south_status=500), ["--tenant", "acme", "--south-url", SOUTH, "--record", str(bad)])
    assert code == 1 and not bad.exists()


# -- the cases assert their outcome ---------------------------------------------------------

def test_a_run_where_nothing_is_written_fails_every_changing_case():
    world = World(noop=True)
    code, out = run(world)
    s = summary(out)
    changing = [c for c in s["cases"] if c["must_change"]]
    assert code == 1 and changing and all(c["outcome_violations"] for c in changing)
    assert world.entities == {} and world.agent == {}


def test_a_write_that_lands_late_is_a_failure_and_not_carried_into_the_next_case():
    world = World(defer="relativeHumidity")
    code, out = run(world)
    s = summary(out)
    by = {c["name"]: c for c in s["cases"]}
    assert code == 1
    assert by["all-channels"]["outcome_violations"]            # its DM did not land in time
    assert any(c["late_write"] or c["outcome_violations"] for c in s["cases"])


def test_a_dm_changing_where_none_may_change_fails_the_case():
    world = World()
    original = world._apply

    def sneaky(dev_id, message):
        original(dev_id, message)
        if message.get("p") == "ERR":
            dm = world.entities["urn:ngsi-ld:DeviceMeasurement:acme:conf-iota-dm-0a1b2c3d:pressure"]
            world._stamp(dm)

    world._apply = sneaky
    code, out = run(world)
    by = {c["name"]: c for c in summary(out)["cases"]}
    assert code == 1 and by["p-invalid-ERR"]["outcome_violations"]


def test_the_device_may_take_a_stamp_from_an_unusable_value_without_failing():
    world = World()
    original = world._apply

    def stamps(dev_id, message):
        original(dev_id, message)
        if message.get("p") == "ERR":
            ent = world.entities["urn:ngsi-ld:Device:acme:conf-iota-dm-0a1b2c3d"]
            world._stamp(ent)

    world._apply = stamps
    code, out = run(world)
    assert code == 0, "\n".join(out)


def test_quiet_cases_wait_the_fixed_time_and_it_is_checked_against_the_latency():
    code, out = run(World(), ["--tenant", "acme", "--south-url", SOUTH, "--quiet-wait", "10"])
    s = summary(out)
    assert code == 0 and s["quiet_wait"] == 10.0 and 0 < s["max_latency"] <= 3
    quiet = [c for c in s["cases"] if not c["must_change"]]
    assert quiet and all(c["quiet_wait"] == 10.0 for c in quiet)
    assert any("waited 10s" in line for line in out)
    assert all(c["latency"] is not None for c in s["cases"] if c["must_change"])
    code, out = run(World(), ["--tenant", "acme", "--south-url", SOUTH, "--quiet-wait", "0.5"])
    assert code == 1 and summary(out)["quiet_wait_ok"] is False


def test_sigterm_runs_the_cleanup():
    import os
    import signal
    world = World()
    calls = []

    def kill():
        calls.append(1)
        if len(calls) == 3:
            os.kill(os.getpid(), signal.SIGTERM)

    world.on_south = kill
    code, out = run(world)
    assert code == 1 and world.entities == {} and world.agent == {}
    assert any("interrupted" in line for line in out)


def test_the_shape_flag_and_the_sigterm_handler_are_put_back(monkeypatch):
    import signal
    monkeypatch.setenv(conf.SDM_SHAPE_FLAG, "true")
    before = signal.getsignal(signal.SIGTERM)
    run(World())
    import os
    assert os.environ[conf.SDM_SHAPE_FLAG] == "true" and signal.getsignal(signal.SIGTERM) is before
    monkeypatch.delenv(conf.SDM_SHAPE_FLAG)
    run(World())
    assert conf.SDM_SHAPE_FLAG not in os.environ


def test_cleanup_only_takes_only_the_ids_the_script_makes():
    for bad in ("conf-iota-dm-test01", "conf-iota-dm-0a1b2c3", "conf-iota-dm-0A1B2C3D", "conf-iota-dm-0a1b2c3d9"):
        assert run(World(), ["--tenant", "acme", "--cleanup-only", bad])[0] == 2


def test_an_invalid_value_with_a_device_time_must_not_touch_or_create_a_dm():
    code, out = run(World())
    by = {c["name"]: c for c in summary(out)["cases"]}
    assert code == 0 and "p-invalid-ERR-with-time" in by and "unknown-key-with-time" in by
    code, out = run(World(bad_ti=True))
    s = summary(out)
    by = {c["name"]: c for c in s["cases"]}
    assert code == 1
    for name in ("p-invalid-ERR-with-time", "p-invalid-null-with-time", "unknown-key-with-time"):
        assert by[name]["outcome_violations"], name


def test_an_entity_without_modified_at_aborts_the_run():
    world = World(no_modified_at=True)
    code, out = run(world)
    assert code == 1 and any("modifiedAt" in line for line in out if "ABORT" in line)
    assert world.entities == {} and world.agent == {}


def test_cleanup_finishes_under_a_second_signal_and_restores_the_handlers():
    import os
    import signal
    sent = []

    def hit(urn):
        if not sent:
            sent.append(1)
            os.kill(os.getpid(), signal.SIGTERM)
            os.kill(os.getpid(), signal.SIGINT)

    before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
    world = World(on_delete=hit)
    code, out = run(world)
    assert sent and world.entities == {} and world.agent == {}
    assert summary(out)["left"]["clean"]
    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)) == before


def test_one_failing_delete_does_not_stop_the_others():
    failing = []

    def boom(urn):
        if ":DeviceMeasurement:" in urn and not failing:
            failing.append(urn)
            raise RuntimeError("boom")

    world = World(on_delete=boom)
    code, out = run(world)
    assert failing and code == 1 and not summary(out)["left"]["clean"]
    assert list(world.entities) == [failing[0]] or all(":DeviceMeasurement:" not in k or k == failing[0] for k in world.entities)
    assert "urn:ngsi-ld:Device:acme:conf-iota-dm-0a1b2c3d" not in world.entities


def test_baseline_of_the_device_only_agent_is_reported_before_the_mappings_are_put():
    code, out = run(World())
    s = summary(out)
    line = [x for x in out if x.startswith("baseline device-only:")]
    assert line and "status=207" in line[0]
    b = s["baseline"]
    assert b["status"] == 207 and b["device_written"] is True and b["other_channels_written"] is True
    assert out.index(line[0]) < next(i for i, x in enumerate(out) if x.startswith("== partial-with-time-first"))


def test_a_207_where_200_is_expected_is_a_failed_case_not_an_abort():
    world = World()
    world.south_ok = 207
    code, out = run(world)
    s = summary(out)
    assert code == 1 and not any("ABORT" in x for x in out)
    assert any(c["status"] == 207 and c["status_violations"] for c in s["cases"])
    empty = {c["name"]: c for c in s["cases"]}["p-invalid-empty"]
    assert empty["status"] == 207 and empty["status_violations"] == []


def test_the_summary_counts_cases_and_prints_each_undeclared_difference_once():
    world = World()
    original = world._apply

    def corrupt(dev_id, message):
        original(dev_id, message)
        for e in world.entities.values():
            if e["type"] == "DeviceMeasurement" and "numValue" in e:
                e["numValue"]["unitCode"] = "ZZZ"

    world._apply = corrupt
    code, out = run(world)
    s = summary(out)
    total = [d for d in s["differences"] if d["classification"] == "UNDECLARED"]
    printed = [x for x in out if x.lstrip().startswith("[UNDECLARED]")]
    assert code == 1 and len(total) > len(printed) > 0
    assert any(x.startswith(f"cases: {len(s['cases'])}") for x in out)
    assert any("times" in x and "first in" in x for x in out)
    assert s["undeclared_summary"] and all(r["count"] >= 1 for r in s["undeclared_summary"])


def test_the_merged_expectation_follows_number_then_text_in_the_broker():
    code, out = run(World())
    assert code == 0, "\n".join(out)


def test_a_device_read_under_the_alias_key_is_canonicalised():
    world = World()
    world.alias_device = True
    code, out = run(world)
    s = summary(out)
    assert code == 0, "\n".join(out)
    assert not [d for d in s["differences"] if d["classification"] == "UNDECLARED"]


def _refuse_world(**kw):
    world = World()
    world.refuse_device = True
    for k, v in kw.items():
        setattr(world, k, v)
    return world


def test_dms_written_while_the_device_is_refused_are_declared_when_the_baseline_wrote_nothing():
    world = _refuse_world(baseline_device_wrote=False)
    code, out = run(world)
    s = summary(out)
    assert code == 0, "\n".join(out)
    assert s["baseline"]["device_written"] is False
    mixed = {c["name"]: c for c in s["cases"]}["empty-with-valid-others"]
    assert mixed["device_written"] is False and mixed["status"] == 207
    labels = {d["label"] for d in mixed["diffs"] if d["classification"] == "declared"}
    assert "device-refused" in labels
    assert not [d for d in s["differences"] if d["classification"] == "UNDECLARED"]


def test_the_merged_expectation_carries_on_after_a_device_refused_case(monkeypatch):
    real = conf.build_cases

    def more(ti, val):
        return real(ti, val) + [conf.Case("after-p", {"p": 5.5, "TimeInstant": "2026-01-01T09:58:00Z"},
                                          frozenset({"pressure"}), True),
                                conf.Case("after", {"tMax": 7.5, "TimeInstant": "2026-01-01T09:59:00Z"},
                                          frozenset({"airTemperatureMax"}), True)]

    monkeypatch.setattr(conf, "build_cases", more)
    code, out = run(_refuse_world(baseline_device_wrote=False))
    assert code == 0, "\n".join(out)


def test_device_refused_is_not_declared_when_the_baseline_wrote_the_device():
    code, out = run(_refuse_world(baseline_device_wrote=True))
    s = summary(out)
    assert code == 1 and s["baseline"]["device_written"] is True
    mixed = {c["name"]: c for c in s["cases"]}["empty-with-valid-others"]
    assert not [d for d in mixed["diffs"] if d["label"] == "device-refused"]
    assert [d for d in mixed["diffs"] if d["classification"] == "UNDECLARED"]


def test_device_refused_is_not_declared_when_the_dm_differs():
    code, out = run(_refuse_world(baseline_device_wrote=False, dm_value_shift=1))
    mixed = {c["name"]: c for c in summary(out)["cases"]}["empty-with-valid-others"]
    assert code == 1 and not [d for d in mixed["diffs"] if d["label"] == "device-refused"]
    assert [d for d in mixed["diffs"] if d["classification"] == "UNDECLARED"]


def test_the_baseline_is_printed_again_in_the_summary():
    code, out = run(World())
    lines = [x for x in out if x.startswith("baseline device-only")]
    assert len(lines) == 2 and lines[0].split(":", 1)[1] == lines[1].split(":", 1)[1]
