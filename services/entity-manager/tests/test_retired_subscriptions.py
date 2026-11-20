"""A subscription the service no longer declares is removed from the broker.

Dropping an entry from SUBSCRIPTIONS only stops it being reconciled: the one
already registered stays, per tenant, pointing at a receiver that ignores it.
"""
import os
import sys
from unittest.mock import MagicMock, patch

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import subscription_manager as sm  # noqa: E402


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
            "notification": {"endpoint": {"uri": sm.NOTIFICATION_URL}},
        }
        for sub_def in sm.SUBSCRIPTIONS
    ]


def _retired(sub_id):
    return {
        "id": sub_id,
        "description": sorted(sm.RETIRED_DESCRIPTIONS)[0],
        "notification": {"endpoint": {"uri": sm.NOTIFICATION_URL}},
    }


def _run(monkeypatch, existing):
    calls = []
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "")
    monkeypatch.setattr(sm, "_fetch_all_subscriptions", lambda headers: existing)
    monkeypatch.setattr(sm, "reactivate_if_paused", lambda *a, **k: None)

    def rec(verb, status):
        def _call(url, **kwargs):
            calls.append((verb, url))
            return _resp(status)
        return _call

    with patch.object(sm.requests, "post", side_effect=rec("post", 201)), \
         patch.object(sm.requests, "patch", side_effect=rec("patch", 204)), \
         patch.object(sm.requests, "delete", side_effect=rec("delete", 204)):
        sm._ensure_tenant_subscriptions("tenant-a")
    return calls


def test_a_retired_subscription_is_deleted(monkeypatch):
    retired_id = "urn:ngsi-ld:Subscription:entity-manager:retired"
    calls = _run(monkeypatch, _declared() + [_retired(retired_id)])
    assert calls == [("delete", f"{sm.ORION_URL}/ngsi-ld/v1/subscriptions/{retired_id}")]


def test_every_copy_of_a_retired_subscription_is_deleted(monkeypatch):
    """Copies from before ids were deterministic share the description, not the id."""
    ids = ["urn:ngsi-ld:Subscription:old-1", "urn:ngsi-ld:Subscription:old-2"]
    calls = _run(monkeypatch, _declared() + [_retired(i) for i in ids])
    assert sorted(url.rsplit("/", 1)[-1] for verb, url in calls if verb == "delete") == ids
    assert all(verb == "delete" for verb, _ in calls)


def test_a_retired_subscription_goes_even_when_a_declared_one_cannot_be_created(monkeypatch):
    retired_id = "urn:ngsi-ld:Subscription:entity-manager:retired"
    calls = []
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "")
    monkeypatch.setattr(sm, "_fetch_all_subscriptions", lambda headers: _declared()[1:] + [_retired(retired_id)])
    monkeypatch.setattr(sm, "reactivate_if_paused", lambda *a, **k: None)

    def failing_post(url, **kwargs):
        calls.append(("post", url))
        raise sm.requests.ConnectionError("broker unreachable")

    def delete(url, **kwargs):
        calls.append(("delete", url))
        return _resp(204)

    with patch.object(sm.requests, "post", side_effect=failing_post), \
         patch.object(sm.requests, "delete", side_effect=delete):
        sm._ensure_tenant_subscriptions("tenant-a")
    assert ("delete", f"{sm.ORION_URL}/ngsi-ld/v1/subscriptions/{retired_id}") in calls
    assert any(verb == "post" for verb, _ in calls)


def test_a_failed_delete_does_not_stop_the_others(monkeypatch):
    ids = ["urn:ngsi-ld:Subscription:old-1", "urn:ngsi-ld:Subscription:old-2"]
    tried = []
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "")
    monkeypatch.setattr(sm, "_fetch_all_subscriptions", lambda headers: _declared() + [_retired(i) for i in ids])
    monkeypatch.setattr(sm, "reactivate_if_paused", lambda *a, **k: None)

    def failing_delete(url, **kwargs):
        tried.append(url.rsplit("/", 1)[-1])
        raise sm.requests.ConnectionError("broker unreachable")

    with patch.object(sm.requests, "delete", side_effect=failing_delete):
        sm._ensure_tenant_subscriptions("tenant-a")
    assert sorted(tried) == ids


def test_nothing_is_deleted_without_a_retired_subscription(monkeypatch):
    foreign = {
        "id": "urn:ngsi-ld:Subscription:someone-else",
        "description": "Another service - its own subscription",
        "notification": {"endpoint": {"uri": "http://other-service/notify"}},
    }
    assert _run(monkeypatch, _declared() + [foreign]) == []


def test_a_retired_subscription_without_id_is_skipped(monkeypatch):
    broken = _retired(None)
    assert _run(monkeypatch, _declared() + [broken]) == []


def test_a_description_is_never_both_declared_and_retired():
    declared = {sub_def["description"] for sub_def in sm.SUBSCRIPTIONS}
    assert sm.RETIRED_DESCRIPTIONS
    assert not declared & sm.RETIRED_DESCRIPTIONS
