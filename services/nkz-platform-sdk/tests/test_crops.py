import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nkz_platform_sdk import crops
from nkz_platform_sdk.crops import (
    FALLOW_KEY, build_crop, build_eppo_app, build_variety, crop_urn, eppo_of, is_fallow,
    resolve_identity, species_lifecycle,
)

SDK_DIR = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "config" / "sdm-examples"
REGISTRY = os.environ.get("SPECIES_REGISTRY_YAML")
FALLOW_URI = "http://aims.fao.org/aos/agrovoc/c_34007"
APP = "urn:ngsi-ld:AgriApp:acme:eppo-gd"


def test_alias_resolves_to_canonical_key():
    assert resolve_identity("ZEAMX").key == "ZEAMA"
    assert resolve_identity("zeamx").eppo == "ZEAMA"


def test_wheat_identity():
    wheat = resolve_identity("TRZAX")
    assert wheat.name == "wheat"
    assert wheat.agrovoc == "http://aims.fao.org/aos/agrovoc/c_7951"
    assert wheat.scientific_name == "Triticum aestivum"


def test_lookup_by_slug_and_english_name():
    assert resolve_identity("durum_wheat").key == "TRZDU"
    assert resolve_identity("Durum Wheat").key == "TRZDU"
    assert resolve_identity("italian_ryegrass") is resolve_identity("Italian ryegrass")


def test_unknown_code_is_none():
    assert resolve_identity("QQQQQ") is None
    assert resolve_identity("") is None
    assert resolve_identity(None) is None


def test_fallow_has_no_eppo_and_no_related_source():
    fallow = resolve_identity("fallow")
    assert fallow.key == FALLOW_KEY == "c_34007"
    assert resolve_identity("c_34007") is fallow
    assert fallow.eppo is None
    entity = build_crop("acme", fallow)
    assert "relatedSource" not in entity
    assert entity["name"]["value"] == "fallow"
    assert entity["agroVocConcept"]["value"] == FALLOW_URI
    assert entity["id"] == "urn:ngsi-ld:AgriCrop:acme:c_34007"


def test_lifecycle():
    assert species_lifecycle("TRZAX") == "annual"
    assert species_lifecycle("OLVEU") == "perennial"
    assert species_lifecycle("ZEAMX") == "annual"
    assert species_lifecycle("QQQQQ") is None
    assert species_lifecycle(None) is None


def test_woody_flag():
    assert resolve_identity("OLVEU").woody is True
    assert resolve_identity("TRZAX").woody is False


def test_build_crop_wheat():
    entity = build_crop("acme", resolve_identity("TRZAX"))
    assert entity == {
        "id": "urn:ngsi-ld:AgriCrop:acme:TRZAX", "type": "AgriCrop",
        "name": {"type": "Property", "value": "wheat"},
        "alternateName": {"type": "Property", "value": "Triticum aestivum"},
        "agroVocConcept": {"type": "Property", "value": "http://aims.fao.org/aos/agrovoc/c_7951"},
        "relatedSource": {"type": "Property",
                          "value": [{"application": APP, "applicationEntityId": "TRZAX"}]},
    }
    assert crop_urn("acme", "TRZAX") == entity["id"]


def test_build_crop_without_agrovoc_omits_it():
    entity = build_crop("acme", crops.CropIdentity("x-mix", None, "mix", None, None, None, None, False))
    assert set(entity) == {"id", "type", "name"}


# ---- eppo_of / is_fallow ---------------------------------------------------

SOURCE = {"application": APP, "applicationEntityId": "TRZAX"}


@pytest.mark.parametrize("crop", [
    {"relatedSource": {"type": "Property", "value": [SOURCE]}},
    {"relatedSource": {"type": "Property", "value": SOURCE}},
    {"relatedSource": [SOURCE]},
    {"relatedSource": SOURCE},
    {"relatedSource": [{"application": APP, "applicationEntityId": {"type": "Property", "value": "TRZAX"}}]},
    {"relatedSource": {"type": "Property", "value": [{"application": "urn:x:other", "applicationEntityId": "AAAAA"}, SOURCE]}},
])
def test_eppo_of_forms(crop):
    assert eppo_of(crop) == "TRZAX"


def test_eppo_of_ignores_other_applications():
    other = {"relatedSource": [{"application": "urn:ngsi-ld:AgriApp:acme:field-client", "applicationEntityId": "ABCDE"}]}
    assert eppo_of(other) is None


def test_eppo_of_never_returns_a_url():
    assert eppo_of({"eppoCode": {"type": "Property", "value": FALLOW_URI}}) is None
    assert eppo_of({"eppoCode": FALLOW_URI}) is None
    assert eppo_of({"relatedSource": [{"application": APP, "applicationEntityId": FALLOW_URI}]}) is None


