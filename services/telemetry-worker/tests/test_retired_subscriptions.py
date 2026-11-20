"""A subscription the worker no longer declares is removed from the broker.

Dropping an entry from SUBSCRIPTIONS only stops it being reconciled: the one
already registered stays, per tenant, until it is deleted.
"""
import os
import sys
from unittest.mock import MagicMock, patch

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from telemetry_worker import subscription_manager as sm  # noqa: E402

BASE = f"{sm.ORION_URL}/ngsi-ld/v1/subscriptions"


def _resp(status):
    r = MagicMock()
    r.status_code = status
    r.text = ""
    return r


def _declared():
    """A healthy, current subscription for every entry the manager declares."""
    return [
        {
            "id": sm._subscription_id(sub_def),
            "description": sub_def["description"],
            "notification": {"endpoint": {"uri": sub_def["notification"]["endpoint"]["uri"]}},
        }
        for sub_def in sm.SUBSCRIPTIONS
    ]


def _retired(sub_id, description=None):
    return {
        "id": sub_id,
        "description": description or sorted(sm.RETIRED_DESCRIPTIONS)[0],
        "notification": {"endpoint": {"uri": sm.NOTIFICATION_URL}},
    }


def _run(monkeypatch, existing, delete_status=204, post=None):
    calls = []
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "")
    monkeypatch.setattr(sm, "_fetch_all_subscriptions", lambda headers: existing)
    monkeypatch.setattr(sm, "reactivate_if_paused", lambda *a, **k: None)

    def rec(verb, status):
        def _call(url, **kwargs):
            calls.append((verb, url))
            return _resp(status)
        return _call

    with patch.object(sm.requests, "post", side_effect=post or rec("post", 201)), \
         patch.object(sm.requests, "patch", side_effect=rec("patch", 204)), \
         patch.object(sm.requests, "delete", side_effect=rec("delete", delete_status)):
        sm._ensure_tenant_subscriptions("tenant-a")
    return calls


def test_retired_descriptions_are_the_former_agrisensor_and_agridevice_ones():
    assert sm.RETIRED_DESCRIPTIONS == {
        "Telemetry Worker - AgriSensor updates",
        "Telemetry Worker - AgriDevice updates",
    }


def test_a_description_is_never_both_declared_and_retired():
    declared = {sub_def["description"] for sub_def in sm.SUBSCRIPTIONS}
    assert not declared & sm.RETIRED_DESCRIPTIONS


def test_a_retired_subscription_is_deleted(monkeypatch):
    sub_id = "urn:ngsi-ld:Subscription:telemetry-worker:agrisensor-updates"
    calls = _run(monkeypatch, _declared() + [_retired(sub_id)])
    assert calls == [("delete", f"{BASE}/{sub_id}")]


def test_both_retired_descriptions_are_deleted(monkeypatch):
    existing = [
        _retired("urn:ngsi-ld:Subscription:a", "Telemetry Worker - AgriSensor updates"),
        _retired("urn:ngsi-ld:Subscription:b", "Telemetry Worker - AgriDevice updates"),
    ]
    calls = _run(monkeypatch, _declared() + existing)
    assert sorted(url.rsplit("/", 1)[-1] for verb, url in calls if verb == "delete") == [
        "urn:ngsi-ld:Subscription:a", "urn:ngsi-ld:Subscription:b",
    ]


def test_a_404_on_delete_counts_as_done(monkeypatch, caplog):
    sub_id = "urn:ngsi-ld:Subscription:gone"
    with caplog.at_level("WARNING"):
        calls = _run(monkeypatch, _declared() + [_retired(sub_id)], delete_status=404)
    assert calls == [("delete", f"{BASE}/{sub_id}")]
    assert not [r for r in caplog.records if r.levelname in ("WARNING", "ERROR")]


def test_a_rerun_with_nothing_left_does_nothing(monkeypatch):
    assert _run(monkeypatch, _declared()) == []


def test_a_non_matching_description_is_untouched(monkeypatch):
    """Exact match only: not a prefix, not the entity type, not another service."""
    lookalikes = [
        _retired("urn:ngsi-ld:Subscription:p", "Telemetry Worker - AgriSensor updates (copy)"),
        _retired("urn:ngsi-ld:Subscription:q", "Telemetry Worker - AgriSensor"),
        _retired("urn:ngsi-ld:Subscription:r", "Another service - AgriSensor updates"),
        _retired("urn:ngsi-ld:Subscription:s", "telemetry worker - agrisensor updates"),
        {**_retired("urn:ngsi-ld:Subscription:t", "Someone else"),
         "entities": [{"type": "AgriSensor"}]},
    ]
    assert _run(monkeypatch, _declared() + lookalikes) == []


def test_a_retired_subscription_without_id_is_skipped(monkeypatch):
    assert _run(monkeypatch, _declared() + [_retired(None)]) == []


def test_a_failed_delete_does_not_block_the_declared_reconciliation(monkeypatch):
    ids = ["urn:ngsi-ld:Subscription:old-1", "urn:ngsi-ld:Subscription:old-2"]
    tried, created = [], []
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "")
    monkeypatch.setattr(sm, "_fetch_all_subscriptions",
                        lambda headers: _declared()[1:] + [_retired(i) for i in ids])
    monkeypatch.setattr(sm, "reactivate_if_paused", lambda *a, **k: None)

    def failing_delete(url, **kwargs):
        tried.append(url.rsplit("/", 1)[-1])
        raise sm.requests.ConnectionError("broker unreachable")

    def post(url, **kwargs):
        created.append(url)
        return _resp(201)

    with patch.object(sm.requests, "delete", side_effect=failing_delete), \
         patch.object(sm.requests, "post", side_effect=post):
        sm._ensure_tenant_subscriptions("tenant-a")
    assert sorted(tried) == ids          # a failure does not stop the next delete
    assert len(created) == 1             # the missing declared one is still created


def test_an_http_error_on_delete_does_not_block_the_declared_reconciliation(monkeypatch):
    sub_id = "urn:ngsi-ld:Subscription:old"
    calls = _run(monkeypatch, _declared()[1:] + [_retired(sub_id)], delete_status=500)
    assert ("delete", f"{BASE}/{sub_id}") in calls
    assert any(verb == "post" for verb, _ in calls)


def test_cleanup_runs_before_the_declared_reconciliation(monkeypatch):
    sub_id = "urn:ngsi-ld:Subscription:old"
    calls = _run(monkeypatch, _declared()[1:] + [_retired(sub_id)])
    verbs = [verb for verb, _ in calls]
    assert verbs.index("delete") < verbs.index("post")
