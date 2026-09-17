"""Reconciliation identifies a subscription by its id, and never leaves a gap.

Two failures shaped the rules here:

* Matching on `description` made the description the identity. The id is
  derived from it, so editing a description minted a new id and abandoned the
  old subscription, still registered and no longer reconciled by anyone. That
  is how the vocabulary migration left hundreds of subscriptions that can
  never fire.
* Refreshing a stale subscription by deleting it and creating it again leaves
  an interval with no subscription at all, and every notification raised in
  that interval is lost with no trace.
"""

from unittest.mock import MagicMock, patch

import pytest

from ._subscription_managers import MANAGERS, load, service_id

CANONICAL_DESC_INDEX = 0


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = [] if body is None else body
    r.text = ""
    return r


def _sub(module, sub_id, receiver_info=True):
    body = {
        "id": sub_id,
        "description": module.SUBSCRIPTIONS[CANONICAL_DESC_INDEX]["description"],
        "notification": {
            "endpoint": {"uri": module.NOTIFICATION_URL, "accept": "application/json"}
        },
    }
    if receiver_info:
        body["notification"]["endpoint"]["receiverInfo"] = [
            {"key": "X-Internal-Service-Secret", "value": "s3cret"}
        ]
    return body


@pytest.fixture
def calls():
    """Record the order of Orion writes across all verbs."""
    return []


def _all_canonical(module, skip_first=False):
    """A healthy, current subscription for every entry the manager declares.

    Every entry must be present or the manager legitimately creates the missing
    ones, and the assertions below would be reading that noise instead of the
    behaviour under test.
    """
    subs = []
    for i, sub_def in enumerate(module.SUBSCRIPTIONS):
        if skip_first and i == CANONICAL_DESC_INDEX:
            continue
        body = _sub(module, module._subscription_id(sub_def))
        body["description"] = sub_def["description"]
        subs.append(body)
    return subs


def _run(module, monkeypatch, calls, existing):
    monkeypatch.setattr(module, "INTERNAL_SERVICE_SECRET", "s3cret")
    monkeypatch.setattr(module, "_fetch_all_subscriptions", lambda headers: existing)
    monkeypatch.setattr(module, "reactivate_if_paused", lambda *a, **k: calls.append(("reactivate",)))

    def rec(verb):
        def _call(url, **kwargs):
            calls.append((verb, url))
            return _resp(201 if verb == "post" else 204)
        return _call

    with patch.object(module.requests, "post", side_effect=rec("post")), \
         patch.object(module.requests, "patch", side_effect=rec("patch")), \
         patch.object(module.requests, "delete", side_effect=rec("delete")):
        module._ensure_tenant_subscriptions("t1")
    return calls


@pytest.mark.parametrize("path", MANAGERS, ids=service_id)
def test_a_stale_subscription_is_patched_never_deleted(path, monkeypatch, calls):
    """The canonical id already exists but predates receiverInfo."""
    module = load(path)
    canonical = module._subscription_id(module.SUBSCRIPTIONS[CANONICAL_DESC_INDEX])
    existing = _all_canonical(module, skip_first=True)
    existing.append(_sub(module, canonical, receiver_info=False))
    _run(module, monkeypatch, calls, existing)

    verbs = [c[0] for c in calls]
    assert "patch" in verbs, f"stale subscription was not refreshed in place: {calls}"
    assert "delete" not in verbs, (
        f"deleting the canonical subscription reopens the gap this closes: {calls}"
    )


@pytest.mark.parametrize("path", MANAGERS, ids=service_id)
def test_leftovers_go_only_after_the_canonical_one_exists(path, monkeypatch, calls):
    """A subscription under an older id must outlive the create, not precede it."""
    module = load(path)
    leftover_id = "urn:ngsi-ld:Subscription:from-an-older-description"
    existing = _all_canonical(module, skip_first=True)
    existing.append(_sub(module, leftover_id))
    _run(module, monkeypatch, calls, existing)

    verbs = [c[0] for c in calls]
    assert "post" in verbs and "delete" in verbs, calls
    assert verbs.index("post") < verbs.index("delete"), (
        f"the leftover was deleted before the replacement existed: {calls}"
    )


@pytest.mark.parametrize("path", MANAGERS, ids=service_id)
def test_a_healthy_subscription_is_left_alone(path, monkeypatch, calls):
    module = load(path)
    canonical = module._subscription_id(module.SUBSCRIPTIONS[CANONICAL_DESC_INDEX])
    _run(module, monkeypatch, calls, _all_canonical(module))

    verbs = [c[0] for c in calls]
    assert "post" not in verbs and "patch" not in verbs and "delete" not in verbs, (
        f"a healthy subscription was rewritten for nothing: {calls}"
    )
    assert ("reactivate",) in calls, "a paused subscription would stay paused"


@pytest.mark.parametrize("path", MANAGERS, ids=service_id)
def test_identity_is_the_id_not_the_description(path, monkeypatch, calls):
    """Same description, different id: the id decides, so this is a leftover."""
    module = load(path)
    canonical = module._subscription_id(module.SUBSCRIPTIONS[CANONICAL_DESC_INDEX])
    existing = _all_canonical(module)
    existing.append(_sub(module, "urn:ngsi-ld:Subscription:other"))
    _run(module, monkeypatch, calls, existing)

    deleted = [c[1] for c in calls if c[0] == "delete"]
    assert len(deleted) == 1 and deleted[0].endswith("urn:ngsi-ld:Subscription:other"), (
        f"the canonical subscription must survive, only the other id goes: {calls}"
    )
