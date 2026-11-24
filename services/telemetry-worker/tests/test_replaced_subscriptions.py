"""Subscriptions whose definition changed get a new id; the old one is retired safely.

Orion cannot drop throttling with a PATCH, and delete+recreate of one id loses
notifications while the broker's per-replica cache is stale. So the replacement is
created first and the old one is deleted only once the replacement is proven active
and has been known for RETIRE_MIN_AGE_S.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from telemetry_worker import subscription_manager as sm  # noqa: E402

BASE = f"{sm.ORION_URL}/ngsi-ld/v1/subscriptions"
OLD_IDS = {
    "Telemetry Worker - CropHealthAssessment updates":
        "urn:ngsi-ld:Subscription:telemetry-worker:crophealthassessment-updates",
    "Telemetry Worker - Weather zonal stats (AgriParcelRecord)":
        "urn:ngsi-ld:Subscription:telemetry-worker:weather-zonal-stats-agriparcelrecord",
    "Telemetry Worker - AgriSoil water budget attributes":
        "urn:ngsi-ld:Subscription:telemetry-worker:agrisoil-water-budget-attributes",
    "Telemetry Worker - WeatherObserved virtual stations":
        "urn:ngsi-ld:Subscription:telemetry-worker:weatherobserved-virtual-stations",
    "Telemetry Worker - WeatherForecast daily aggregates":
        "urn:ngsi-ld:Subscription:telemetry-worker:weatherforecast-daily-aggregates",
    "Telemetry Worker - WeatherAlert updates":
        "urn:ngsi-ld:Subscription:telemetry-worker:weatheralert-updates",
}
SOME_OLD = "Telemetry Worker - CropHealthAssessment updates"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(sm, "_monotonic", c)
    monkeypatch.setattr(sm, "_FIRST_SEEN", {})
    return c


def _resp(status):
    r = MagicMock()
    r.status_code = status
    r.text = ""
    return r


def _live(sub_def, **over):
    s = {
        "id": sm._subscription_id(sub_def),
        "description": sub_def["description"],
        "isActive": True,
        "status": "active",
        "notification": {"endpoint": {"uri": sub_def["notification"]["endpoint"]["uri"]}},
    }
    s.update(over)
    return s


def _declared(skip=()):
    return [_live(d) for d in sm.SUBSCRIPTIONS if d["description"] not in skip]


def _old(description=SOME_OLD, sub_id=None):
    return {
        "id": sub_id or OLD_IDS[description],
        "description": description,
        "isActive": True,
        "status": "active",
        "notification": {"endpoint": {"uri": sm.NOTIFICATION_URL}},
    }


def _new_desc(old=SOME_OLD):
    return sm.REPLACED_DESCRIPTIONS[old]


def _new_def(old=SOME_OLD):
    return next(d for d in sm.SUBSCRIPTIONS if d["description"] == _new_desc(old))


def _pass(monkeypatch, existing, tenant="tenant-a", post_status=201, post_raises=None):
    calls = []
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "")
    monkeypatch.setattr(sm, "_fetch_all_subscriptions", lambda headers: list(existing))
    monkeypatch.setattr(sm, "reactivate_if_paused", lambda *a, **k: None)

    def post(url, **kw):
        calls.append(("post", kw["json"]["id"]))
        if post_raises:
            raise post_raises
        return _resp(post_status)

    def delete(url, **kw):
        calls.append(("delete", url.rsplit("/", 1)[-1]))
        return _resp(204)

    with patch.object(sm.requests, "post", side_effect=post), \
         patch.object(sm.requests, "patch", side_effect=lambda *a, **k: _resp(204)), \
         patch.object(sm.requests, "delete", side_effect=delete):
        sm._ensure_tenant_subscriptions(tenant)
    return calls


def _deletes(calls):
    return [c[1] for c in calls if c[0] == "delete"]


def test_replacements_cover_the_six_and_ids_are_new_and_unique():
    assert set(sm.REPLACED_DESCRIPTIONS) == set(OLD_IDS)
    new_ids = []
    for old, new in sm.REPLACED_DESCRIPTIONS.items():
        d = next(x for x in sm.SUBSCRIPTIONS if x["description"] == new)
        new_ids.append(sm._subscription_id(d))
        assert new.isascii() and new.startswith("Telemetry Worker - ")
    assert len(set(new_ids)) == 6
    assert not set(new_ids) & set(OLD_IDS.values())
    all_ids = [sm._subscription_id(d) for d in sm.SUBSCRIPTIONS]
    assert len(set(all_ids)) == len(all_ids)


def test_replaced_descriptions_are_neither_declared_nor_retired():
    declared = {d["description"] for d in sm.SUBSCRIPTIONS}
    assert not set(sm.REPLACED_DESCRIPTIONS) & declared
    assert not set(sm.REPLACED_DESCRIPTIONS) & sm.RETIRED_DESCRIPTIONS
    assert set(sm.REPLACED_DESCRIPTIONS.values()) <= declared


def test_old_is_kept_when_the_replacement_is_missing_and_it_is_created(monkeypatch, clock, caplog):
    new_id = sm._subscription_id(_new_def())
    with caplog.at_level("INFO"):
        calls = _pass(monkeypatch, _declared(skip=[_new_desc()]) + [_old()])
    assert ("post", new_id) in calls
    assert _deletes(calls) == []
    assert any("missing" in r.getMessage() for r in caplog.records)


def test_old_is_kept_when_the_replacement_is_not_active(monkeypatch, clock):
    for over in ({"isActive": False}, {"status": "paused"}, {"status": None}, {"isActive": False, "status": "paused"}):
        sm._FIRST_SEEN.clear()
        existing = [s for s in _declared() if s["description"] != _new_desc()]
        existing.append(_live(_new_def(), **over))
        _pass(monkeypatch, existing + [_old()])
        clock.t += 10_000
        calls = _pass(monkeypatch, existing + [_old()])
        assert _deletes(calls) == [], over


def test_a_listing_without_status_is_not_proof(monkeypatch, clock):
    new = _live(_new_def())
    del new["status"]
    existing = [s for s in _declared() if s["description"] != _new_desc()] + [new, _old()]
    _pass(monkeypatch, existing)
    clock.t += 10_000
    assert _deletes(_pass(monkeypatch, existing)) == []


def test_old_is_kept_while_the_replacement_is_younger_than_the_window(monkeypatch, clock, caplog):
    existing = _declared() + [_old()]
    assert _deletes(_pass(monkeypatch, existing)) == []  # first seen now
    clock.t += sm.RETIRE_MIN_AGE_S - 1
    with caplog.at_level("INFO"):
        assert _deletes(_pass(monkeypatch, existing)) == []
    assert any("too recent" in r.getMessage() for r in caplog.records)


def test_old_is_deleted_once_the_window_has_passed(monkeypatch, clock, caplog):
    existing = _declared() + [_old()]
    _pass(monkeypatch, existing)
    clock.t += sm.RETIRE_MIN_AGE_S
    with caplog.at_level("INFO"):
        calls = _pass(monkeypatch, existing)
    assert _deletes(calls) == [OLD_IDS[SOME_OLD]]
    msg = [r.getMessage() for r in caplog.records if "Retired replaced" in r.getMessage()]
    assert len(msg) == 1
    assert "tenant-a" in msg[0] and OLD_IDS[SOME_OLD] in msg[0]
    assert sm._subscription_id(_new_def()) in msg[0]


def test_a_replacement_created_in_this_process_counts_from_its_creation(monkeypatch, clock):
    # pass 1: replacement absent from the listing -> created, old kept
    assert _deletes(_pass(monkeypatch, _declared(skip=[_new_desc()]) + [_old()])) == []
    # pass 2 right after: listing shows it, but it is only seconds old
    clock.t += 5
    assert _deletes(_pass(monkeypatch, _declared() + [_old()])) == []
    clock.t += sm.RETIRE_MIN_AGE_S
    assert _deletes(_pass(monkeypatch, _declared() + [_old()])) == [OLD_IDS[SOME_OLD]]


def test_creation_failure_keeps_the_old_one(monkeypatch, clock):
    listing = _declared(skip=[_new_desc()]) + [_old()]
    for kw in ({"post_status": 500}, {"post_raises": RuntimeError("boom")}):
        sm._FIRST_SEEN.clear()
        assert _deletes(_pass(monkeypatch, listing, **kw)) == []
        clock.t += 10_000
        assert _deletes(_pass(monkeypatch, listing, **kw)) == []


def test_a_failed_pass_creation_does_not_start_the_clock(monkeypatch, clock):
    listing = _declared(skip=[_new_desc()]) + [_old()]
    _pass(monkeypatch, listing, post_status=500)
    assert (("tenant-a", sm._subscription_id(_new_def()))) not in sm._FIRST_SEEN


def test_tenants_are_independent(monkeypatch, clock):
    existing_a = _declared() + [_old()]
    _pass(monkeypatch, existing_a, tenant="tenant-a")
    clock.t += sm.RETIRE_MIN_AGE_S
    # tenant acme sees the replacement for the first time now
    assert _deletes(_pass(monkeypatch, existing_a, tenant="acme")) == []
    assert _deletes(_pass(monkeypatch, existing_a, tenant="tenant-a")) == [OLD_IDS[SOME_OLD]]


def test_exact_description_only(monkeypatch, clock):
    lookalikes = [
        _old(SOME_OLD, "urn:ngsi-ld:Subscription:p") | {"description": SOME_OLD + " (copy)"},
        _old(SOME_OLD, "urn:ngsi-ld:Subscription:q") | {"description": "Another module - CropHealthAssessment updates"},
        _old(SOME_OLD, "urn:ngsi-ld:Subscription:r") | {"description": SOME_OLD.lower()},
    ]
    existing = _declared() + lookalikes
    _pass(monkeypatch, existing)
    clock.t += 10_000
    assert _deletes(_pass(monkeypatch, existing)) == []


def test_any_id_with_the_old_description_is_retired(monkeypatch, clock):
    odd = _old(SOME_OLD, "urn:ngsi-ld:Subscription:legacy-copy")
    existing = _declared() + [odd]
    _pass(monkeypatch, existing)
    clock.t += sm.RETIRE_MIN_AGE_S
    assert _deletes(_pass(monkeypatch, existing)) == ["urn:ngsi-ld:Subscription:legacy-copy"]


def test_second_pass_after_deletion_does_nothing(monkeypatch, clock):
    _pass(monkeypatch, _declared() + [_old()])
    clock.t += sm.RETIRE_MIN_AGE_S
    assert _pass(monkeypatch, _declared()) == []


def test_all_six_are_retired_together(monkeypatch, clock):
    existing = _declared() + [_old(d) for d in OLD_IDS]
    _pass(monkeypatch, existing)
    clock.t += sm.RETIRE_MIN_AGE_S
    assert sorted(_deletes(_pass(monkeypatch, existing))) == sorted(OLD_IDS.values())


def test_a_restart_waits_again(monkeypatch, clock):
    existing = _declared() + [_old()]
    _pass(monkeypatch, existing)
    clock.t += 10_000
    sm._FIRST_SEEN.clear()  # new process: empty record
    assert _deletes(_pass(monkeypatch, existing)) == []
    clock.t += sm.RETIRE_MIN_AGE_S
    assert _deletes(_pass(monkeypatch, existing)) == [OLD_IDS[SOME_OLD]]


def test_the_leftover_logic_does_not_touch_old_subscriptions(monkeypatch, clock):
    # An old id kept while the replacement is young is never removed as a "leftover".
    calls = _pass(monkeypatch, _declared() + [_old()])
    assert _deletes(calls) == []


def test_a_failing_delete_does_not_skip_the_others(monkeypatch, clock):
    existing = _declared() + [_old(d) for d in OLD_IDS]
    _pass(monkeypatch, existing)
    clock.t += sm.RETIRE_MIN_AGE_S
    tried = []

    def delete(url, **kw):
        tried.append(url.rsplit("/", 1)[-1])
        if len(tried) == 1:
            raise RuntimeError("boom")
        return _resp(204)

    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "")
    monkeypatch.setattr(sm, "_fetch_all_subscriptions", lambda headers: list(existing))
    monkeypatch.setattr(sm, "reactivate_if_paused", lambda *a, **k: None)
    with patch.object(sm.requests, "delete", side_effect=delete):
        sm._ensure_tenant_subscriptions("tenant-a")
    assert sorted(tried) == sorted(OLD_IDS.values())
