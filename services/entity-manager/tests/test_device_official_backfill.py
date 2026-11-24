"""The backfill of the official Device attributes: what it writes, what it refuses, when it stops.

Orion is a fake at HTTP level (`tests/fake_broker.py`): the real requests, the real headers and the
real SDK client run against it.
"""
import copy
import importlib.util
import json
import logging
import os
import subprocess
import sys
import textwrap

import pytest
import requests

from tests.fake_broker import DEFAULT, DEVICE_IRI, NEW_TERMS, FakeBroker, prop, rel, without
from tests.telemetry_fakes import TENANT as REGISTRATION_TENANT
from tests.telemetry_fakes import PARCEL, FakeState, client, fake_db, post_json, seed_datalogger, wired

import dataloggers  # noqa: E402
import device_models  # noqa: E402
import device_official_backfill as backfill  # noqa: E402
from blueprints.measurements import build_measurements  # noqa: E402

TENANT = "acmefarm"
CONTEXT = "http://context.test/ngsi-ld-context.json"
T0 = "2026-03-01T08:00:00.000Z"
AIR = {"measurements": [
    {"type": "t", "incomingKey": "r1", "sdmAttribute": "airTemperature", "unit": "°C"},
    {"type": "hum", "sdmAttribute": "relativeHumidity", "unit": "%"}]}
UNMAPPED = {"measurements": [{"type": "d", "sdmAttribute": "stemDiameter", "unit": "mm"}]}
OFFICIAL = ["controlledProperty", "dateInstalled", "deviceCategory", "deviceState", "refDeviceModel"]


def urn(external_id, tenant=TENANT, type_="Device"):
    return f"urn:ngsi-ld:{type_}:{tenant}:{external_id}"


def model(code, tenant=TENANT):
    return f"urn:ngsi-ld:DeviceModel:{tenant}:{code}"


def legacy(external_id="S1", code="p", **attrs):
    """A Device as registered before the official attributes existed."""
    out = {"id": urn(external_id), "type": "Device", "name": prop("Sensor 1"),
           "externalId": prop(external_id), "profileCode": prop(code), "sensorType": prop(code),
           "installedAt": prop("2026-01-01T00:00:00Z"), "status": prop("active"),
           "airTemperature": prop(21.5, observedAt=T0)}
    out.update(attrs)
    return {name: attr for name, attr in out.items() if attr is not None}


def level(external_id="S1", code="p", **attrs):
    """The same Device with everything a new registration writes."""
    return legacy(external_id, code, **{
        "refDeviceModel": rel(model(code)), "deviceCategory": prop(["sensor"]),
        "controlledProperty": prop(["humidity", "temperature"]),
        "dateInstalled": prop("2026-01-01T00:00:00Z"), "deviceState": prop("active"), **attrs})


def profile(mapping=None, **over):
    return {"id": 1, "name": "Profile", "sdm_entity_type": "Device", "sdm_device_category": None,
            "mapping": AIR if mapping is None else mapping, "manufacturer_name": None,
            "brand_name": None, "model_name": None, **over}


