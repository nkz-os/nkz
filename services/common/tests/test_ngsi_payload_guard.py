"""Tests for common.ngsi_payload_guard."""

from __future__ import annotations

import importlib.util
import os

_path = os.path.join(os.path.dirname(__file__), "..", "ngsi_payload_guard.py")
_spec = importlib.util.spec_from_file_location("ngsi_payload_guard", _path)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

shorthand_null_attributes = _mod.shorthand_null_attributes


def test_flags_top_level_shorthand_null_token():
    body = {"refAgriParcel": "urn:ngsi-ld:null", "name": {"type": "Property", "value": "x"}}
    assert shorthand_null_attributes(body) == ["refAgriParcel"]


def test_flags_every_offending_attribute_in_order():
    body = {"a": "urn:ngsi-ld:null", "b": 1, "c": "urn:ngsi-ld:null"}
    assert shorthand_null_attributes(body) == ["a", "c"]


def test_ignores_normalized_attribute_objects():
    body = {
        "name": {"type": "Property", "value": "urn:ngsi-ld:null"},
        "refAgriParcel": {"type": "Relationship", "object": "urn:ngsi-ld:null"},
    }
    assert shorthand_null_attributes(body) == []


def test_ignores_context_key():
    assert shorthand_null_attributes({"@context": "urn:ngsi-ld:null"}) == []


def test_ignores_other_shorthand_values():
    assert shorthand_null_attributes({"name": "hello", "n": 5, "f": None}) == []


def test_non_object_bodies_have_no_attributes():
    assert shorthand_null_attributes(None) == []
    assert shorthand_null_attributes([{"a": "urn:ngsi-ld:null"}]) == []
    assert shorthand_null_attributes("urn:ngsi-ld:null") == []
