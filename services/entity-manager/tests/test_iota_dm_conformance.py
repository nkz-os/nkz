"""Comparison and classification logic of the IoT Agent conformance script (no network)."""
import json
import pathlib
import sys

import pytest

from tests.telemetry_fakes import TENANT  # noqa: F401,I001  (stubs first)

import dm_writer_cutover  # noqa: E402,F401

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import iota_dm_conformance as conf  # noqa: E402

DM = "urn:ngsi-ld:DeviceMeasurement:acme:conf-iota-dm-aa:p"
DM2 = "urn:ngsi-ld:DeviceMeasurement:acme:conf-iota-dm-aa:hum"
DEVICE = "urn:ngsi-ld:Device:acme:conf-iota-dm-aa"
T0 = "2026-01-01T10:00:00Z"


def prop(value, **extra):
    return {"type": "Property", "value": value, **extra}


def dm(dm_id=DM, num=1.5, **over):
    ent = {
        "id": dm_id, "type": "DeviceMeasurement",
        "refDevice": {"type": "Relationship", "object": DEVICE},
        "numValue": prop(num, unitCode="A97"),
        "dateObserved": prop(T0), "timeSource": prop("device"),
        "controlledProperty": prop("pressure"), "measurementType": prop("p"),
        "deviceType": prop("conf"),
    }
    ent.update(over)
    return ent


def with_observed(ent, at=T0):
    out = json.loads(json.dumps(ent))
    for k, v in out.items():
        if isinstance(v, dict) and k not in ("id", "type"):
            v["observedAt"] = at
    return out


def ctx(sent=None, ti=True, keys=None, unchanged=(DM, DM2), device_changed=True):
    return conf.Context(sent=sent or {"p": 1.5},
                        server_stamped=frozenset() if ti else frozenset({DM, DM2}),
                        keys_by_dm=keys or {DM: ("p",), DM2: ("relativeHumidity", "humidity")},
                        unchanged=frozenset(unchanged), device_changed=device_changed)


def classes(diffs):
    return sorted({(d.attr, d.classification, d.label) for d in diffs})


# -- compare_dms --------------------------------------------------------------------------

def test_identical_entities_have_no_difference():
    assert conf.compare_dms([dm()], [dm()], ctx()) == []


def test_system_attributes_are_ignored():
    actual = dm(createdAt="2026-01-01T00:00:00Z", modifiedAt="2026-01-01T00:00:01Z")
    actual["numValue"]["modifiedAt"] = "2026-01-01T00:00:01Z"
    assert conf.compare_dms([dm()], [actual], ctx()) == []


def test_observed_at_on_every_attribute_is_declared():
    diffs = conf.compare_dms([dm()], [with_observed(dm())], ctx())
    assert diffs and all(d.classification == "declared" and d.label == "observedAt" for d in diffs)
    assert {d.attr for d in diffs} == {"numValue", "dateObserved", "timeSource", "controlledProperty",
                                       "measurementType", "deviceType", "refDevice"}


def test_observed_at_declared_only_when_expected_has_none():
    expected = dm()
    expected["numValue"]["observedAt"] = "2026-01-01T09:00:00Z"
    actual = dm()
    actual["numValue"]["observedAt"] = T0
    diffs = conf.compare_dms([expected], [actual], ctx())
    assert [(d.attr, d.classification) for d in diffs] == [("numValue", "UNDECLARED")]


def test_observed_at_must_be_the_reading_time():
    diffs = conf.compare_dms([dm()], [with_observed(dm(), "2026-01-01T11:00:00Z")], ctx())
    assert diffs and all(d.classification == "UNDECLARED" for d in diffs if d.aspect == "observedAt")


