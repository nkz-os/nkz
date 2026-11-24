"""End-to-end run of the conformance script against an in-memory broker and IoT Agent."""
import json
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

    def __init__(self, *, south_status=200, agent_extra=None, leave_dm_on_delete=False):
        self.entities, self.agent, self.log = {}, {}, []
        self.mod = 0
        self.south_status = south_status
        self.agent_extra = agent_extra
        self.leave_dm_on_delete = leave_dm_on_delete

    # -- helpers
    def _stamp(self, ent):
        self.mod += 1
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
            self._apply(params["i"], json)
            return Resp(200)
        raise AssertionError(f"POST {url}")

    def put(self, url, headers=None, json=None, timeout=None):
        dev_id = url.rsplit("/", 1)[1]
        urn = self.agent[dev_id]["entity_name"]
        if urn not in self.entities:
            return Resp(200, {"name": "ENTITY_NOT_FOUND"})
        self.agent[dev_id]["attributes"] = json["attributes"]
        return Resp(204)

    def delete(self, url, headers=None, timeout=None):
        if "/iot/devices/" in url:
            self.agent.pop(url.rsplit("/", 1)[1], None)
            return Resp(204)
        urn = url.rsplit("/entities/", 1)[1]
        if self.leave_dm_on_delete and ":DeviceMeasurement:" in urn:
            return Resp(204)
        return Resp(204) if self.entities.pop(urn, None) else Resp(404)

    # -- the agent
    def _apply(self, dev_id, message):
        urn = self.agent[dev_id]["entity_name"]
        device = self.entities[urn]
        parts = urn.split(":")
        channels = conf.live_channels(parts[3], ":".join(parts[4:]))
        at = message.get("TimeInstant") or SERVER_NOW
        touched = set()
        for ch in channels:
            if ch.key not in message:
                continue
            raw = message[ch.key]
            if ch.expression:
                raw = eval(ch.expression, {}, {k: v for k, v in message.items()})
            if ch.kind == "number":
                try:
                    if isinstance(raw, bool) or raw is None or raw == "":
                        continue
                    raw = float(raw)
                except (TypeError, ValueError):
                    continue
            elif raw is None or (ch.kind == "runtime" and isinstance(raw, bool)):
                continue
            device[ch.name] = {"type": "Property", "value": raw, "observedAt": at}
            touched.add(ch.dm_id)
        if not touched:
            return
        device["TimeInstant"] = {"type": "Property", "value": at}
        self._stamp(device)
        built = build_measurements({**device, "profileCode": {"type": "Property", "value": conf.PROFILE_CODE}},
                                   conf.LIVE_PROFILE)
        for dm in built:
            if dm["id"] not in touched:
                continue
            for k, v in dm.items():
                if isinstance(v, dict):
                    v["observedAt"] = at
            if "TimeInstant" not in message:
                dm["timeSource"]["value"] = "server"
            self._stamp(dm)
            self.entities[dm["id"]] = dm


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
                         new_id=lambda: "conf-iota-dm-test01")
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
    assert s["dm_ids"] and all(i.startswith("urn:ngsi-ld:DeviceMeasurement:acme:conf-iota-dm-test01:")
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
    world.entities["urn:ngsi-ld:Device:acme:conf-iota-dm-test01"] = {"id": "x", "type": "Device"}
    code, out = run(world)
    assert code == 1 and any("exists" in line for line in out)
    assert "urn:ngsi-ld:Device:acme:conf-iota-dm-test01" in world.entities  # not deleted by a refused run


def test_refuses_sdm_and_missing_arguments():
    world = World()
    assert run(world, ["--tenant", "acme", "--south-url", SOUTH, "--shape", "sdm"])[0] == 2
    assert run(world, ["--tenant", "acme"])[0] == 2
    assert run(world, ["--tenant", "acme", "--south-url", SOUTH], environ={"ORION_URL": "x"})[0] == 2
    assert run(world, ["--tenant", "Bad Tenant", "--south-url", SOUTH])[0] == 2


def test_cleanup_only_removes_leftovers_and_refuses_other_ids():
    world = World()
    run(world)  # leaves nothing
    urn = "urn:ngsi-ld:Device:acme:conf-iota-dm-test01"
    dm = "urn:ngsi-ld:DeviceMeasurement:acme:conf-iota-dm-test01:pressure"
    world.entities[urn] = {"id": urn, "type": "Device"}
    world.entities[dm] = {"id": dm, "type": "DeviceMeasurement"}
    other = "urn:ngsi-ld:DeviceMeasurement:acme:real-sensor:pressure"
    world.entities[other] = {"id": other, "type": "DeviceMeasurement"}
    code, out = run(world, ["--tenant", "acme", "--cleanup-only", "conf-iota-dm-test01"])
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