def test_eppo_of_plain_code_under_alias_and_empty():
    assert eppo_of({"eppoCode": {"type": "Property", "value": "OLVEU"}}) == "OLVEU"
    assert eppo_of({}) is None
    assert eppo_of({"relatedSource": None}) is None
    assert eppo_of(None) is None


def test_is_fallow_by_agrovoc_and_by_alias():
    assert is_fallow({"agroVocConcept": {"type": "Property", "value": FALLOW_URI}})
    assert is_fallow({"agroVocConcept": FALLOW_URI})
    assert is_fallow({"eppoCode": {"type": "Property", "value": FALLOW_URI}})
    assert not is_fallow({"agroVocConcept": {"type": "Property", "value": "http://aims.fao.org/aos/agrovoc/c_7951"}})
    assert not is_fallow({})
    assert is_fallow(build_crop("acme", resolve_identity("fallow")))
    assert not is_fallow(build_crop("acme", resolve_identity("TRZAX")))


# ---- varieties and app -----------------------------------------------------

def test_variety_keeps_name_byte_for_byte():
    entity = build_variety("acme", "TRZAX", "Marius ñ 2")
    assert entity["name"] == {"type": "Property", "value": "Marius ñ 2"}
    assert entity["category"] == {"type": "Property", "value": ["cropVariety"]}
    assert entity["root"] == {"type": "Property", "value": True}
    assert entity["type"] == "AgriProductType"
    assert entity["id"] == "urn:ngsi-ld:AgriProductType:acme:TRZAX-marius-n-2"


@pytest.mark.parametrize("name,slug", [
    ("  Ñandú / Rojo!! ", "nandu-rojo"), ("Café", "cafe"), ("A--B  C", "a-b-c"), ("ABC123", "abc123"),
])
def test_variety_id_normalization(name, slug):
    assert build_variety("acme", "HORVX", name)["id"].endswith(f":HORVX-{slug}")


def test_variety_without_alphanumerics_is_rejected():
    with pytest.raises(ValueError):
        build_variety("acme", "HORVX", "***")


def test_eppo_app():
    assert build_eppo_app("acme") == {"id": APP, "type": "AgriApp",
                                      "name": {"type": "Property", "value": "EPPO Global Database"}}


def test_no_builder_emits_forbidden_attributes():
    outputs = [build_crop("acme", i) for i in (resolve_identity("TRZAX"), resolve_identity("fallow"))]
    outputs += [build_variety("acme", "TRZAX", "Marius"), build_eppo_app("acme")]
    for entity in outputs:
        assert not {"hasAgriParcel", "species", "status", "@context"} & set(entity)


# ---- generated table ---------------------------------------------------------

def test_table_has_no_duplicate_codes():
    raw = json.loads((SDK_DIR / "nkz_platform_sdk" / "data" / "crops.json").read_text(encoding="utf-8"))
    codes = [c for item in raw for c in [item["eppo"], *item["aliases"]] if c]
    assert len(codes) == len(set(codes))
    assert [item["key"] for item in raw] == sorted(item["key"] for item in raw)


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_crops", SDK_DIR / "scripts" / "generate_crops.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not REGISTRY, reason="SPECIES_REGISTRY_YAML (path to species_registry.yaml) not set")
def test_generated_file_equals_script_output(tmp_path):
    out = tmp_path / "crops.json"
    subprocess.run([sys.executable, str(SDK_DIR / "scripts" / "generate_crops.py"), REGISTRY, str(out)], check=True)
    assert out.read_text(encoding="utf-8") == (SDK_DIR / "nkz_platform_sdk" / "data" / "crops.json").read_text(encoding="utf-8")


def test_generator_supplement_is_only_fallow():
    assert [item["key"] for item in _load_generator().SUPPLEMENT] == [FALLOW_KEY]


# ---- conformance examples ----------------------------------------------------
# `config/sdm-examples/` holds the builder output; refresh after a deliberate change with SDM_EXAMPLES_WRITE=1.

def _assert_example(file_name, entity):
    path = EXAMPLES / file_name
    if os.environ.get("SDM_EXAMPLES_WRITE") == "1":
        path.write_text(json.dumps(entity, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == entity, f"{file_name} no longer matches the builder"


def test_example_agricrop():
    _assert_example("AgriCrop.json", build_crop("acme", resolve_identity("TRZAX")))


def test_example_agricrop_fallow():
    _assert_example("AgriCrop.fallow.json", build_crop("acme", resolve_identity("fallow")))


def test_example_agriproducttype():
    _assert_example("AgriProductType.json", build_variety("acme", "TRZAX", "Marius"))


def test_example_agriapp():
    _assert_example("AgriApp.json", build_eppo_app("acme"))


# ---- writers -----------------------------------------------------------------

import httpx  # noqa: E402

from nkz_platform_sdk.crops import ensure_crop, ensure_variety  # noqa: E402


def _status(code):
    req = httpx.Request("POST", "http://orion.invalid/x")
    return httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))