def test_observed_at_of_a_server_stamped_dm_may_be_close_to_the_reading_time():
    near = with_observed(dm(), "2026-01-01T10:00:40Z")
    assert all(d.classification == "declared" for d in conf.compare_dms([dm()], [near], ctx(ti=False))
               if d.aspect == "observedAt")
    far = with_observed(dm(), "2026-01-01T10:02:00Z")
    assert any(d.classification == "UNDECLARED" for d in conf.compare_dms([dm()], [far], ctx(ti=False))
               if d.aspect == "observedAt")


def test_time_source_server_without_time_instant_is_declared():
    actual = dm(timeSource=prop("server"))
    diffs = conf.compare_dms([dm()], [actual], ctx(ti=False))
    assert classes(diffs) == [("timeSource", "declared", "timeSource-server")]


def test_time_source_server_with_time_instant_is_undeclared():
    actual = dm(timeSource=prop("server"))
    diffs = conf.compare_dms([dm()], [actual], ctx(ti=True))
    assert classes(diffs) == [("timeSource", "UNDECLARED", "")]


def test_server_stamp_is_per_entity():
    c = ctx(ti=True)._replace(server_stamped=frozenset({DM2}))
    actual = dm(timeSource=prop("server"))
    assert classes(conf.compare_dms([dm()], [actual], c)) == [("timeSource", "UNDECLARED", "")]


def test_time_source_device_instead_of_server_is_undeclared():
    expected = dm(timeSource=prop("server"))
    diffs = conf.compare_dms([expected], [dm()], ctx(ti=False))
    assert classes(diffs) == [("timeSource", "UNDECLARED", "")]


def test_server_clock_skew_within_tolerance_is_declared_only_without_time_instant():
    actual = dm(dateObserved=prop("2026-01-01T10:00:03Z"))
    assert classes(conf.compare_dms([dm()], [actual], ctx(ti=False))) == [
        ("dateObserved", "declared", "server-clock")]
    assert classes(conf.compare_dms([dm()], [actual], ctx(ti=True))) == [
        ("dateObserved", "UNDECLARED", "")]
    far = dm(dateObserved=prop("2026-01-01T10:01:00Z"))
    assert classes(conf.compare_dms([dm()], [far], ctx(ti=False))) == [
        ("dateObserved", "UNDECLARED", "")]


def test_one_e_three_is_declared_only_when_it_was_sent():
    expected, actual = dm(num=1), dm(num=1000)
    sent = {"p": "1e3"}
    assert classes(conf.compare_dms([expected], [actual], ctx(sent=sent))) == [
        ("numValue", "declared", "1e3")]
    assert classes(conf.compare_dms([expected], [actual], ctx(sent={"p": "2"}))) == [
        ("numValue", "UNDECLARED", "")]


def test_one_e_three_needs_exactly_device_1_and_dm_1000():
    sent = {"p": "1e3"}
    for exp, act in ((1, 999), (2, 1000), (1, 1000.5)):
        assert classes(conf.compare_dms([dm(num=exp)], [dm(num=act)], ctx(sent=sent))) == [
            ("numValue", "UNDECLARED", "")]


def test_one_e_three_does_not_excuse_another_attribute():
    actual = dm(num=1000, controlledProperty=prop("other"))
    diffs = conf.compare_dms([dm(num=1)], [actual], ctx(sent={"p": "1e3"}))
    assert classes(diffs) == [("controlledProperty", "UNDECLARED", ""), ("numValue", "declared", "1e3")]


def test_hex_value_missing_dm_is_declared_only_when_it_was_sent():
    assert classes(conf.compare_dms([dm(num=16)], [], ctx(sent={"p": "0x10"}))) == [
        ("<entity>", "declared", "0x10")]
    assert classes(conf.compare_dms([dm(num=16)], [], ctx(sent={"p": "16"}))) == [
        ("<entity>", "UNDECLARED", "")]


def test_hex_value_stale_dm_is_declared():
    stale = dm(num=9, dateObserved=prop("2026-01-01T09:00:00Z"))
    diffs = conf.compare_dms([dm(num=16)], [stale], ctx(sent={"p": "0x10"}))
    assert diffs and all(d.classification == "declared" and d.label == "0x10" for d in diffs)


