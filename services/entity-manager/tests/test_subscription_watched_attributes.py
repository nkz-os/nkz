"""A change of `watchedAttributes` reaches the subscription already registered, and only once.

Orion-LD compacts what it returns with the context of the read: normally the short names that
were declared, IRIs when that context lacks a term.
"""
import copy
import json
import os
import sys
from collections import defaultdict
from unittest.mock import MagicMock, patch

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tests.telemetry_fakes  # noqa: E402,F401,I001  (stubs first)
from tests.real_common import seed_real_common  # noqa: E402

# The stub of `common` is not a package: the module is found only if it is already loaded.
seed_real_common(("subscription_health",))

import subscription_manager as sm  # noqa: E402

CONTEXT_FILE = os.path.normpath(os.path.join(_SVC_DIR, "..", "..", "config", "ngsi-ld-context.json"))
DEVICE = next(s for s in sm.SUBSCRIPTIONS if s["entities"] == [{"type": "Device"}])
COMMAND = next(s for s in sm.SUBSCRIPTIONS if s["entities"] == [{"type": "DeviceCommand"}])
ADDED = ["refDeviceModel", "dateInstalled", "deviceState"]
BEFORE = ["name", "externalId", "profileCode", "controlledAsset", "parcelId", "location", "status",
          "metadata", "isUnderCanopy", "altitudeMeters", "installedAt"]
WATCHED = BEFORE + ADDED


def _stored(sub_def, tenant="acmefarm", **over):
    """The subscription as the broker holds it when it is healthy and current."""
    return {**copy.deepcopy(sm._subscription_body(sub_def, tenant)), "status": "active", **over}


def _without(sub, key):
    return {k: v for k, v in sub.items() if k != key}


class Broker:
    """Stored subscriptions; records every write and applies the updates."""

    def __init__(self, *subs):
        self.subs = {s["id"]: s for s in subs}
        self.calls = []

    def _resp(self, status):
        return MagicMock(status_code=status, text="")

    def patch(self, url, json=None, **kw):
        sub_id = url.rsplit("/", 1)[-1]
        self.calls.append(("patch", sub_id, json))
        self.subs[sub_id].update(copy.deepcopy(json))
        return self._resp(204)

    def post(self, url, json=None, **kw):
        self.calls.append(("post", json["id"], json))
        return self._resp(201)

    def delete(self, url, **kw):
        self.calls.append(("delete", url.rsplit("/", 1)[-1], None))
        return self._resp(204)


def _reconcile(broker, monkeypatch, secret="s3cret", times=1):
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", secret)
    monkeypatch.delenv("NKZ_NOTIFY_DERIVED_TOKENS", raising=False)
    monkeypatch.setattr(sm, "_fetch_all_subscriptions", lambda headers: list(broker.subs.values()))
    monkeypatch.setattr(sm, "reactivate_if_paused", lambda *a, **k: None)
    with patch.object(sm.requests, "post", side_effect=broker.post), \
         patch.object(sm.requests, "patch", side_effect=broker.patch), \
         patch.object(sm.requests, "delete", side_effect=broker.delete):
        for _ in range(times):
            sm._ensure_tenant_subscriptions("acmefarm")
    return broker.calls


@pytest.fixture(params=["s3cret", ""], ids=["with-secret", "without-secret"])
def secret(request, monkeypatch):
    monkeypatch.setattr(sm, "INTERNAL_SERVICE_SECRET", request.param)
    return request.param


def test_the_old_and_the_official_attributes_are_watched():
    assert DEVICE["watchedAttributes"] == WATCHED


@pytest.mark.parametrize("stored", [BEFORE, BEFORE[:3], [], WATCHED + ["soilMoisture"]],
                         ids=["before", "fewer", "empty", "more"])
def test_an_old_set_is_updated_in_place_once(stored, secret, monkeypatch):
    broker = Broker(_stored(DEVICE, watchedAttributes=stored), _stored(COMMAND))
    calls = _reconcile(broker, monkeypatch, secret, times=3)
    (verb, sub_id, body), = calls
    assert (verb, sub_id) == ("patch", sm._subscription_id(DEVICE))
    assert body["watchedAttributes"] == WATCHED
    assert body == _without(sm._subscription_body(DEVICE, "acmefarm"), "id")