def _real(name):
    """The real `common` module: the suite may have replaced it with a MagicMock."""
    path = os.path.join(os.path.dirname(__file__), "..", "..", "common", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_real_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def state(monkeypatch):
    st = FakeState()
    st.profiles["p"] = profile()
    monkeypatch.setattr(dataloggers, "get_db_connection_with_tenant", fake_db(st))
    return st


@pytest.fixture
def broker(monkeypatch, state):
    monkeypatch.setenv("CONTEXT_URL", CONTEXT)
    monkeypatch.setattr(device_models, "CONTEXT_URL", CONTEXT)
    monkeypatch.setattr(device_models, "ORION_URL", "http://orion.test:1026")
    monkeypatch.setattr(device_models, "inject_fiware_headers",
                        _real("ngsi_headers").inject_fiware_headers)
    monkeypatch.setattr(backfill, "validate_tenant_id", _real("tenant_utils").validate_tenant_id)
    return FakeBroker(TENANT).install(monkeypatch)


class Sleeps(list):
    def __call__(self, seconds):
        self.append(seconds)


def run(apply=True, device=None, tenant=TENANT, sleep=None):
    return backfill.run(tenant, device=device, apply=apply, sleep=Sleeps() if sleep is None else sleep)


def outcomes(summary):
    return {d["urn"]: d["outcome"] for d in summary["devices"]}


def report(summary, device_urn=None):
    return next(d for d in summary["devices"] if d["urn"] == (device_urn or urn("S1")))


def attr_posts(broker, device_urn=None):
    return [body for method, subject, body in broker.bodies
            if method == "POST" and subject == (device_urn or urn("S1"))]


# ---- dry run, apply, second apply --------------------------------------------

def test_a_dry_run_writes_nothing_and_names_what_it_would_write(broker):
    broker.seed(legacy())
    summary, code = run(apply=False)
    assert code == 0
    assert broker.writes == [] and {method for method, _, _ in broker.log} == {"GET"}
    assert summary == {
        "tenant": TENANT, "mode": "dry-run",
        "counts": {"devices": 1, "planned": 1, "written": 0, "unchanged": 0, "refused": 0,
                   "ignored": 0, "failed": 0, "not_attempted": 0},
        "device_models": [{"id": model("p"), "outcome": "planned"}],
        "devices": [{"urn": urn("S1"), "outcome": "planned", "profile": "p", "attributes": OFFICIAL}]}


def test_apply_writes_the_model_first_then_one_post_per_device_and_keeps_the_old_attributes(broker):
    broker.seed(legacy())
    before = broker.view(urn("S1"))
    summary, code = run()
    assert code == 0
    assert broker.writes == [("UPSERT", model("p")), ("POST", urn("S1"))]
    assert attr_posts(broker) == [{
        "refDeviceModel": rel(model("p")), "deviceCategory": prop(["sensor"]),
        "controlledProperty": prop(["humidity", "temperature"]),
        "dateInstalled": prop("2026-01-01T00:00:00Z"), "deviceState": prop("active")}]
    after = broker.view(urn("S1"))
    assert {name: after[name] for name in before} == before
    assert sorted(set(after) - set(before)) == OFFICIAL
    assert broker.view(model("p"))["modelName"] == prop("p")
    assert summary["mode"] == "apply"
    assert summary["counts"] == {"devices": 1, "planned": 0, "written": 1, "unchanged": 0, "refused": 0,
                                 "ignored": 0, "failed": 0, "not_attempted": 0}
    assert summary["device_models"] == [{"id": model("p"), "outcome": "written"}]
    assert summary["devices"] == [{"urn": urn("S1"), "outcome": "written", "profile": "p",
                                   "attributes": OFFICIAL}]


def test_a_second_apply_writes_nothing(broker):
    for external_id in ("S1", "S2"):
        broker.seed(legacy(external_id))
    run()
    broker.bodies.clear()
    sleeps = Sleeps()
    summary, code = run(sleep=sleeps)
    assert code == 0 and broker.writes == [] and sleeps == []
    assert outcomes(summary) == {urn("S1"): "unchanged", urn("S2"): "unchanged"}
    assert summary["device_models"] == [{"id": model("p"), "outcome": "unchanged"}]


def test_only_the_missing_or_different_attributes_are_written(broker):
    broker.seed(level(deviceCategory=prop(["actuator"]), dateInstalled=None))
    run()
    assert attr_posts(broker) == [{"deviceCategory": prop(["sensor"]),
                                   "dateInstalled": prop("2026-01-01T00:00:00Z")}]


def test_a_missing_model_of_a_level_device_is_written_and_the_device_is_not(broker):
    broker.seed(level())
    summary, code = run()
    assert code == 0 and broker.writes == [("UPSERT", model("p"))]
    assert outcomes(summary) == {urn("S1"): "unchanged"}


@pytest.mark.parametrize("change", [
    {"modelName": prop("old")}, {"name": prop("old")}, {"category": prop(["meter"])},
    {"controlledProperty": prop(["temperature"])}], ids=lambda c: next(iter(c)))
def test_an_outdated_model_is_brought_up_to_date(broker, change):
    broker.seed(level())
    run()
    current = broker.view(model("p"))
    broker.seed({**current, **change})
    broker.bodies.clear()
    run()
    assert broker.writes == [("UPSERT", model("p"))] and broker.view(model("p")) == current


def test_a_model_that_lost_its_controlled_property_is_corrected_once(broker, state):
    state.profiles["p"] = profile(UNMAPPED)
    broker.seed(level(controlledProperty=None))
    run()
    broker.seed({**broker.view(model("p")), "controlledProperty": prop(["temperature"])})
    broker.bodies.clear()
    run()
    assert broker.writes == [("UPSERT", model("p")), ("DELETE", model("p"))]
    assert "controlledProperty" not in broker.view(model("p"))
    broker.bodies.clear()
    run()
    assert broker.writes == []


def test_each_profile_in_use_gets_its_model_before_any_device_write(broker, state):
    state.profiles["q"] = profile(UNMAPPED)
    broker.seed(legacy("S1", "q"))
    broker.seed(legacy("S2", "p"))
    run()
    assert broker.writes == [("UPSERT", model("p")), ("UPSERT", model("q")), ("DELETE", model("q")),
                             ("POST", urn("S1")), ("POST", urn("S2"))]


def test_a_backfilled_device_equals_a_newly_registered_one(monkeypatch, broker):
    st = FakeState()
    dl = seed_datalogger(st)
    st.profiles["p"] = profile(manufacturer_name="Acme Instruments")
    with wired(st) as w:
        answer = post_json(client(), "/api/sensors/register", {
            "external_id": "S1", "name": "Sensor 1", "profile": "p",
            "location": {"lat": 42.0, "lon": -2.0}, "controlledAsset": [PARCEL],
            "datalogger_id": dl, "validFrom": "2026-03-01T08:00:00+00:00"})
        assert answer.status_code == 201, answer.get_json()
        registered = w.orion.entities[urn("S1", REGISTRATION_TENANT)]
        registered_model = w.orion.device_models[model("p", REGISTRATION_TENANT)]
    monkeypatch.setattr(dataloggers, "get_db_connection_with_tenant", fake_db(st))
    broker.tenant = REGISTRATION_TENANT
    broker.seed(registered)
    new = broker.view(registered["id"])
    broker.seed({k: v for k, v in registered.items() if k not in OFFICIAL})
    _, code = run(tenant=REGISTRATION_TENANT)
    assert code == 0 and broker.view(registered["id"]) == new
    expected = FakeBroker(REGISTRATION_TENANT)
    expected.seed(registered_model)
    assert broker.view(registered_model["id"]) == expected.view(registered_model["id"])


def test_the_measurements_of_a_backfilled_device_are_the_ones_from_before(broker, state):
    state.profiles["p"] = profile({"measurements": AIR["measurements"] + [
        {"type": "w", "sdmAttribute": "windSpeed", "unit": "m/s", "statistics": ["mean", "max"]}]})
    broker.seed(legacy(relativeHumidity=prop(40, observedAt="2026-03-01T08:05:00.000Z"),
                       windSpeed=prop(2.5, observedAt=T0), windSpeedMax=prop(4, observedAt=T0)))
    before = build_measurements(broker.view(urn("S1")), state.profiles["p"])
    _, code = run()
    assert code == 0
    after = build_measurements(broker.view(urn("S1")), state.profiles["p"])
    key = lambda m: m["id"]  # noqa: E731
    assert sorted(after, key=key) == sorted(before, key=key)
    assert len(before) == 4
    assert {m["dateObserved"]["value"] for m in after} == {T0, "2026-03-01T08:05:00.000Z"}
    assert {m["timeSource"]["value"] for m in after} == {"device"}


# ---- dateInstalled, deviceState, controlledProperty ----------------------------

@pytest.mark.parametrize("attrs,absent", [
    ({"installedAt": None}, "dateInstalled"), ({"installedAt": prop("")}, "dateInstalled"),
    ({"installedAt": prop("  ")}, "dateInstalled"), ({"installedAt": prop(5)}, "dateInstalled"),
    ({"installedAt": prop({"@type": "DateTime", "@value": T0})}, "dateInstalled"),
    ({"installedAt": rel("urn:x")}, "dateInstalled"),
    ({"installedAt": {"type": "VocabProperty", "value": "x"}}, "dateInstalled"),
    ({"status": None}, "deviceState"), ({"status": prop("")}, "deviceState"),
    ({"status": prop(True)}, "deviceState"), ({"status": prop(["a", "b"])}, "deviceState")])
def test_an_old_attribute_that_is_not_text_gives_no_new_one(broker, attrs, absent):
    broker.seed(legacy(**attrs))
    summary, code = run()
    assert code == 0
    assert report(summary)["attributes"] == [a for a in OFFICIAL if a != absent]
    assert absent not in broker.view(urn("S1"))


def test_a_date_installed_and_a_device_state_that_exist_are_kept(broker):
    broker.seed(legacy(dateInstalled=prop("2025-01-01T00:00:00Z"), deviceState=prop("broken")))
    summary, _ = run()
    assert report(summary)["attributes"] == ["controlledProperty", "deviceCategory", "refDeviceModel"]
    after = broker.view(urn("S1"))
    assert (after["dateInstalled"], after["deviceState"]) == (prop("2025-01-01T00:00:00Z"), prop("broken"))


def test_only_the_value_is_copied(broker):
    broker.seed(legacy(status=prop("active", observedAt=T0)))
    run()
    assert attr_posts(broker)[0]["deviceState"] == prop("active")


STATUS_IS_A_READING = [
    {"measurements": [{"type": "st", "sdmAttribute": "status", "unit": ""}]},
    {"measurements": [{"type": "st", "sdmAttribute": "status", "unit": "", "statistics": ["last", "max"]}]}]


@pytest.mark.parametrize("mapping", STATUS_IS_A_READING, ids=["plain", "statistics"])
def test_a_status_that_is_a_reading_gives_no_device_state_and_a_note(broker, state, mapping):
    state.profiles["p"] = profile(mapping)
    broker.seed(legacy(status=prop("running", observedAt=T0)))
    summary, code = run()
    assert code == 0
    assert report(summary) == {
        "urn": urn("S1"), "outcome": "written", "profile": "p",
        "attributes": ["dateInstalled", "deviceCategory", "refDeviceModel"],
        "notes": ["status_is_a_reading"]}
    assert "deviceState" not in broker.view(urn("S1"))


def test_a_derived_status_channel_is_not_the_status(broker, state):
    state.profiles["p"] = profile({"measurements": [
        {"type": "st", "sdmAttribute": "status", "unit": "", "statistics": ["max"]}]})
    broker.seed(legacy())
    summary, _ = run()
    assert "deviceState" in report(summary)["attributes"] and "notes" not in report(summary)


def test_a_profile_that_maps_to_nothing_omits_controlled_property_and_removes_a_stale_one(broker, state):
    state.profiles["p"] = profile(UNMAPPED)
    broker.seed(legacy("S1"))
    broker.seed(legacy("S2", controlledProperty=prop(["temperature"])))
    summary, code = run()
    assert code == 0
    assert "controlledProperty" not in report(summary)["attributes"]
    assert "deleted" not in report(summary)
    assert report(summary, urn("S2"))["deleted"] == ["controlledProperty"]
    for external_id in ("S1", "S2"):
        assert "controlledProperty" not in broker.view(urn(external_id))
    broker.bodies.clear()
    run()
    assert broker.writes == []


def test_a_device_with_only_a_stale_attribute_gets_one_delete_and_no_post(broker, state):
    state.profiles["p"] = profile(UNMAPPED)
    broker.seed(level(controlledProperty=None))
    run()
    broker.seed({**broker.view(urn("S1")), "controlledProperty": prop(["temperature"])})
    broker.bodies.clear()
    summary, code = run()
    assert code == 0 and broker.writes == [("DELETE", urn("S1"))]
    assert report(summary) == {"urn": urn("S1"), "outcome": "written", "profile": "p",
                               "deleted": ["controlledProperty"]}


# ---- refusals ----------------------------------------------------------------

def _undated(*names):
    return {"reason": "undated_readings", "readings": list(names)}


REFUSALS = {
    "unknown-profile": (legacy(code="gone"), {}, {"reason": "profile_unknown"}),
    "id-without-tenant": ({**legacy(), "id": "urn:ngsi-ld:Device:S1"}, {}, {"reason": "id_not_parseable"}),
    "id-of-another-tenant": ({**legacy(), "id": urn("S1", "otherfarm")}, {}, {"reason": "id_not_parseable"}),
    "id-of-a-machine": ({**legacy(), "id": urn("S1", type_="ManufacturingMachine")}, {},
                        {"reason": "id_not_parseable"}),
    "undated": (legacy(airTemperature=prop(1)), {}, _undated("airTemperature")),
    "undated-alias": (legacy(airTemperature=None, temperature=prop(1)), {}, _undated("airTemperature")),
    "undated-alias-in-the-profile": (legacy(airTemperature=prop(1)), {"p": profile({"measurements": [
        {"type": "t", "sdmAttribute": "temperature", "unit": "°C"}]})}, _undated("airTemperature")),
    "undated-statistic": (legacy(windSpeedMax=prop(1)), {"p": profile({"measurements": [
        {"type": "w", "sdmAttribute": "windSpeed", "unit": "m/s", "statistics": ["mean", "max"]}]})},
        _undated("windSpeedMax")),
    "undated-but-level": (level(airTemperature=prop(1)), {}, _undated("airTemperature")),
    "codes-disagree": (legacy(refDeviceModel=rel(model("q"))), {"q": profile()},
                       {"reason": "profile_codes_disagree",
                        "codes": {"refDeviceModel": "q", "profileCode": "p"}}),
    "foreign-model": (legacy(refDeviceModel=rel(model("p", "otherfarm"))), {},
                      {"reason": "ref_device_model_unusable"}),
    "malformed-model": (legacy(refDeviceModel=rel("urn:x")), {}, {"reason": "ref_device_model_unusable"}),
    "foreign-model-only": (legacy(profileCode=None, refDeviceModel=rel(model("p", "otherfarm"))), {},
                           {"reason": "ref_device_model_unusable"}),
    "machine-profile": (legacy(), {"p": profile(sdm_entity_type="ManufacturingMachine")},
                        {"reason": "profile_not_of_a_device"}),
    "code-with-a-space": (legacy(code="bad code"), {"bad code": profile()},
                          {"reason": "profile_code_unusable"}),
    "empty-code": (legacy(profileCode=prop("")), {}, {"reason": "profile_code_unusable"}),
    "numeric-code": (legacy(profileCode=prop(7)), {}, {"reason": "profile_code_unusable"}),
    "two-types": ({**legacy(), "type": ["Device", "Other"]}, {}, {"reason": "type_not_device"}),
    "broken-profile": (legacy(), {"p": profile({"measurements": [{"type": "x", "statistics": ["max"]}]})},
                       {"reason": "profile_malformed"}),
    "attribute-is-a-reading": (legacy(), {"p": profile({"measurements": [
        {"type": "c", "sdmAttribute": "deviceCategory", "unit": ""}]})},
        {"reason": "official_attribute_is_a_reading", "readings": ["deviceCategory"]}),
    "stale-attribute-is-a-reading": (
        legacy(controlledProperty=prop("x", observedAt=T0)),
        {"p": profile({"measurements": [{"type": "c", "sdmAttribute": "controlledProperty", "unit": ""}]})},
        {"reason": "official_attribute_is_a_reading", "readings": ["controlledProperty"]}),
}


@pytest.mark.parametrize("entity,profiles,expected", REFUSALS.values(), ids=REFUSALS.keys())
def test_a_refused_device_is_listed_with_its_reason_and_not_written(broker, state, entity, profiles, expected):
    state.profiles["ok"] = profile()
    state.profiles.update(profiles)
    broker.seed(entity)
    broker.seed(legacy("S9", "ok"))
    before = broker.view(entity["id"])

    summary, code = run(apply=False)
    assert code == 0 and summary["counts"]["refused"] == 1 and broker.writes == []

    summary, code = run()
    assert code == 1
    found = report(summary, entity["id"])
    assert found["outcome"] == "refused"
    assert {k: v for k, v in found.items() if k not in ("urn", "outcome", "profile")} == expected
    assert broker.view(entity["id"]) == before
    assert outcomes(summary)[urn("S9")] == "written"
    assert broker.writes == [("UPSERT", model("ok")), ("POST", urn("S9"))]


@pytest.mark.parametrize("entity,profiles", [
    (legacy(airTemperature=prop(1), observedAt=prop(T0)), {}),
    (legacy(airTemperature=None), {}),
    (legacy(soilMoisture=prop(1)), {}),
    (legacy(), {"p": profile(sdm_entity_type=None)}),
    (legacy(profileCode=None, refDeviceModel=rel(model("p"))), {}),
    (legacy(profileCode=prop(""), refDeviceModel=rel(model("p"))), {}),
    (legacy(refDeviceModel=rel(model("p"))), {}),
], ids=["entity-time", "no-reading", "undeclared-undated", "profile-type-null", "model-only",
        "model-and-empty-code", "codes-agree"])
def test_what_is_not_a_reason_to_refuse(broker, state, entity, profiles):
    state.profiles.update(profiles)
    broker.seed(entity)
    summary, code = run()
    assert code == 0 and outcomes(summary) == {urn("S1"): "written"}


@pytest.mark.parametrize("term", ["refDeviceModel", "dateInstalled", "deviceCategory", "deviceState",
                                  "controlledProperty"])
def test_an_official_attribute_under_the_default_vocabulary_is_refused(broker, term):
    """Written before the context had the term: a read with the Link header hides it."""
    entity = level()
    broker.seed(entity)
    stored = broker.entities[urn("S1")]["attrs"]
    stored[DEFAULT + term] = stored.pop(DEVICE_IRI + term)
    assert broker.view(urn("S1"))[term] == _bare(entity[term])
    for apply in (False, True):
        summary, code = run(apply=apply)
        assert code == int(apply) and broker.writes == []
        assert report(summary) == {
            "urn": urn("S1"), "outcome": "refused", "profile": "p",
            "reason": "attributes_outside_the_official_context", "attributes_outside": [term]}


def _bare(attr):
    value = attr.get("value")
    return prop(value[0]) if isinstance(value, list) and len(value) == 1 else attr


def test_every_device_is_assessed_before_the_first_write(broker):
    for n in range(1, 4):
        broker.seed(legacy(f"S{n}"))
    run()
    first_write = next(i for i, entry in enumerate(broker.log) if entry[0] != "GET")
    for n in range(1, 4):
        assert broker.log.index(("GET", urn(f"S{n}"), False)) < first_write


def test_a_device_with_no_profile_at_all_is_ignored(broker):
    twin = {k: v for k, v in legacy("T1").items() if k not in ("profileCode", "sensorType")}
    broker.seed(twin)
    broker.seed(legacy())
    summary, code = run()
    assert code == 0
    assert report(summary, urn("T1")) == {"urn": urn("T1"), "outcome": "ignored", "reason": "no_profile"}
    assert summary["counts"]["ignored"] == 1 and attr_posts(broker, urn("T1")) == []


def test_only_twins_is_not_an_error(broker):
    broker.seed(legacy(profileCode=None))
    summary, code = run()
    assert code == 0 and outcomes(summary) == {urn("S1"): "ignored"} and broker.writes == []


def test_machines_are_neither_listed_nor_written(broker):
    machine = {**legacy("M1"), "id": urn("M1", type_="ManufacturingMachine"), "type": "ManufacturingMachine"}
    broker.seed(machine)
    broker.seed(legacy())
    summary, code = run()
    assert code == 0 and outcomes(summary) == {urn("S1"): "written"}
    assert not [entry for entry in broker.log if machine["id"] in entry[1]]


# ---- selection ---------------------------------------------------------------

def test_devices_are_done_in_the_order_of_their_ids(broker):
    for n in (3, 1, 2):
        broker.seed(legacy(f"S{n}"))
    summary, _ = run()
    expected = [urn(f"S{n}") for n in (1, 2, 3)]
    assert [d["urn"] for d in summary["devices"]] == expected
    assert [subject for method, subject in broker.writes if method == "POST"] == expected


def test_device_restricts_the_run_to_one(broker):
    broker.seed(legacy("S1"))
    broker.seed(legacy("S2"))
    summary, code = run(device=urn("S2"))
    assert code == 0 and outcomes(summary) == {urn("S2"): "written"}
    assert ("GET", "entities", True) not in broker.log and attr_posts(broker, urn("S1")) == []


@pytest.mark.parametrize("wanted", [
    urn("M1", type_="ManufacturingMachine"), urn("S1", "otherfarm"), "urn:ngsi-ld:Device:S1",
    urn("S1") + "/attrs", urn("S1") + "?x=1", "", "S1"])
@pytest.mark.parametrize("apply", [False, True])
def test_a_urn_that_is_no_device_of_the_tenant_is_not_even_read(broker, wanted, apply):
    broker.seed(legacy())
    broker.seed({**legacy("M1"), "id": urn("M1", type_="ManufacturingMachine"),
                 "type": "ManufacturingMachine"})
    summary, code = run(device=wanted, apply=apply)
    assert code == 1 and summary["error"]["code"] == "not_a_device_of_the_tenant"
    assert summary["devices"] == [] and broker.log == []


@pytest.mark.parametrize("apply", [False, True])
def test_a_device_that_does_not_exist_is_an_error(broker, apply):
    broker.seed(legacy())
    summary, code = run(device=urn("S7"), apply=apply)
    assert code == 1 and summary["error"] == {"code": "device_not_found", "entity": urn("S7")}
    assert broker.writes == []


@pytest.mark.parametrize("type_", ["AgriSensor", ["Device", "Other"]])
def test_a_device_urn_of_an_entity_of_another_type_is_an_error(broker, type_):
    broker.seed({**legacy(), "type": type_})
    summary, code = run(device=urn("S1"))
    assert code == 1 and summary["error"] == {"code": "not_a_device_of_the_tenant", "entity": urn("S1")}
    assert broker.writes == []


@pytest.mark.parametrize("apply", [False, True])
def test_zero_devices_is_an_error(broker, apply):
    broker.seed({**legacy(), "type": "AgriSensor"})
    summary, code = run(apply=apply)
    assert code == 1 and summary["error"] == {"code": "no_devices"} and broker.writes == []


def test_every_page_of_the_listing_is_read(broker, monkeypatch):
    monkeypatch.setattr(device_models, "PAGE_SIZE", 2)
    for n in range(1, 6):
        broker.seed(level(f"S{n}"))
    summary, code = run(apply=False)
    assert code == 0 and len(summary["devices"]) == 5
    assert broker.log.count(("GET", "entities", True)) == 3


def _listing_answer(*answers):
    answers = list(answers)

    def _hook(method, subject, link):
        if subject == "entities":
            return answers.pop(0) if len(answers) > 1 else answers[0]
    return _hook


PAGE = [{"id": urn("S1"), "type": "Device", "profileCode": prop("p")}]
BAD_LISTINGS = {
    "500": (500, PAGE, {"NGSILD-Results-Count": "1"}),
    "404": (404, PAGE, {"NGSILD-Results-Count": "1"}),
    "network": requests.exceptions.ConnectionError("down"),
    "not-json": (200,),
    "not-a-list": (200, {"id": "x"}, {"NGSILD-Results-Count": "1"}),
    "not-entities": (200, ["x"], {"NGSILD-Results-Count": "1"}),
    "no-count": (200, PAGE, {}),
    "bad-count": (200, PAGE, {"NGSILD-Results-Count": "many"}),
    "fewer-than-counted": (200, PAGE, {"NGSILD-Results-Count": "2"}),
    "more-than-counted": (200, PAGE + [{**PAGE[0], "id": urn("S2")}], {"NGSILD-Results-Count": "1"}),
    "false-zero": (200, [], {"NGSILD-Results-Count": "3"}),
}


@pytest.mark.parametrize("answer", BAD_LISTINGS.values(), ids=BAD_LISTINGS.keys())
@pytest.mark.parametrize("apply", [False, True])
def test_a_listing_that_cannot_be_trusted_is_an_error_and_nothing_is_written(broker, answer, apply):
    broker.seed(legacy())
    broker.hooks.append(_listing_answer(answer))
    summary, code = run(apply=apply)
    assert code == 1 and summary["error"] == {"code": "listing_failed"} and broker.writes == []


def test_a_broker_that_does_not_page_is_an_error(broker, monkeypatch):
    monkeypatch.setattr(device_models, "PAGE_SIZE", 1)
    broker.hooks.append(_listing_answer((200, PAGE, {"NGSILD-Results-Count": "2"})))
    summary, code = run()
    assert code == 1 and summary["error"] == {"code": "listing_failed"}


def test_a_second_page_that_fails_is_an_error(broker, monkeypatch):
    monkeypatch.setattr(device_models, "PAGE_SIZE", 1)
    broker.seed(legacy("S1"))
    broker.seed(legacy("S2"))
    broker.hooks.append(_listing_answer(None, (500,)))
    summary, code = run()
    assert code == 1 and summary["error"] == {"code": "listing_failed"} and broker.writes == []


@pytest.mark.parametrize("apply", [False, True])
def test_a_profile_that_cannot_be_read_is_an_error_and_nothing_is_written(broker, state, monkeypatch, apply):
    state.profiles["q"] = profile()
    broker.seed(legacy("S1"))
    broker.seed(legacy("S2", "q"))
    read = dataloggers.read_profile

    def _read(tenant, code):
        if code == "q":
            raise dataloggers.ApiError(502, "Database error", "db")
        return read(tenant, code)

    monkeypatch.setattr(dataloggers, "read_profile", _read)
    summary, code = run(apply=apply)
    assert code == 1 and summary["error"] == {"code": "profile_read_failed", "profile": "q"}
    assert broker.writes == []
    assert set(outcomes(summary).values()) == ({"not_attempted"} if apply else {"planned"})


@pytest.mark.parametrize("apply", [False, True])
def test_a_device_that_cannot_be_read_as_stored_is_an_error_and_nothing_is_written(broker, apply):
    broker.seed(legacy())
    broker.hooks.append(lambda m, subject, link: 500 if subject == urn("S1") and not link else None)
    summary, code = run(apply=apply)
    assert code == 1 and summary["error"] == {"code": "device_read_failed", "entity": urn("S1")}
    assert broker.writes == []


def test_an_unexpected_error_still_gives_a_summary(broker, monkeypatch):
    broker.seed(legacy())
    monkeypatch.setattr(device_models, "reading_channels", lambda p: 1 / 0)
    summary, code = run()
    assert code == 1 and summary["error"] == {"code": "unexpected_error", "detail": "ZeroDivisionError"}
    assert broker.writes == []


@pytest.mark.parametrize("value", ["", None])
def test_without_the_context_url_nothing_is_even_read(broker, monkeypatch, value):
    broker.seed(legacy())
    monkeypatch.delenv("CONTEXT_URL")
    if value is not None:
        monkeypatch.setenv("CONTEXT_URL", value)
    summary, code = run()
    assert code == 1 and summary["error"]["code"] == "context_url_not_set" and broker.log == []


# ---- the fresh read ----------------------------------------------------------

def _between_assessment_and_write(broker, change):
    """Run `change` once, when the first Device has been written."""
    def _hook(method, subject, link):
        if method == "POST" and subject.endswith("/attrs"):
            broker.hooks.remove(_hook)
            change()
    broker.hooks.append(_hook)


def _set(broker, device_urn, **attrs):
    def _change():
        broker.seed({**broker.view(device_urn), **attrs})
    return _change


FRESH = {
    "left-the-profile": ({"profileCode": prop("q")}, {"reason": "left_the_profile", "profile": "q"}),
    "lost-the-profile": ({"profileCode": None}, {"reason": "left_the_profile"}),
    "gained-an-undated-reading": ({"relativeHumidity": prop(1)},
                                  {"profile": "p", **_undated("relativeHumidity")}),
    "codes-now-disagree": ({"refDeviceModel": rel(model("q"))},
                           {"reason": "profile_codes_disagree",
                            "codes": {"refDeviceModel": "q", "profileCode": "p"}}),
}


@pytest.mark.parametrize("attrs,expected", FRESH.values(), ids=FRESH.keys())
def test_a_device_that_changed_after_the_listing_is_refused(broker, state, attrs, expected):
    state.profiles["q"] = profile(UNMAPPED)
    for n in (1, 2, 3):
        broker.seed(legacy(f"S{n}"))

    def _change():
        entity = {**broker.view(urn("S2")), **attrs}
        broker.seed({k: v for k, v in entity.items() if v is not None})

    _between_assessment_and_write(broker, _change)
    summary, code = run()
    assert code == 1
    assert report(summary, urn("S2")) == {"urn": urn("S2"), "outcome": "refused", **expected}
    assert attr_posts(broker, urn("S2")) == []
    assert outcomes(summary) == {urn("S1"): "written", urn("S2"): "refused", urn("S3"): "written"}


def test_a_device_deleted_after_the_listing_is_refused(broker):
    for n in (1, 2):
        broker.seed(legacy(f"S{n}"))
    _between_assessment_and_write(broker, lambda: broker.entities.pop(urn("S2")))
    summary, code = run()
    assert code == 1
    assert report(summary, urn("S2")) == {"urn": urn("S2"), "outcome": "refused", "reason": "gone"}


def test_a_device_whose_profile_was_edited_after_the_listing_is_refused(broker, state):
    """Its DeviceModel was written from the profile of before."""
    for n in (1, 2):
        broker.seed(legacy(f"S{n}"))
    _between_assessment_and_write(broker, lambda: state.profiles.update(p=profile(UNMAPPED)))
    summary, code = run()
    assert code == 1 and attr_posts(broker, urn("S2")) == []
    assert report(summary, urn("S2")) == {"urn": urn("S2"), "outcome": "refused", "profile": "p",
                                          "reason": "profile_changed"}


def test_a_device_that_became_level_after_the_listing_is_unchanged(broker):
    for n in (1, 2, 3):
        broker.seed(legacy(f"S{n}"))
    _between_assessment_and_write(broker, _set(broker, urn("S2"), **{
        k: v for k, v in level().items() if k in OFFICIAL}))
    sleeps = Sleeps()
    summary, code = run(sleep=sleeps)
    assert code == 0 and attr_posts(broker, urn("S2")) == []
    assert outcomes(summary) == {urn("S1"): "written", urn("S2"): "unchanged", urn("S3"): "written"}
    assert sleeps == [5]


def test_what_is_written_comes_from_the_fresh_read(broker):
    for n in (1, 2):
        broker.seed(legacy(f"S{n}"))
    _between_assessment_and_write(broker, _set(broker, urn("S2"), dateInstalled=prop("2020-01-01T00:00:00Z"),
                                               status=prop("maintenance")))
    summary, _ = run()
    assert report(summary, urn("S2"))["attributes"] == [a for a in OFFICIAL if a != "dateInstalled"]
    assert attr_posts(broker, urn("S2"))[0]["deviceState"] == prop("maintenance")


# ---- waiting -----------------------------------------------------------------

def test_five_seconds_between_written_devices_and_the_read_comes_after_the_wait(broker):
    for n in (1, 2, 3):
        broker.seed(legacy(f"S{n}"))
    marks = []

    def _sleep(seconds):
        broker.log.append(("SLEEP", seconds, None))
        marks.append(seconds)

    run(sleep=_sleep)
    assert marks == [5, 5]
    for n in (2, 3):
        written = broker.log.index(("POST", urn(f"S{n}") + "/attrs", True))
        assert broker.log[written - 3][0] == "SLEEP"
        assert broker.log[written - 2] == ("GET", urn(f"S{n}"), True)
    assert broker.log[-1][0] != "SLEEP"


def test_no_wait_after_an_unchanged_or_a_refused_device(broker):
    broker.seed(level("S1"))
    broker.seed(legacy("S2", "gone"))
    broker.seed(legacy("S3"))
    broker.seed(level("S4"))
    sleeps = Sleeps()
    run(sleep=sleeps)
    assert sleeps == []


# ---- the context guard -------------------------------------------------------

@pytest.mark.parametrize("missing", [NEW_TERMS, ("DeviceModel",), ("manufacturerName",), ("brandName",),
                                     ("modelName",), ("controlledProperty",), ("name",), ("category",)],
                         ids=lambda m: m[0] if len(m) == 1 else "all")
def test_a_context_without_the_terms_aborts_before_any_device(broker, missing):
    broker.seed(legacy())
    before = copy.deepcopy(broker.entities[urn("S1")])
    broker.context = without(*missing)
    summary, code = run()
    assert code == 1
    error = summary["error"]
    assert (error["code"], error["entity"]) == ("outside_the_official_context", model("p"))
    expected = ["type"] if missing == ("DeviceModel",) else list(missing)
    assert set(error["attributes"]) >= set(expected) & {"type", "manufacturerName", "brandName", "modelName",
                                                        "controlledProperty", "name", "category"}
    assert "delete" in error["detail"]
    assert broker.writes == [("UPSERT", model("p"))]
    assert broker.entities[urn("S1")] == before
    assert outcomes(summary) == {urn("S1"): "not_attempted"}


@pytest.mark.parametrize("term", ["manufacturerName", "brandName", "modelName", "controlledProperty"])
def test_a_model_term_under_another_vocabulary_aborts_before_any_device(broker, term):
    broker.seed(legacy())
    broker.context = {**without(), term: f"https://other.test/{term}"}
    summary, code = run()
    assert code == 1 and summary["error"]["attributes"] == [term] and attr_posts(broker) == []


@pytest.mark.parametrize("term", ["refDeviceModel", "dateInstalled", "deviceCategory"])
def test_a_device_term_under_another_vocabulary_stops_the_run_at_the_first_device(broker, term):
    for n in (1, 2):
        broker.seed(legacy(f"S{n}"))
    broker.context = {**without(), term: f"https://other.test/{term}"}
    summary, code = run()
    assert code == 1 and summary["error"]["attributes"] == [term]
    assert outcomes(summary) == {urn("S1"): "written", urn("S2"): "not_attempted"}


def test_the_guard_runs_after_the_first_model_and_stops_the_others(broker, state):
    state.profiles["q"] = profile()
    broker.seed(legacy("S1"))
    broker.seed(legacy("S2", "q"))
    broker.context = without(*NEW_TERMS)
    run()
    assert broker.writes == [("UPSERT", model("p"))]


def test_the_guard_reads_without_the_link_header_and_everything_else_with_it(broker):
    broker.seed(legacy())
    run()
    guard = [(m, s) for m, s, link in broker.log if not link and m == "GET"]
    assert guard.count(("GET", model("p"))) == 1
    assert set(guard) == {("GET", model("p")), ("GET", urn("S1"))}
    for headers, (method, subject, link) in zip(broker.headers, broker.log, strict=True):
        assert headers["NGSILD-Tenant"] == TENANT
        if link:
            assert headers["Link"].startswith(f"<{CONTEXT}>")
    assert all(link for method, subject, link in broker.log
               if method != "GET" and subject != "entityOperations/upsert")
    assert not [entry for entry in broker.log if entry[0] in ("PATCH", "PUT")]


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("missing", [NEW_TERMS, ("brandName",), ("name",)])
def test_a_model_damaged_by_an_earlier_run_aborts_although_the_context_is_right_now(broker, apply, missing):
    """With the Link header it reads exactly like a right one."""
    broker.seed(level())
    run()
    good = broker.view(model("p"))
    broker.seed(good, context=without(*missing))
    assert broker.view(model("p")) == good
    broker.seed(legacy())
    broker.bodies.clear()
    summary, code = run(apply=apply)
    assert code == 1 and summary["error"]["code"] == "outside_the_official_context"
    assert summary["error"]["entity"] == model("p") and broker.writes == []


def test_the_default_vocabulary_spelled_in_full_is_rejected_too(broker):
    broker.seed(level())
    run()
    stored = broker.view(model("p"), link=False)
    stored[DEFAULT + "name"] = prop("x")
    broker.hooks.append(lambda m, s, link: (200, stored) if s == model("p") and not link else None)
    summary, code = run()
    assert code == 1 and summary["error"]["attributes"] == ["name"]


def test_a_model_that_cannot_be_read_back_aborts_before_any_device(broker):
    broker.seed(legacy())
    broker.hooks.append(lambda m, s, link: 500 if s == model("p") and not link else None)
    summary, code = run()
    assert code == 1 and summary["error"] == {"code": "device_model_failed", "entity": model("p")}
    assert attr_posts(broker) == [] and outcomes(summary) == {urn("S1"): "not_attempted"}


@pytest.mark.parametrize("answer", [500, requests.exceptions.ConnectionError("down"),
                                    (207, {"success": [], "errors": [{"entityId": model("p")}]})],
                         ids=["500", "network", "partial"])
def test_a_model_that_cannot_be_written_aborts_before_any_device(broker, answer):
    broker.seed(legacy())
    broker.hooks.append(lambda m, s, link: answer if s == "entityOperations/upsert" else None)
    summary, code = run()
    assert code == 1 and summary["error"] == {"code": "device_model_failed", "entity": model("p")}
    assert attr_posts(broker) == [] and outcomes(summary) == {urn("S1"): "not_attempted"}


@pytest.mark.parametrize("missing", [("refDeviceModel",), ("dateInstalled",), ("deviceCategory",),
                                     ("deviceState",)])
def test_the_first_written_device_outside_the_context_stops_the_run(broker, missing):
    """The model is right and the Device is not: the broker's context lacks only Device terms."""
    for n in (1, 2, 3):
        broker.seed(legacy(f"S{n}"))
    broker.context = without(*missing)
    sleeps = Sleeps()
    summary, code = run(sleep=sleeps)
    assert code == 1 and sleeps == []
    assert summary["error"]["code"] == "outside_the_official_context"
    assert summary["error"]["entity"] == urn("S1") and summary["error"]["attributes"] == list(missing)
    assert outcomes(summary) == {urn("S1"): "written", urn("S2"): "not_attempted", urn("S3"): "not_attempted"}
    assert broker.writes == [("UPSERT", model("p")), ("POST", urn("S1"))]


def test_a_first_written_device_that_cannot_be_read_back_stops_the_run(broker):
    for n in (1, 2):
        broker.seed(legacy(f"S{n}"))

    def _hook(method, subject, link):
        written = ("POST", urn("S1")) in broker.writes
        return 500 if written and subject == urn("S1") and not link else None

    broker.hooks.append(_hook)
    summary, code = run()
    assert code == 1 and summary["error"] == {"code": "device_read_failed", "entity": urn("S1")}
    assert outcomes(summary) == {urn("S1"): "written", urn("S2"): "not_attempted"}


def test_only_the_first_written_device_is_read_back(broker):
    for n in (1, 2):
        broker.seed(legacy(f"S{n}"))
    run()
    bare = [(m, s) for m, s, link in broker.log if not link and m == "GET"]
    assert bare.count(("GET", urn("S1"))) == 3  # assessed, reassessed, read back
    assert bare.count(("GET", urn("S2"))) == 2


# ---- failures ----------------------------------------------------------------

@pytest.mark.parametrize("answer", [500, 207, 404, requests.exceptions.ConnectionError("down")])
def test_a_failed_write_stops_the_run_and_a_second_run_finishes_it(broker, answer, caplog):
    for n in (1, 2, 3, 4):
        broker.seed(legacy(f"S{n}"))
    broker.seed(level("S5"))
    hook = lambda m, s, link: answer if s == urn("S2") + "/attrs" else None  # noqa: E731
    broker.hooks.append(hook)
    with caplog.at_level(logging.INFO):
        summary, code = run()
    assert code == 1
    assert outcomes(summary) == {urn("S1"): "written", urn("S2"): "failed", urn("S3"): "not_attempted",
                                 urn("S4"): "not_attempted", urn("S5"): "unchanged"}
    assert report(summary, urn("S2"))["reason"] == "write_failed"
    assert [r for r in caplog.records if r.levelno == logging.ERROR and urn("S2") in r.getMessage()]
    assert not [e for e in broker.log if e[0] == "POST" and e[1].startswith((urn("S3"), urn("S4")))]

    broker.hooks.clear()
    summary, code = run()
    assert code == 0
    assert set(outcomes(summary).values()) == {"written", "unchanged"}
    assert outcomes(summary)[urn("S1")] == "unchanged"


def test_a_failed_fresh_read_stops_the_run_and_nothing_is_written_blind(broker):
    for n in (1, 2, 3):
        broker.seed(legacy(f"S{n}"))
    broker.hooks.append(lambda m, s, link: 500 if s == urn("S2") and link else None)
    summary, code = run()
    assert code == 1
    assert outcomes(summary) == {urn("S1"): "written", urn("S2"): "failed", urn("S3"): "not_attempted"}
    assert attr_posts(broker, urn("S2")) == []


def test_a_profile_that_cannot_be_read_again_stops_the_run(broker, monkeypatch):
    for n in (1, 2):
        broker.seed(legacy(f"S{n}"))
    read = dataloggers.read_profile

    def _read(tenant, code):
        if broker.writes:
            raise dataloggers.ApiError(502, "Database error", "db")
        return read(tenant, code)

    monkeypatch.setattr(dataloggers, "read_profile", _read)
    summary, code = run()
    assert code == 1 and attr_posts(broker) == []
    assert outcomes(summary) == {urn("S1"): "failed", urn("S2"): "not_attempted"}


def test_a_stale_attribute_that_cannot_be_deleted_is_a_failure(broker, state):
    state.profiles["p"] = profile(UNMAPPED)
    broker.seed(legacy("S1", controlledProperty=prop(["temperature"])))
    broker.seed(legacy("S2"))
    broker.hooks.append(lambda m, s, link: 500 if m == "DELETE" and s.startswith(urn("S1")) else None)
    summary, code = run()
    assert code == 1 and outcomes(summary) == {urn("S1"): "failed", urn("S2"): "not_attempted"}


def test_every_write_is_logged_with_its_ids(broker, caplog):
    broker.seed(legacy())
    with caplog.at_level(logging.INFO):
        run()
    assert [r for r in caplog.records if f"tenant={TENANT}" in r.getMessage()
            and f"device={urn('S1')}" in r.getMessage() and "refDeviceModel" in r.getMessage()]


# ---- command line ------------------------------------------------------------

def test_main_prints_the_summary_alone_on_stdout_and_is_a_dry_run_by_default(broker, capsys):
    broker.seed(legacy())
    assert backfill.main(["--tenant", TENANT]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["mode"] == "dry-run" and printed["counts"]["planned"] == 1 and broker.writes == []


def test_main_applies_with_the_flag_and_returns_the_exit_code(broker, capsys):
    broker.seed(legacy())
    broker.seed(legacy("S2", "gone"))
    assert backfill.main(["--tenant", TENANT, "--device", urn("S1"), "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["written"] == 1
    assert backfill.main(["--tenant", TENANT, "--apply"]) == 1
    assert json.loads(capsys.readouterr().out)["counts"]["refused"] == 1


def test_the_summary_carries_no_value_of_an_attribute(broker, capsys):
    broker.seed(legacy(status=prop("secretstate"), installedAt=prop("2026-01-01T00:00:00Z"),
                       name=prop("Secret name"), airTemperature=prop(987654)))
    backfill.main(["--tenant", TENANT])
    out = capsys.readouterr().out
    for value in ("secretstate", "2026-01-01", "Secret name", "987654"):
        assert value not in out


@pytest.mark.parametrize("argv", [[], ["--tenant"], ["--tenant", ""], ["--tenant", "Acme Farm"],
                                  ["--tenant", "acme_farm"], ["--tenant", TENANT, "--force"],
                                  ["--tenant", TENANT, "--device"]])
def test_bad_arguments_exit_2_and_touch_nothing(broker, capsys, argv):
    with pytest.raises(SystemExit) as err:
        backfill.main(argv)
    assert err.value.code == 2 and capsys.readouterr().out == "" and broker.log == []


SERVICES = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def image(tmp_path):
    """The layout of the entity-manager image: the service in /app, `common` inside it."""
    app = tmp_path / "app"
    app.mkdir()
    for name in os.listdir(os.path.join(SERVICES, "entity-manager")):
        if name not in ("tests", "__pycache__", "common"):
            (app / name).symlink_to(os.path.join(SERVICES, "entity-manager", name))
    (app / "common").symlink_to(os.path.join(SERVICES, "common"))
    return app


def _python(app, *args):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "CONTEXT_URL", "POSTGRES_URL", "ORION_URL")}
    return subprocess.run([sys.executable, *args], cwd=app, env=env, capture_output=True, text=True,
                          timeout=60)


def test_it_runs_as_a_module_in_the_image_and_stdout_is_one_json_document(image):
    done = _python(image, "-m", "device_official_backfill", "--tenant", TENANT)
    assert done.returncode == 1, done.stderr
    assert json.loads(done.stdout)["error"]["code"] == "context_url_not_set"
    assert f"tenant={TENANT}" in done.stderr


def test_importing_it_has_no_side_effects(image):
    script = textwrap.dedent("""
        import logging, socket, sys, threading
        def _refuse(*a, **kw):
            raise AssertionError("network")
        socket.socket.connect = socket.socket.connect_ex = socket.socket.bind = _refuse
        handlers = list(logging.getLogger().handlers)
        import device_official_backfill
        assert threading.active_count() == 1, threading.enumerate()
        assert logging.getLogger().handlers == handlers
        served = [m for m in ("entity_management_api", "subscription_manager", "notification_handler",
                              "blueprints", "blueprints.notifications") if m in sys.modules]
        assert not served, served
    """)
    done = _python(image, "-c", script)
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""