def test_hex_value_is_declared_only_when_the_dm_is_unchanged_and_the_device_changed():
    stale = dm(num=9)
    sent = {"p": "0x10"}
    changed_dm = ctx(sent=sent, unchanged=())
    assert {d.classification for d in conf.compare_dms([dm(num=16)], [stale], changed_dm)} == {"UNDECLARED"}
    no_device = ctx(sent=sent, device_changed=False)
    assert {d.classification for d in conf.compare_dms([dm(num=16)], [stale], no_device)} == {"UNDECLARED"}
    assert classes(conf.compare_dms([dm(num=16)], [], ctx(sent=sent, unchanged=()))) == [
        ("<entity>", "UNDECLARED", "")]


def test_hex_declaration_is_per_entity():
    other = dm(DM2, num=3)
    bad = dm(DM2, num=4)
    diffs = conf.compare_dms([dm(num=16), other], [dm(num=9), bad], ctx(sent={"p": "0x10"}))
    assert {d.dm_id: d.classification for d in diffs if d.attr == "numValue"} == {
        DM: "declared", DM2: "UNDECLARED"}


def test_unexpected_entity_unit_and_extra_attribute_are_undeclared():
    extra = dm(DM2)
    assert classes(conf.compare_dms([dm()], [dm(), extra], ctx())) == [("<entity>", "UNDECLARED", "")]
    wrong_unit = dm()
    wrong_unit["numValue"]["unitCode"] = "CEL"
    assert classes(conf.compare_dms([dm()], [wrong_unit], ctx())) == [("numValue", "UNDECLARED", "")]
    with_ti = dm(TimeInstant=prop(T0))
    assert classes(conf.compare_dms([dm()], [with_ti], ctx())) == [("TimeInstant", "UNDECLARED", "")]


def test_value_type_difference_is_undeclared():
    expected = dm()
    del expected["numValue"]
    expected["textValue"] = prop("7.5")
    actual = dm(num=7.5)
    assert {d.classification for d in conf.compare_dms([expected], [actual], ctx())} == {"UNDECLARED"}


# -- checks -------------------------------------------------------------------------------

def test_dm_without_value_is_reported():
    empty = dm()
    del empty["numValue"]
    nullish = dm(DM2, num=None)
    assert conf.dms_without_value([dm(), empty, nullish]) == [DM, DM2]
    assert conf.dms_without_value([dm(num=0), dm(DM2, num=0.0)]) == []
    assert conf.dms_without_value([dm(num=None, textValue=prop(""))]) == []


def test_untouched_check():
    before = {DM: "m1", DM2: "m1"}
    assert conf.untouched_violations(before, {DM: "m1", DM2: "m1"}, {DM, DM2}) == []
    assert conf.untouched_violations(before, {DM: "m2", DM2: "m1"}, {DM, DM2}) == [
        f"{DM}: modifiedAt changed"]
    assert conf.untouched_violations({}, {DM: "m1"}, {DM}) == [f"{DM}: created"]
    # a touched entity may change or appear
    assert conf.untouched_violations(before, {DM: "m2", DM2: "m1"}, {DM2}) == []


def test_new_entities_must_belong_to_touched_channels():
    assert conf.untouched_violations({}, {DM: "m1"}, {DM2}, touched={DM}) == []
    assert conf.untouched_violations({}, {DM: "m1", "x": "m"}, {DM2}, touched={DM}) == ["x: created"]


def test_agent_document_comparison_uses_cutover_normalisation():
    expected = [{"object_id": "p", "name": "pressure", "type": "Number"}]
    doc = {"attributes": [{"object_id": "p", "name": "pressure", "type": "Number",
                           "expression": "", "metadata": {}, "ignored": 1}]}
    assert conf.agent_document_equal(doc, expected) is True
    doc["attributes"][0]["type"] = "Text"
    assert conf.agent_document_equal(doc, expected) is False
    assert conf.agent_document_equal({}, expected) is False


