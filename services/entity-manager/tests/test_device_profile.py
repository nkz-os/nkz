"""`device_profile.entity_profile_code`: the profile an entity is on, and what it logs once."""
import logging
import threading

import pytest

from tests.real_common import seed_real_common

seed_real_common()

import device_profile  # noqa: E402

TENANT = "acmefarm"
URN = f"urn:ngsi-ld:Device:{TENANT}:S1"
MACHINE = f"urn:ngsi-ld:ManufacturingMachine:{TENANT}:M1"
DEVICE_IRI = "https://smartdatamodels.org/dataModel.Device/"
MESSAGE = "device_profile_code_fallback"
MISMATCH = "device_profile_code_mismatch"


def _prop(value):
    return {"type": "Property", "value": value}


def _ref(code, tenant=TENANT):
    return {"type": "Relationship", "object": f"urn:ngsi-ld:DeviceModel:{tenant}:{code}"}


def _device(urn=URN, type_="Device", **attrs):
    return {"id": urn, "type": type_, **attrs}


@pytest.fixture(autouse=True)
def _nothing_logged_yet(monkeypatch):
    monkeypatch.setattr(device_profile, "_logged", type(device_profile._logged)())


def _fallbacks(caplog):
    return [r for r in caplog.records if r.getMessage().startswith(MESSAGE)]


def _mismatches(caplog):
    return [r for r in caplog.records if r.getMessage().startswith(MISMATCH)]


# ---- which profile -----------------------------------------------------------

@pytest.mark.parametrize("attrs,expected,logged", [
    ({"refDeviceModel": _ref("model_code")}, "model_code", 0),
    ({"profileCode": _prop("legacy_code")}, "legacy_code", 1),
    ({"refDeviceModel": _ref("same"), "profileCode": _prop("same")}, "same", 0),
    ({"refDeviceModel": _ref("model_code"), "profileCode": _prop("legacy_code")}, "legacy_code", 0),
    ({DEVICE_IRI + "refDeviceModel": _ref("model_code"), "profileCode": _prop("x")}, "x", 0),
    ({"refDeviceModel": _ref("model_code"), "profileCode": _prop("")}, "model_code", 0),
    ({"refDeviceModel": _ref("model_code", "othertenant"), "profileCode": _prop("legacy_code")},
     "legacy_code", 1),
    ({"refDeviceModel": _ref("model_code", "othertenant")}, None, 0),
    ({"refDeviceModel": {"type": "Relationship", "object": "urn:x"}, "profileCode": _prop("legacy_code")},
     "legacy_code", 1),
    ({}, None, 0),
], ids=["model", "legacy", "equal", "differ", "differ-iri", "empty-legacy", "foreign-and-legacy", "foreign", "malformed-and-legacy", "none"])
@pytest.mark.parametrize("type_", ["Device", DEVICE_IRI + "Device"])
def test_the_profile_of_a_device(attrs, expected, logged, type_, caplog):
    with caplog.at_level(logging.INFO):
        assert device_profile.entity_profile_code(TENANT, _device(type_=type_, **attrs)) == expected
    assert len(_fallbacks(caplog)) == logged


@pytest.mark.parametrize("attrs,expected", [
    ({"profileCode": _prop("p")}, "p"),
    ({"refDeviceModel": _ref("model_code"), "profileCode": _prop("p")}, "p"),
    ({"refDeviceModel": _ref("model_code")}, None),
    ({}, None),
])
def test_a_machine_is_on_its_profile_code_only_and_logs_nothing(attrs, expected, caplog):
    machine = _device(MACHINE, "ManufacturingMachine", **attrs)
    with caplog.at_level(logging.DEBUG):
        assert device_profile.entity_profile_code(TENANT, machine) == expected
    assert caplog.records == []


@pytest.mark.parametrize("entity", [None, {}])
def test_no_entity_has_no_profile(entity):
    assert device_profile.entity_profile_code(TENANT, entity) is None


# ---- the fallback log --------------------------------------------------------

def _legacy(urn=URN):
    return _device(urn, profileCode=_prop("p"))


