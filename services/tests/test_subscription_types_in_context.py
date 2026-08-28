"""Every entity type any service subscribes to must be a term of the platform @context.

A subscription stores its entity type *already expanded*, at creation time, and never
re-expands it. A type missing from the context therefore expands against the default
vocabulary and the subscription silently matches nothing — no error, no notification,
ever. The same happens to subscriptions already in the broker when a term is renamed,
which is why this belongs in CI rather than in an incident report.
"""

import json

import pytest

from ._subscription_managers import CONTEXT_FILE, KNOWN, MANAGERS, load, service_id


def _context_terms() -> set:
    doc = json.loads(CONTEXT_FILE.read_text())
    ctx = doc.get("@context", doc)
    terms: dict = {}
    if isinstance(ctx, list):
        for part in ctx:
            if isinstance(part, dict):
                terms.update(part)
    else:
        terms = ctx
    return {k for k in terms if isinstance(k, str) and not k.startswith("@")}


def _subscribed_types(module) -> set:
    types = set()
    for sub in getattr(module, "SUBSCRIPTIONS", []):
        for entity in sub.get("entities", []):
            entity_type = entity.get("type")
            if entity_type:
                types.add(entity_type)
    return types


def test_the_discovery_actually_found_the_known_managers():
    """Guards every parametrized assertion from passing on an empty glob."""
    found = {service_id(p) for p in MANAGERS}
    assert KNOWN <= found, found


@pytest.mark.parametrize("path", MANAGERS, ids=service_id)
def test_every_subscribed_type_is_defined_in_the_platform_context(path):
    types = _subscribed_types(load(path))
    assert types, f"{path} declares no subscription types"
    missing = sorted(types - _context_terms())
    assert not missing, (
        f"{service_id(path)} subscribes to {missing}, which the platform @context does "
        "not define. Orion expands unknown terms against the default vocabulary, so "
        "these subscriptions would never match any entity."
    )