def test_a_subscription_that_watches_everything_is_updated(secret, monkeypatch):
    broker = Broker(_without(_stored(DEVICE), "watchedAttributes"), _stored(COMMAND))
    (verb, _, body), = _reconcile(broker, monkeypatch, secret, times=2)
    assert verb == "patch" and body["watchedAttributes"] == WATCHED


@pytest.mark.parametrize("stored", [
    WATCHED, WATCHED[::-1], WATCHED + WATCHED[:2],
    [f"https://smartdatamodels.org/dataModel.Device/{n}" for n in WATCHED],
    [f"https://example.org/vocabulary#{n}" for n in WATCHED],
    BEFORE + [f"https://smartdatamodels.org/dataModel.Device/{n}" for n in ADDED],
], ids=["same", "other-order", "repeated", "iris", "hash-iris", "mixed"])
def test_an_equal_set_is_left_alone(stored, secret, monkeypatch):
    broker = Broker(_stored(DEVICE, watchedAttributes=stored), _stored(COMMAND))
    assert _reconcile(broker, monkeypatch, secret, times=2) == []


@pytest.mark.parametrize("stored", [{}, {"watchedAttributes": []}, {"watchedAttributes": ["commandId"]}],
                         ids=["none", "empty", "some"])
def test_a_subscription_that_declares_none_is_never_stale_for_it(stored, secret, monkeypatch):
    """A PATCH without the list would not remove it, so it would be sent for ever."""
    assert "watchedAttributes" not in COMMAND
    broker = Broker(_stored(DEVICE), _stored(COMMAND, **stored))
    assert _reconcile(broker, monkeypatch, secret) == []


def test_the_auth_header_is_still_a_reason(monkeypatch):
    broker = Broker(_stored(DEVICE), _stored(COMMAND))
    (verb, sub_id, body), (other, _, _) = _reconcile(broker, monkeypatch, "rotated", times=2)
    assert (verb, other, sub_id) == ("patch", "patch", sm._subscription_id(DEVICE))
    assert body["notification"]["endpoint"]["receiverInfo"] == [
        {"key": sm.MASTER_HEADER, "value": "rotated"}]


# ---- no perpetual update ----------------------------------------------------------
# The stored names come back compacted. If two terms of the platform context share an IRI, Orion
# returns the LAST of them: a watched name would come back as another and never look equal.

DEFAULT_CONTEXT = "https://uri.etsi.org/ngsi-ld/default-context/"


def _platform_iris():
    """Term -> full IRI of every term of the platform context."""
    with open(CONTEXT_FILE, encoding="utf-8") as handle:
        contexts = json.load(handle)["@context"]
    terms = {t: v for part in contexts if isinstance(part, dict) for t, v in part.items()}
    raw = {t: v if isinstance(v, str) else v.get("@id") for t, v in terms.items()}

    def _expand(value):
        prefix, sep, rest = (value or "").partition(":")
        if sep and not rest.startswith("//") and prefix in raw:
            return _expand(raw[prefix]) + rest
        return value

    return {t: _expand(v) for t, v in raw.items()}


def test_no_watched_name_shares_its_iri_with_another_term():
    iris = _platform_iris()
    assert iris["name"] == "https://schema.org/name"  # prefixes are expanded
    terms_of = defaultdict(set)
    for term, iri in iris.items():
        terms_of[iri].add(term)
    for name in DEVICE["watchedAttributes"]:
        iri = iris.get(name, DEFAULT_CONTEXT + name)
        assert terms_of.get(iri, set()) <= {name}, f"{name} comes back as one of {terms_of[iri]}"


def test_a_watched_name_is_the_last_part_of_its_iri():
    """Otherwise a stored IRI would not be recognised as that name."""
    iris = _platform_iris()
    for name in DEVICE["watchedAttributes"]:
        assert sm._attribute_name(iris.get(name, DEFAULT_CONTEXT + name)) == name