# -- waiting ------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_await_outcome_reports_latency_and_waits_for_quiet():
    clock = Clock()
    seq = iter([{"a": "1"}, {"a": "1"}, {"a": "2"}, {"a": "3"}] + [{"a": "3"}] * 50)
    snap, done_at = conf.await_outcome(lambda: next(seq), lambda s: s["a"] == "3",
                                       sleep=clock.sleep, now=clock.now)
    assert snap == {"a": "3"} and done_at == 2.0 and clock.t == 3.5


def test_await_outcome_gives_up_at_the_deadline_without_the_outcome():
    clock = Clock()
    snap, done_at = conf.await_outcome(lambda: {"a": "1"}, lambda s: False, sleep=clock.sleep,
                                       now=clock.now, deadline=20.0)
    assert done_at is None and clock.t == 20.0


def test_await_outcome_stops_at_the_deadline_if_it_never_goes_quiet():
    clock = Clock()
    counter = iter(range(10_000))
    snap, done_at = conf.await_outcome(lambda: {"a": str(next(counter))}, lambda s: True,
                                       sleep=clock.sleep, now=clock.now, deadline=10.0)
    assert done_at is not None and clock.t <= 10.5


def test_hold_waits_the_fixed_time_and_sees_a_late_change():
    clock = Clock()
    reads = iter([{"a": "1"}] * 5 + [{"a": "2"}] * 100)
    snap = conf.hold(lambda: next(reads), 10.0, sleep=clock.sleep, now=clock.now)
    assert clock.t == 10.0 and snap == {"a": "2"}


def case(name="c", writes=(), must_change=None, message=None):
    return conf.Case(name, message or {"p": 1}, frozenset(writes),
                     bool(writes) if must_change is None else must_change)


def test_outcome_violations_are_exact():
    ok = dict(device_changed=True, changed_names={"pressure"}, existing_names={"pressure"},
              expected_existing={"pressure"})
    assert conf.outcome_violations(case(writes={"pressure"}), **ok) == []
    # nothing happened although something must
    nothing = dict(ok, device_changed=False, changed_names=set())
    assert conf.outcome_violations(case(writes={"pressure"}), **nothing)
    # something happened although nothing may
    assert conf.outcome_violations(case(), **ok)
    # the wrong DM changed, or one too many
    assert conf.outcome_violations(case(writes={"pressure"}), **dict(ok, changed_names={"status"}))
    assert conf.outcome_violations(case(writes={"pressure"}), **dict(ok, changed_names={"pressure", "status"}))
    # the Device moves, the DM stays (the "0x10" shape)
    hexcase = case(must_change=True)
    assert conf.outcome_violations(hexcase, **dict(ok, changed_names=set())) == []
    # the set of existing DM is exactly the accumulated writes
    assert conf.outcome_violations(case(writes={"pressure"}), **dict(ok, existing_names={"pressure", "x"}))
    assert conf.outcome_violations(case(writes={"pressure"}), **dict(ok, expected_existing={"pressure", "y"}))


# -- channels, cases ----------------------------------------------------------------------

def _channels():
    return conf.live_channels("acme", "conf-iota-dm-aa", "legacy")


def test_live_profile_covers_the_required_kinds():
    ch = {c.name: c for c in _channels()}
    assert ch["airTemperature"].kind == "number" and ch["airTemperatureMin"].key == "tMin"
    assert ch["status"].kind == "text" and ch["windSpeed"].kind == "runtime"
    assert ch["soilMoisture"].key == "soilMoisture" and ch["soilMoisture"].expression
    assert {c.key for c in _channels() if c.name == "relativeHumidity"} == {"humidity", "relativeHumidity"}
    assert ch["relativeHumidity"].key == "relativeHumidity"  # canonical last


