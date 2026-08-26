"""Every entity type the worker subscribes to must be a term of the platform
@context.

A subscription stores its entity type already expanded, at creation time, and
never re-expands it. So a type that is missing from the context is expanded to
the default vocabulary and the subscription silently matches nothing — no error,
no notification, ever. The same happens to subscriptions already in the broker
when a term is renamed in the context, which is why this contract is worth
asserting in CI rather than discovering in production.
"""

import json
import os
import sys

# ── Path setup (mirrors other telemetry-worker tests) ──────────────────────
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_REPO_ROOT = os.path.normpath(os.path.join(_SERVICES_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")

for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

import telemetry_worker.subscription_manager as sm

CONTEXT_PATH = os.path.join(_REPO_ROOT, "config", "ngsi-ld-context.json")


def _context_terms() -> set:
    with open(CONTEXT_PATH, encoding="utf-8") as fh:
        doc = json.load(fh)
    ctx = doc.get("@context", doc)
    terms: dict = {}
    if isinstance(ctx, list):
        for part in ctx:
            if isinstance(part, dict):
                terms.update(part)
    else:
        terms = ctx
    return {k for k in terms if isinstance(k, str) and not k.startswith("@")}


def _subscribed_types() -> set:
    types = set()
    for sub in sm.SUBSCRIPTIONS:
        for entity in sub.get("entities", []):
            entity_type = entity.get("type")
            if entity_type:
                types.add(entity_type)
    return types


@pytest.mark.skipif(
    not os.path.isfile(CONTEXT_PATH), reason="platform @context not in this checkout"
)
def test_every_subscribed_type_is_defined_in_the_platform_context():
    missing = sorted(_subscribed_types() - _context_terms())
    assert not missing, (
        f"subscribed to {missing}, which the platform @context does not define. "
        "Orion expands unknown terms to the default vocabulary, so these "
        "subscriptions would never match any entity."
    )


def test_there_is_at_least_one_subscribed_type():
    """Guards the assertion above from passing on an empty set."""
    assert _subscribed_types()
