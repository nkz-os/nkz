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