class _Writer:
    def __init__(self, fail=None):
        self.created, self.fail = [], fail or {}

    async def create_entity(self, entity):
        self.created.append(entity)
        code = self.fail.get(entity["type"])
        if code:
            raise _status(code)
        return {}


@pytest.mark.asyncio
async def test_ensure_crop_creates_the_app_and_the_crop():
    w = _Writer()
    urn = await ensure_crop(w, "acme", "TRZAX", role="main_crop")
    assert urn == "urn:ngsi-ld:AgriCrop:acme:TRZAX"
    assert [e["type"] for e in w.created] == ["AgriApp", "AgriCrop"]
    assert w.created[1] == build_crop("acme", resolve_identity("TRZAX"))


@pytest.mark.asyncio
async def test_ensure_crop_409_is_success_and_nothing_is_patched():
    w = _Writer(fail={"AgriApp": 409, "AgriCrop": 409})
    assert await ensure_crop(w, "acme", "wheat", role="main_crop") == "urn:ngsi-ld:AgriCrop:acme:TRZAX"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [400, 500, 404])
async def test_ensure_crop_any_other_error_raises(code):
    with pytest.raises(httpx.HTTPStatusError):
        await ensure_crop(_Writer(fail={"AgriCrop": code}), "acme", "TRZAX", role="main_crop")


@pytest.mark.asyncio
async def test_ensure_crop_fallow_has_no_app():
    w = _Writer()
    assert await ensure_crop(w, "acme", "fallow", role="main_crop") == f"urn:ngsi-ld:AgriCrop:acme:{FALLOW_KEY}"
    assert [e["type"] for e in w.created] == ["AgriCrop"]


@pytest.mark.asyncio
async def test_ensure_crop_free_text_only_for_cover_and_catch():
    w = _Writer()
    urn = await ensure_crop(w, "acme", "  Mezcla de Leguminosas ", role="cover_crop")
    assert urn == "urn:ngsi-ld:AgriCrop:acme:x-mezcla-de-leguminosas"
    assert w.created[0]["name"]["value"] == "Mezcla de Leguminosas" and w.created[0]["type"] == "AgriCrop"
    assert await ensure_crop(_Writer(), "acme", "zzz unknown", role="catch_crop")
    for bad in ("zzz unknown", "QQQQQ"):
        with pytest.raises(ValueError):
            await ensure_crop(_Writer(), "acme", bad, role="main_crop")
    with pytest.raises(ValueError):
        await ensure_crop(_Writer(), "acme", "!!!", role="cover_crop")


@pytest.mark.asyncio
async def test_ensure_crop_refuses_a_main_crop_without_eppo_and_unknown_roles():
    # an identity without EPPO that is not fallow (none in the table today) would raise; roles are checked
    with pytest.raises(ValueError):
        await ensure_crop(_Writer(), "acme", "TRZAX", role="boss")


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant", ["Acme", "ac me", "acme:x", "", "a/b", "acme\n"])
async def test_the_tenant_is_validated_before_anything_is_built_or_sent(tenant):
    w = _Writer()
    with pytest.raises(ValueError):
        await ensure_crop(w, tenant, "TRZAX", role="main_crop")
    with pytest.raises(ValueError):
        await ensure_variety(w, tenant, "TRZAX", "Marius")
    assert w.created == []


@pytest.mark.asyncio
async def test_ensure_variety_creates_409_ok_and_validates_the_key():
    w = _Writer()
    urn = await ensure_variety(w, "acme", "TRZAX", "Marius")
    assert urn == "urn:ngsi-ld:AgriProductType:acme:TRZAX-marius" and w.created[0]["name"]["value"] == "Marius"
    assert await ensure_variety(_Writer(fail={"AgriProductType": 409}), "acme", "TRZAX", "Marius") == urn
    with pytest.raises(httpx.HTTPStatusError):
        await ensure_variety(_Writer(fail={"AgriProductType": 500}), "acme", "TRZAX", "Marius")
    for bad in ("TR ZAX", "a:b", "a/b", ""):
        with pytest.raises(ValueError):
            await ensure_variety(w, "acme", bad, "Marius")
    with pytest.raises(ValueError):
        await ensure_variety(w, "acme", "TRZAX", "!!!")