def test_the_fallback_is_one_info_with_its_ids_per_tenant_and_entity(caplog):
    other = f"urn:ngsi-ld:Device:{TENANT}:S2"
    with caplog.at_level(logging.INFO):
        for tenant, urn in [(TENANT, URN), (TENANT, URN), (TENANT, other), ("othertenant", URN),
                            (TENANT, other), ("othertenant", URN)]:
            assert device_profile.entity_profile_code(tenant, _legacy(urn)) == "p"
    records = _fallbacks(caplog)
    assert [r.levelno for r in records] == [logging.INFO] * 3
    assert [r.args for r in records] == [(TENANT, URN, "p"), (TENANT, other, "p"), ("othertenant", URN, "p")]
    for part in (f"tenant={TENANT}", f"entity_id={URN}", "profile=p"):
        assert part in records[0].getMessage()


def test_the_memory_of_the_log_is_bounded(monkeypatch, caplog):
    monkeypatch.setattr(device_profile, "_MAX_LOGGED", 3)
    with caplog.at_level(logging.INFO):
        for i in range(20):
            device_profile.entity_profile_code(TENANT, _legacy(f"urn:ngsi-ld:Device:{TENANT}:S{i}"))
        assert len(device_profile._logged) == 3
        assert len(_fallbacks(caplog)) == 20
        # the newest ones are the remembered ones
        device_profile.entity_profile_code(TENANT, _legacy(f"urn:ngsi-ld:Device:{TENANT}:S19"))
    assert len(_fallbacks(caplog)) == 20


def test_threads_reading_the_same_device_log_it_once(monkeypatch, caplog):
    class _SlowDict(type(device_profile._logged)):
        """Gives every other thread time to look before this one has written."""

        def __contains__(self, key):
            found = super().__contains__(key)
            threading.Event().wait(0.01)
            return found

    monkeypatch.setattr(device_profile, "_logged", _SlowDict())
    start = threading.Barrier(8)

    def _read():
        start.wait()
        device_profile.entity_profile_code(TENANT, _legacy())

    threads = [threading.Thread(target=_read) for _ in range(8)]
    with caplog.at_level(logging.INFO):
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert len(_fallbacks(caplog)) == 1


# ---- the log of a model that is ignored ---------------------------------------

IGNORED = "refDeviceModel ignored"


def _ignored(caplog):
    return [r for r in caplog.records if r.getMessage().startswith(IGNORED)]


@pytest.mark.parametrize("ref,reason", [
    (_ref("m", "othertenant"), "foreign tenant"),
    ({"type": "Relationship", "object": "urn:x"}, "malformed"),
    ({"type": "Relationship", "object": [f"urn:ngsi-ld:DeviceModel:{TENANT}:m"] * 2}, "malformed"),
    (None, "malformed"),
], ids=["foreign", "malformed", "two-targets", "null"])
@pytest.mark.parametrize("attrs,code", [({}, None), ({"profileCode": _prop("p")}, "p")],
                         ids=["no-code", "with-code"])
def test_an_ignored_model_is_one_warning_per_tenant_and_entity(attrs, code, ref, reason, caplog):
    other = f"urn:ngsi-ld:Device:{TENANT}:S2"
    with caplog.at_level(logging.DEBUG):
        for urn in (URN, other, URN, other, URN):
            assert device_profile.entity_profile_code(TENANT, _device(urn, refDeviceModel=ref, **attrs)) == code
        assert device_profile.entity_profile_code("thirdtenant", _device(refDeviceModel=ref, **attrs)) == code
    records = _ignored(caplog)
    assert [r.levelno for r in records] == [logging.WARNING] * 3
    assert [r.args[:2] for r in records] == [(TENANT, URN), (TENANT, other), ("thirdtenant", URN)]
    for part in (f"tenant={TENANT}", f"entity_id={URN}", f"reason={reason}"):
        assert part in records[0].getMessage()
    assert _mismatches(caplog) == [] and len(_fallbacks(caplog)) == (3 if code else 0)


def test_a_model_that_is_used_or_absent_is_not_logged_as_ignored(caplog):
    with caplog.at_level(logging.DEBUG):
        for attrs in ({}, {"profileCode": _prop("p")}, {"refDeviceModel": _ref("m")},
                      {"refDeviceModel": _ref("m"), "profileCode": _prop("p")}):
            device_profile.entity_profile_code(TENANT, _device(**attrs))
    assert _ignored(caplog) == []


