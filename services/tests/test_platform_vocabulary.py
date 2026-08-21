"""Invariants for the platform NGSI-LD @context.

The platform vocabulary had fragmented into four self-namespaces:

  https://nekazari.io/vocab       72 terms — the domain was never registered (NXDOMAIN),
                                   so anyone could claim it and define semantics there
  https://nkz-os.org/sdm-proposals  20 terms
  nkz: -> nekazari.robotika.cloud  82 terms — a vocabulary pinned to a deployment hostname
  smart-data-models.github.io       8 terms — a URL shape that 404s, some pointing at a
                                   JSON Schema rather than the type IRI

Consolidated onto one: https://nkz-os.org/ns/ (prefix nkz:).

These are not style rules. An entity's type and attribute names are stored *expanded* in
Orion-LD, so changing a term's IRI orphans everything already written under the old one.
A second self-namespace means a second migration.
"""

import json
import pathlib

import pytest

CONTEXT_FILE = pathlib.Path(__file__).resolve().parents[2] / "config" / "ngsi-ld-context.json"
PLATFORM_NS = "https://nkz-os.org/ns/"

# Domains that must never appear again, and why.
BANNED = {
    "nekazari.io": "unregistered domain (NXDOMAIN) — anyone could claim the vocabulary",
    "smart-data-models.github.io": "returns 404; the canonical host is smartdatamodels.org",
    "nkz-os.org/sdm-proposals": "second self-namespace — everything platform-owned lives under /ns/",
}

# Terms that have an official Smart Data Model IRI and must use it.
OFFICIAL = {
    "WeatherObserved": "https://smartdatamodels.org/dataModel.Weather/WeatherObserved",
    "AgriParcelOperation": "https://smartdatamodels.org/dataModel.Agrifood/AgriParcelOperation",
    "Alert": "https://smartdatamodels.org/dataModel.Alert/Alert",
    "AgriCrop": "https://smartdatamodels.org/dataModel.Agrifood/AgriCrop",
    "EOProduct": "https://smartdatamodels.org/dataModel.SatelliteImagery/EOProduct",
    "hasAgriParcel": "https://smartdatamodels.org/dataModel.Agrifood/hasAgriParcel",
}

# Deprecated ref<Type> spellings kept as aliases during the migration window. Each must
# expand to exactly the same IRI as its canonical partner, or the two stop being the same
# attribute in the broker.
ALIASES = [
    ("refTenant", "belongsTo"),
    ("refParcel", "locatedAt"),
    ("refDeviceProfile", "hasDeviceProfile"),
    ("refWeatherStation", "observes"),
    ("refEquipment", "usesEquipment"),
    ("refTractor", "usesTractor"),
    ("refImplement", "usesImplement"),
    ("refTrialSite", "hasTrialSite"),
    ("refArticleSource", "hasArticleSource"),
    ("refSoilSample", "hasSoilSample"),
    ("refSourceOperation", "derivedFrom"),
    ("refVegetationIndex", "hasVegetationIndex"),
]

VALID_TERM_KEYS = {
    "@id", "@type", "@container", "@context", "@language", "@reverse",
    "@prefix", "@protected", "@nest", "@index", "@direction",
}


@pytest.fixture(scope="module")
def ctx():
    doc = json.loads(CONTEXT_FILE.read_text())
    return doc["@context"][1]


def _iri(v):
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("@id", "")
    return ""


def test_context_file_is_valid_json(ctx):
    assert len(ctx) > 100


def test_prefix_points_at_the_platform_namespace(ctx):
    assert ctx["nkz"] == PLATFORM_NS


@pytest.mark.parametrize("domain,reason", sorted(BANNED.items()))
def test_banned_namespace_is_absent(ctx, domain, reason):
    offenders = [k for k, v in ctx.items() if k != "_comment" and domain in _iri(v)]
    assert not offenders, f"{domain}: {reason}\nterms: {offenders[:10]}"


def test_exactly_one_self_namespace(ctx):
    """Every platform-owned IRI must sit under the single namespace."""
    selfish = set()
    for term, v in ctx.items():
        if term == "_comment":
            continue
        s = _iri(v)
        if "nkz-os.org" in s or "nekazari" in s:
            selfish.add(s.rsplit("/", 1)[0] + "/")
    assert selfish <= {PLATFORM_NS}, f"more than one self-namespace: {selfish}"


@pytest.mark.parametrize("term,expected", sorted(OFFICIAL.items()))
def test_official_sdm_terms_use_the_official_iri(ctx, term, expected):
    assert _iri(ctx[term]) == expected


@pytest.mark.parametrize("deprecated,canonical", ALIASES)
def test_ref_aliases_still_match_their_canonical_term(ctx, deprecated, canonical):
    assert _iri(ctx[deprecated]) == _iri(ctx[canonical]), (
        f"{deprecated} and {canonical} must expand to the same IRI while the "
        "ref<Type> migration window is open"
    )


def test_term_definitions_use_only_jsonld_keywords(ctx):
    """The defect that made the cue context invalid: made-up keys in a term definition."""
    bad = []
    for term, v in ctx.items():
        if not isinstance(v, dict):
            continue
        for key in v:
            # _comment is a pre-existing, tolerated annotation across this file.
            if key == "_comment":
                continue
            if key not in VALID_TERM_KEYS:
                bad.append(f"{term}.{key}")
    assert not bad, f"invalid term-definition keys: {bad}"


def test_no_unit_codes_in_term_definitions(ctx):
    """unitCode belongs on the attribute instance, never in the vocabulary."""
    bad = [k for k, v in ctx.items() if isinstance(v, dict) and "unitCode" in v]
    assert not bad, bad


def test_relationship_terms_declare_id_typing(ctx):
    """A term whose @type is set must use a JSON-LD-legal value."""
    legal = {"@id", "@vocab", "@json", "@none"}
    bad = []
    for term, v in ctx.items():
        if not isinstance(v, dict) or "@type" not in v:
            continue
        t = v["@type"]
        if t not in legal and not (isinstance(t, str) and (t.startswith("http") or ":" in t)):
            bad.append(f"{term}: @type={t!r}")
    assert not bad, bad
