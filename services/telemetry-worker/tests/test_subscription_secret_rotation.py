"""After a secret rotation the workers must replace subscriptions that still
carry the old value, not only the ones with no receiverInfo at all."""
import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import telemetry_worker.subscription_manager as sm  # noqa: E402


def _match(secret_value):
    endpoint = {"uri": "http://svc:80/notify"}
    if secret_value is not None:
        endpoint["receiverInfo"] = [{"key": "X-Internal-Service-Secret", "value": secret_value}]
    return {"id": "urn:ngsi-ld:Subscription:x", "notification": {"endpoint": endpoint}}


def test_old_secret_is_stale(monkeypatch):
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "new")
    assert sm._matches_are_stale([_match("old")]) is True


def test_current_secret_is_fresh(monkeypatch):
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "new")
    assert sm._matches_are_stale([_match("new")]) is False


def test_missing_receiver_info_is_stale(monkeypatch):
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "new")
    assert sm._matches_are_stale([_match(None)]) is True


def test_no_secret_configured_never_stale(monkeypatch):
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "")
    assert sm._matches_are_stale([_match("old")]) is False


def test_duplicates_are_stale(monkeypatch):
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "new")
    assert sm._matches_are_stale([_match("new"), _match("new")]) is True


# --- derived notification tokens -------------------------------------------

import pytest  # noqa: E402

from nkz_platform_sdk.notify_auth import TOKEN_HEADER, notification_token  # noqa: E402


def _derived(tenant="tenant-a", secret="new"):
    return notification_token(secret, tenant, sm.RECEIVER_HOST)


def _match_token(value):
    return {
        "id": "urn:ngsi-ld:Subscription:x",
        "notification": {
            "endpoint": {
                "uri": "http://svc:80/notify",
                "receiverInfo": [{"key": TOKEN_HEADER, "value": value}],
            }
        },
    }


def test_derived_body_carries_the_tenant_token_and_no_master(monkeypatch):
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "new")
    monkeypatch.setenv("NKZ_NOTIFY_DERIVED_TOKENS", "true")
    info_a = sm._subscription_body(sm.SUBSCRIPTIONS[0], "tenant-a")["notification"]["endpoint"]["receiverInfo"]
    info_b = sm._subscription_body(sm.SUBSCRIPTIONS[0], "tenant-b")["notification"]["endpoint"]["receiverInfo"]
    assert info_a == [{"key": TOKEN_HEADER, "value": _derived("tenant-a")}]
    assert info_b == [{"key": TOKEN_HEADER, "value": _derived("tenant-b")}]


def test_derived_body_needs_a_tenant(monkeypatch):
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "new")
    monkeypatch.setenv("NKZ_NOTIFY_DERIVED_TOKENS", "true")
    with pytest.raises(ValueError):
        sm._subscription_body(sm.SUBSCRIPTIONS[0])


def test_derived_mode_stale_semantics(monkeypatch):
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "new")
    monkeypatch.setenv("NKZ_NOTIFY_DERIVED_TOKENS", "true")
    assert sm._matches_are_stale([_match("new")], "tenant-a") is True  # still the master
    assert sm._matches_are_stale([_match(None)], "tenant-a") is True  # nothing at all
    assert sm._matches_are_stale([_match_token(_derived("tenant-b"))], "tenant-a") is True
    assert sm._matches_are_stale([_match_token(_derived(secret="old"))], "tenant-a") is True
    assert sm._matches_are_stale([_match_token(_derived())], "tenant-a") is False
    both = _match_token(_derived())
    both["notification"]["endpoint"]["receiverInfo"].append(
        {"key": "X-Internal-Service-Secret", "value": "new"}
    )
    assert sm._matches_are_stale([both], "tenant-a") is True  # the master must go


def test_legacy_mode_is_stale_once_a_token_replaced_the_master(monkeypatch):
    # Switching the flag back off re-emits the master: a token-only copy is stale.
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", "new")
    monkeypatch.delenv("NKZ_NOTIFY_DERIVED_TOKENS", raising=False)
    assert sm._matches_are_stale([_match_token(_derived())], "tenant-a") is True
