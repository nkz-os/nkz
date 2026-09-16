"""The reconciler must migrate subscriptions onto the auth'd, single-copy contract.

Orion injects per-subscription headers listed in `notification.endpoint.receiverInfo`.
Internal notification receivers are being closed behind `X-Internal-Service-Secret`, so
every creator must (a) give subscriptions a deterministic id so concurrent creates can
not duplicate them and (b) carry receiverInfo so the delivery authenticates. Existing
subscriptions predate both, so the reconciler replaces stale or duplicated copies with a
single auth'd one.
"""

from unittest.mock import ANY, MagicMock, patch

import pytest

from ._subscription_managers import MANAGERS, load, service_id


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = [] if body is None else body
    r.text = ""
    return r


def _existing_sub(module, receiver_info=False):
    body = {
        "id": "urn:ngsi-ld:Subscription:legacy-random-id",
        "description": module.SUBSCRIPTIONS[0]["description"],
        "notification": {
            "endpoint": {"uri": module.NOTIFICATION_URL, "accept": "application/json"}
        },
    }
    if receiver_info:
        body["notification"]["endpoint"]["receiverInfo"] = [
            {"key": "X-Internal-Service-Secret", "value": "s3cret"}
        ]
    return body


@pytest.mark.parametrize("path", MANAGERS, ids=service_id)
def test_subscription_body_has_deterministic_id_and_receiver_info(path, monkeypatch):
    module = load(path)
    monkeypatch.setattr(module, "INTERNAL_SERVICE_SECRET", "s3cret")
    body = module._subscription_body(module.SUBSCRIPTIONS[0])
    assert body["id"].startswith("urn:ngsi-ld:Subscription:")
    assert body["notification"]["endpoint"]["receiverInfo"] == [
        {"key": "X-Internal-Service-Secret", "value": "s3cret"}
    ]


@pytest.mark.parametrize("path", MANAGERS, ids=service_id)
def test_matches_are_stale_semantics(path, monkeypatch):
    module = load(path)
    fresh = [_existing_sub(module, receiver_info=True)]
    stale = [_existing_sub(module, receiver_info=False)]

    monkeypatch.setattr(module, "INTERNAL_SERVICE_SECRET", "s3cret")
    assert module._matches_are_stale(fresh) is False
    assert module._matches_are_stale(stale) is True
    assert module._matches_are_stale(fresh + stale) is True  # duplicates
    assert module._matches_are_stale([]) is False

    monkeypatch.setattr(module, "INTERNAL_SERVICE_SECRET", "")
    assert module._matches_are_stale(stale) is False  # no secret -> only dupes stale


@pytest.mark.parametrize("path", MANAGERS, ids=service_id)
def test_stale_subscription_is_replaced_with_authd_copy(path, monkeypatch):
    module = load(path)
    monkeypatch.setattr(module, "INTERNAL_SERVICE_SECRET", "s3cret")
    old = _existing_sub(module, receiver_info=False)
    with patch.object(module.requests, "get", return_value=_resp(200, [old])), \
            patch.object(module.requests, "delete", return_value=_resp(204)) as delete, \
            patch.object(module.requests, "post", return_value=_resp(201)) as post:
        module._ensure_tenant_subscriptions("montiko")

    delete.assert_called_once_with(
        f"{module.ORION_URL}/ngsi-ld/v1/subscriptions/{old['id']}",
        headers=ANY, timeout=30,
    )
    assert post.call_count == len(module.SUBSCRIPTIONS)
    created = post.call_args_list[0].kwargs["json"]
    assert created["id"].startswith("urn:ngsi-ld:Subscription:")
    assert created["notification"]["endpoint"]["receiverInfo"] == [
        {"key": "X-Internal-Service-Secret", "value": "s3cret"}
    ]
