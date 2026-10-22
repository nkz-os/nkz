"""Crop-cycle terms resolve in the platform @context (no default-vocab writes)."""
import json
import os

CTX = os.path.join(os.path.dirname(__file__), "..", "..", "config", "ngsi-ld-context.json")
TERMS = [
    "cropLifecycle", "actualPlantingDate", "actualTerminationDate", "plantingDateSource",
    "cropRole", "cropSegmentSeq", "expectedTerminationDate", "terminationDate",
    "sowingWindowStart", "sowingWindowEnd", "terminationMethod", "cropSeason",
]


def _terms():
    ctx = json.load(open(CTX))["@context"]
    merged = {}
    for part in ctx if isinstance(ctx, list) else [ctx]:
        if isinstance(part, dict):
            merged.update(part)
    return merged


def test_crop_cycle_terms_in_nkz_namespace():
    t = _terms()
    for term in TERMS:
        v = t.get(term)
        v = v.get("@id") if isinstance(v, dict) else v
        assert v in (f"https://nkz-os.org/ns/{term}", f"nkz:{term}"), term


def test_retired_and_shared_terms_not_added():
    t = _terms()
    for term in ("cropSeasonStart", "cropSeasonEnd", "provenance", "role", "seq"):
        assert term not in t, term