def test_absent_dm_ids():
    channels = _channels()
    ids = conf.absent_dm_ids(channels, {"humidity": 4, "TimeInstant": T0})
    present = f"urn:ngsi-ld:DeviceMeasurement:acme:conf-iota-dm-aa:relativeHumidity"
    assert present not in ids and len(ids) == len({c.dm_id for c in channels}) - 1


def test_cases_use_distinct_values_and_cover_the_brief():
    counter = iter(range(10_000))
    cases = conf.build_cases(ti=lambda: f"2026-01-01T10:{next(counter):02d}:00Z",
                             val=lambda: 100 + next(counter) * 1.25)
    assert len({json.dumps(c.message, sort_keys=True) for c in cases}) == len(cases)
    names = {c.name for c in cases}
    for needed in ("partial-with-time-first", "partial-with-time", "partial-without-time", "statistics",
                   "runtime-number", "runtime-text", "alias-only", "canonical-only", "alias-and-canonical",
                   "scaling-key-is-attribute-name", "text"):
        assert needed in names
    sent = [c.message.get("p") for c in cases]
    for bad in (None, "ERR", "12abc", "", 0, "1e3", "0x10"):
        assert any(s == bad and type(s) is type(bad) for s in sent), bad
    # every invalid value follows a valid, distinct reading of the same channel
    for i, c in enumerate(cases):
        if c.name.startswith("p-invalid"):
            prev = cases[i - 1].message["p"]
            assert isinstance(prev, float)
    values = [m for c in cases for k, m in c.message.items() if k == "p" and isinstance(m, float)]
    assert len(values) == len(set(values))
    assert cases[0].message.keys() == {"p", "TimeInstant"}
    by = {c.name: c for c in cases}
    assert by["all-channels"].writes == {"airTemperature", "airTemperatureMin", "airTemperatureMax", "pressure",
                                         "relativeHumidity", "soilMoisture", "status", "windSpeed"}
    assert by["partial-with-time"].writes == {"airTemperatureMax"}
    assert by["alias-only"].writes == {"relativeHumidity"}
    for label in ("null", "ERR", "12abc", "empty"):
        c = by[f"p-invalid-{label}"]
        assert c.writes == frozenset() and c.must_change is False
    assert by["p-invalid-zero"].writes == {"pressure"}
    assert by["p-invalid-1e3"].writes == {"pressure"}
    assert by["p-invalid-0x10"].writes == frozenset() and by["p-invalid-0x10"].must_change is True
    assert all(c.must_change for c in cases if c.writes)


# -- hash ---------------------------------------------------------------------------------

def test_generator_hash_depends_on_both_files(tmp_path):
    (tmp_path / "iota_dm_mappings.py").write_text("a")
    (tmp_path / "device_measurement_shape.py").write_text("b")
    h1 = conf.generator_hash(tmp_path)
    assert len(h1) == 64 and conf.generator_hash(tmp_path) == h1
    (tmp_path / "device_measurement_shape.py").write_text("c")
    assert conf.generator_hash(tmp_path) != h1


def test_generator_hash_ignores_line_endings(tmp_path):
    (tmp_path / "device_measurement_shape.py").write_text("b\n")
    (tmp_path / "iota_dm_mappings.py").write_bytes(b"a\nb\n")
    unix = conf.generator_hash(tmp_path)
    (tmp_path / "iota_dm_mappings.py").write_bytes(b"a\r\nb\r\n")
    assert conf.generator_hash(tmp_path) == unix


def test_recorded_hash_matches_the_generator_modules():
    path = pathlib.Path(__file__).resolve().parents[3] / "config" / "iota-dm-conformance.json"
    recorded = json.loads(path.read_text())
    assert recorded["generator_hash"] == conf.generator_hash(), (
        "services/common/iota_dm_mappings.py or device_measurement_shape.py changed since the last "
        "live conformance run: re-run services/entity-manager/scripts/iota_dm_conformance.py against "
        "the IoT Agent (test tenant) with --record config/iota-dm-conformance.json, then commit it.")