def test_a_model_on_a_machine_is_not_logged_as_ignored(caplog):
    with caplog.at_level(logging.DEBUG):
        device_profile.entity_profile_code(
            TENANT, _device(MACHINE, "ManufacturingMachine", refDeviceModel=_ref("m", "othertenant")))
    assert caplog.records == []


def test_the_log_of_an_ignored_model_uses_the_same_bounded_memory(monkeypatch, caplog):
    monkeypatch.setattr(device_profile, "_MAX_LOGGED", 3)
    for i in range(9):
        device_profile.entity_profile_code(
            TENANT, _device(f"urn:ngsi-ld:Device:{TENANT}:S{i}", refDeviceModel=_ref("m", "othertenant")))
    assert len(device_profile._logged) == 3 and len(_ignored(caplog)) == 9


# ---- the mismatch log --------------------------------------------------------

def _disagreeing(urn=URN):
    return _device(urn, refDeviceModel=_ref("stale"), profileCode=_prop("fresh"))


def test_two_codes_that_differ_are_one_warning_per_tenant_and_entity_and_profile_code_wins(caplog):
    other = f"urn:ngsi-ld:Device:{TENANT}:S2"
    with caplog.at_level(logging.INFO):
        for urn in (URN, URN, other, URN, other):
            assert device_profile.entity_profile_code(TENANT, _disagreeing(urn)) == "fresh"
    records = _mismatches(caplog)
    assert [r.levelno for r in records] == [logging.WARNING] * 2
    assert [r.args for r in records] == [(TENANT, URN, "stale", "fresh"), (TENANT, other, "stale", "fresh")]
    for part in (f"tenant={TENANT}", f"entity_id={URN}", "refDeviceModel=stale", "profileCode=fresh"):
        assert part in records[0].getMessage()
    assert _fallbacks(caplog) == []


@pytest.mark.parametrize("attrs", [
    {"refDeviceModel": _ref("p"), "profileCode": _prop("p")},
    {"refDeviceModel": _ref("p")},
    {"refDeviceModel": _ref("p"), "profileCode": _prop("")},
    {"refDeviceModel": _ref("p", "othertenant"), "profileCode": _prop("old")},
    {"profileCode": _prop("old")},
], ids=["equal", "model-only", "empty-legacy", "foreign-model", "legacy-only"])
def test_anything_but_two_usable_codes_that_differ_is_no_mismatch(attrs, caplog):
    with caplog.at_level(logging.DEBUG):
        device_profile.entity_profile_code(TENANT, _device(**attrs))
    assert _mismatches(caplog) == []


def test_a_machine_with_two_codes_is_no_mismatch(caplog):
    machine = _device(MACHINE, "ManufacturingMachine", refDeviceModel=_ref("new"), profileCode=_prop("old"))
    with caplog.at_level(logging.DEBUG):
        assert device_profile.entity_profile_code(TENANT, machine) == "old"
    assert caplog.records == []


def test_a_device_that_logged_the_fallback_still_logs_a_later_mismatch(caplog):
    with caplog.at_level(logging.INFO):
        device_profile.entity_profile_code(TENANT, _legacy())
        device_profile.entity_profile_code(TENANT, _disagreeing())
    assert (len(_fallbacks(caplog)), len(_mismatches(caplog))) == (1, 1)


def test_both_logs_share_one_bounded_memory(monkeypatch, caplog):
    monkeypatch.setattr(device_profile, "_MAX_LOGGED", 3)
    with caplog.at_level(logging.INFO):
        for i in range(5):
            device_profile.entity_profile_code(TENANT, _disagreeing(f"urn:ngsi-ld:Device:{TENANT}:M{i}"))
            device_profile.entity_profile_code(TENANT, _legacy(f"urn:ngsi-ld:Device:{TENANT}:L{i}"))
    assert len(device_profile._logged) == 3
    assert (len(_fallbacks(caplog)), len(_mismatches(caplog))) == (5, 5)
