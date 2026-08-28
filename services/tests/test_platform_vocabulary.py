"""Invariants for the platform NGSI-LD @context.

The platform vocabulary had fragmented into four self-namespaces:

  https://nekazari.io/vocab       72 terms — the domain was never registered (NXDOMAIN),
                                   so anyone could claim it and define semantics there
  https://nkz-os.org/sdm-proposals  20 terms
  nkz: -> a deployment hostname   82 terms — a vocabulary pinned to where it happens to run
  smart-data-models.github.io       8 terms — a URL shape that 404s, some pointing at a
                                   JSON Schema rather than the type IRI

Consolidated onto one: https://nkz-os.org/ns/ (prefix nkz:).

These are not style rules. An entity's type and attribute names are stored *expanded* in
Orion-LD, so changing a term's IRI orphans everything already written under the old one.
A second self-namespace means a second migration.
"""

import json
import pathlib
from urllib.parse import urlsplit

import pytest

CONTEXT_FILE = pathlib.Path(__file__).resolve().parents[2] / "config" / "ngsi-ld-context.json"
PLATFORM_NS = "https://nkz-os.org/ns/"

# (host, path prefix) pairs that must never appear again, and why. Matched on the parsed
# host, not as substrings: "nkz-os.org" in a URL also matches evil-nkz-os.org.example.net.
BANNED = {
    ("nekazari.io", ""): "unregistered domain (NXDOMAIN) — anyone could claim the vocabulary",
    ("smart-data-models.github.io", ""): "returns 404; the canonical host is smartdatamodels.org",
    ("nkz-os.org", "/sdm-proposals"): "second self-namespace — platform terms live under /ns/",
}

PLATFORM_HOST = "nkz-os.org"
PLATFORM_PATH = "/ns/"

# Terms that have an official Smart Data Model IRI and must use it.
OFFICIAL = {
    "WeatherObserved": "https://smartdatamodels.org/dataModel.Weather/WeatherObserved",
    "AgriParcelOperation": "https://smartdatamodels.org/dataModel.Agrifood/AgriParcelOperation",
    "Alert": "https://smartdatamodels.org/dataModel.Alert/Alert",
    "AgriCrop": "https://smartdatamodels.org/dataModel.Agrifood/AgriCrop",
    "EOProduct": "https://smartdatamodels.org/dataModel.SatelliteImagery/EOProduct",
    "hasAgriParcel": "https://smartdatamodels.org/dataModel.Agrifood/hasAgriParcel",
    "Device": "https://smartdatamodels.org/dataModel.Device/Device",
    "DeviceMeasurement": "https://smartdatamodels.org/dataModel.Device/DeviceMeasurement",
    "ManufacturingMachine": "https://smartdatamodels.org/dataModel.ManufacturingMachine/ManufacturingMachine",
    "controlledAsset": "https://smartdatamodels.org/dataModel.Device/controlledAsset",
    "controlledProperty": "https://smartdatamodels.org/dataModel.Device/controlledProperty",
    "measurementType": "https://smartdatamodels.org/dataModel.Device/measurementType",
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

# Entity types the platform actually writes to the broker. A type missing from the
# context does not fail loudly: NGSI-LD silently expands it against the default
# vocabulary, so it lands as uri.etsi.org/ngsi-ld/default-context/<Type> and stops
# matching anything the platform queries for. Three of these were absent and covered
# most of the entities in the broker.
WRITTEN_TYPES = [
    "AgriParcel", "AgriCrop", "AgriSoil", "AgriSoilExtended", "AgriParcelRecord",
    "AgriParcelOperation", "AgriGreenhouse", "AgriParcelZone", "AgriCropSeason",
    "WeatherObserved", "WeatherAlert", "EOProduct", "VegetationIndex",
    "CropHealthAssessment", "CropHealthZoneAssessment", "CropAdvisory",
    "RiskAssessment", "DiseaseRiskAssessment", "CarbonStock", "CarbonAssessment",
    "CarbonCalculationRun", "BaselineScenario", "ProjectScenario", "CompostRecipe",
    "SoilSamplingPoint", "SoilSurvey", "SoilDerivedRaster", "ElevationSource",
    "DataProcessingJob", "DeviceProfile", "DeviceCommand", "AgriSensor",
    "WaterStorage", "OpenChannelFlow", "AgriCropDeclaration", "SigpacEnclosure",
    "AgriPest", "AgriFertilize", "Alert",
    "Device", "DeviceMeasurement", "ManufacturingMachine",
]


VALID_TERM_KEYS = {
    "@id", "@type", "@container", "@context", "@language", "@reverse",
    "@prefix", "@protected", "@nest", "@index", "@direction",
}


@pytest.fixture(scope="module")
def ctx():
    doc = json.loads(CONTEXT_FILE.read_text())
    return doc["@context"][1]


def _host_and_path(iri: str):
    """Split an absolute IRI into (host, path). Non-absolute IRIs yield ("", "")."""
    parsed = urlsplit(iri)
    if parsed.scheme not in ("http", "https"):
        return "", ""
    return parsed.hostname or "", parsed.path


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


@pytest.mark.parametrize("banned,reason", sorted(BANNED.items()))
def test_banned_namespace_is_absent(ctx, banned, reason):
    host, path_prefix = banned
    offenders = []
    for term, v in ctx.items():
        if term == "_comment":
            continue
        h, path = _host_and_path(_iri(v))
        if h == host and path.startswith(path_prefix):
            offenders.append(term)
    assert not offenders, f"{host}{path_prefix}: {reason}\nterms: {offenders[:10]}"


def test_exactly_one_self_namespace(ctx):
    """Every platform-owned IRI must sit under the single namespace.

    Host is compared exactly. A substring test would both miss a lookalike host and
    accept one, which is how vocabularies quietly fork in the first place.
    """
    stray = {}
    for term, v in ctx.items():
        if term == "_comment":
            continue
        iri = _iri(v)
        host, path = _host_and_path(iri)
        if host == PLATFORM_HOST and not path.startswith(PLATFORM_PATH):
            stray[term] = iri
    assert not stray, f"platform IRIs outside {PLATFORM_NS}: {stray}"


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


@pytest.mark.parametrize("entity_type", WRITTEN_TYPES)
def test_written_type_is_defined(ctx, entity_type):
    """Every type the platform writes must map to a real IRI.

    An undefined type is not an error at write time — it expands against the default
    vocabulary and is stored under uri.etsi.org/ngsi-ld/default-context/<Type>, where
    nothing that queries with this context will find it.
    """
    assert entity_type in ctx, (
        f"{entity_type} is written to the broker but absent from the context; "
        "it would be stored under the default vocabulary"
    )
    iri = _iri(ctx[entity_type])
    assert iri and not iri.startswith("https://uri.etsi.org/ngsi-ld/default-context/"), iri


# Atributos del modelo IoT canónico. Un atributo ausente del contexto no falla al escribir:
# expande contra el vocabulario por defecto, así que la entidad se guarda con el tipo correcto
# y los campos en otro namespace, invisibles para quien consulte con este contexto.
IOT_ATTRIBUTES = [
    "controlledAsset", "controlledProperty", "measurementType",
    "numValue", "textValue", "outlier",
    "serialNumber", "deviceState", "machineModel",
]


@pytest.mark.parametrize("attribute", IOT_ATTRIBUTES)
def test_iot_attribute_is_defined(ctx, attribute):
    assert attribute in ctx, (
        f"{attribute} forma parte del modelo IoT canónico pero no está en el contexto; "
        "expandiría contra el vocabulario por defecto"
    )
    iri = _iri(ctx[attribute])
    assert iri and not iri.startswith("https://uri.etsi.org/ngsi-ld/default-context/"), iri


def test_unit_is_not_repurposed_as_a_measurement_unit(ctx):
    """`unit` está ocupado por saref:Unit, que es una clase, no una unidad de medida.

    La unidad de una medida viaja en el `unitCode` nativo de la Property NGSI-LD. Rebindar
    `unit` cambiaría el significado de un término ya en uso.
    """
    assert _iri(ctx["unit"]) == "saref:Unit"
