"""Crop catalogue: species identity table, readers and builders for ``AgriCrop``.

The species table (``data/crops.json``) is GENERATED from bioorchestrator's
``species_registry.yaml`` by ``scripts/generate_crops.py``; never edit it by hand. Fallow is
the one entry that is not in the registry.

One ``AgriCrop`` per tenant and species: ``urn:ngsi-ld:AgriCrop:{tenant}:{key}``, where the key
is the canonical EPPO code (``c_34007``, the AGROVOC local id, for fallow). Builders emit the
NGSI-LD normalized form and never a context, a parcel link, ``species`` or ``status``.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

FALLOW_KEY = "c_34007"
EPPO_APP = "eppo-gd"

_FALLOW_AGROVOC = "http://aims.fao.org/aos/agrovoc/c_34007"
_EPPO_APP_NAME = "EPPO Global Database"
_EPPO_CODE = re.compile(r"^[A-Z0-9]{5,6}$")


@dataclass(frozen=True)
class CropIdentity:
    key: str
    eppo: str | None
    name: str
    scientific_name: str | None
    agrovoc: str | None
    group: str | None
    lifecycle: str | None
    woody: bool


@lru_cache(maxsize=1)
def _table() -> tuple[dict[str, CropIdentity], dict[str, CropIdentity]]:
    """(by exact EPPO code or alias, by normalized slug / English name / key)."""
    raw = json.loads(
        resources.files("nkz_platform_sdk").joinpath("data", "crops.json").read_text(encoding="utf-8")
    )
    by_code: dict[str, CropIdentity] = {}
    by_name: dict[str, CropIdentity] = {}
    for item in raw:
        identity = CropIdentity(
            key=item["key"], eppo=item["eppo"], name=item["name"],
            scientific_name=item["scientific_name"], agrovoc=item["agrovoc"],
            group=item["group"], lifecycle=item["lifecycle"], woody=bool(item["woody"]),
        )
        for code in [item["eppo"], *item["aliases"]]:
            if code:
                by_code[code.upper()] = identity
        for label in (item["slug"], item["name"], item["key"]):
            by_name[_plain(label)] = identity
    return by_code, by_name


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def resolve_identity(code_or_name: str) -> CropIdentity | None:
    """Identity from an EPPO code, an EPPO alias, a registry slug or an English name."""
    if not isinstance(code_or_name, str) or not code_or_name.strip():
        return None
    by_code, by_name = _table()
    text = code_or_name.strip()
    return by_code.get(text.upper()) or by_name.get(_plain(text))


def species_lifecycle(code: str | None) -> str | None:
    """``"annual"``, ``"perennial"`` or ``None`` (unknown code, never a guess)."""
    if not code:
        return None
    by_code, _ = _table()
    identity = by_code.get(str(code).strip().upper())
    return identity.lifecycle if identity else None


def crop_urn(tenant: str, key: str) -> str:
    return f"urn:ngsi-ld:AgriCrop:{tenant}:{key}"


def _app_urn(tenant: str) -> str:
    return f"urn:ngsi-ld:AgriApp:{tenant}:{EPPO_APP}"


def _unwrap(node: Any) -> Any:
    """Value of a normalized attribute, or the node itself when it is already plain."""
    if isinstance(node, dict) and "type" in node:
        if "value" in node:
            return node["value"]
        if "object" in node:
            return node["object"]
    return node


def _scalar(node: Any) -> str | None:
    value = _unwrap(node)
    return value if isinstance(value, str) else None


def _attr(crop: dict, *names: str) -> Any:
    for name in names:
        if name in crop:
            return crop[name]
    return None


def _is_url(text: str) -> bool:
    return "://" in text


def eppo_of(crop: dict) -> str | None:
    """EPPO code of an AgriCrop, from ``relatedSource`` (one object or a list; normalized or
    keyValues). A plain code under the alias ``eppoCode`` is also read; a URL never is."""
    if not isinstance(crop, dict):
        return None
    source = _unwrap(_attr(crop, "relatedSource"))
    items = source if isinstance(source, list) else [source]
    for item in items:
        item = _unwrap(item)
        if not isinstance(item, dict):
            continue
        app = _scalar(item.get("application"))
        if app is not None and not app.endswith(f":{EPPO_APP}"):
            continue
        code = _scalar(item.get("applicationEntityId"))
        if code and not _is_url(code) and _EPPO_CODE.match(code.strip().upper()):
            return code.strip().upper()
    alias = _scalar(_attr(crop, "eppoCode"))
    if alias and not _is_url(alias) and _EPPO_CODE.match(alias.strip().upper()):
        return alias.strip().upper()
    return None


def is_fallow(crop: dict) -> bool:
    """True when the AGROVOC concept (or its broker alias ``eppoCode``) is the fallow one."""
    if not isinstance(crop, dict):
        return False
    for name in ("agroVocConcept", "eppoCode"):
        value = _scalar(crop.get(name))
        if value and value.rstrip("/").endswith(f"/{FALLOW_KEY}"):
            return True
    return False


def _prop(value: Any) -> dict:
    return {"type": "Property", "value": value}


def build_crop(tenant: str, identity: CropIdentity) -> dict:
    entity: dict[str, Any] = {"id": crop_urn(tenant, identity.key), "type": "AgriCrop",
                              "name": _prop(identity.name)}
    if identity.scientific_name:
        entity["alternateName"] = _prop(identity.scientific_name)
    if identity.agrovoc:
        entity["agroVocConcept"] = _prop(identity.agrovoc)
    if identity.eppo:
        entity["relatedSource"] = _prop(
            [{"application": _app_urn(tenant), "applicationEntityId": identity.eppo}]
        )
    return entity


def build_eppo_app(tenant: str) -> dict:
    return {"id": _app_urn(tenant), "type": "AgriApp", "name": _prop(_EPPO_APP_NAME)}


def _normalize_name(name: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def build_variety(tenant: str, crop_key: str, name: str) -> dict:
    """``AgriProductType`` of a variety; ``name`` is kept exactly as the person wrote it."""
    slug = _normalize_name(name)
    if not slug:
        raise ValueError("variety name has no alphanumeric character")
    return {
        "id": f"urn:ngsi-ld:AgriProductType:{tenant}:{crop_key}-{slug}",
        "type": "AgriProductType",
        "name": _prop(name),
        "category": _prop(["cropVariety"]),
        "root": _prop(True),
    }


# ---- writers -----------------------------------------------------------------

_TENANT = re.compile(r"[a-z0-9_-]+")
_KEY = re.compile(r"[A-Za-z0-9_-]+")
_ROLES = frozenset({"main_crop", "cover_crop", "catch_crop"})
_FREE_TEXT_ROLES = frozenset({"cover_crop", "catch_crop"})


def _check_tenant(tenant: str) -> str:
    if not isinstance(tenant, str) or not _TENANT.fullmatch(tenant):
        raise ValueError("tenant must match ^[a-z0-9_-]+$")
    return tenant


async def _create_once(client: Any, entity: dict) -> None:
    """POST; an existing entity (409) is the expected outcome and is never patched. Anything else raises."""
    import httpx

    try:
        await client.create_entity(entity)
    except httpx.HTTPStatusError as e:
        if e.response is None or e.response.status_code != 409:
            raise


async def ensure_crop(client: Any, tenant: str, code_or_name: str, *, role: str) -> str:
    """Make sure the tenant's catalogue ``AgriCrop`` exists and return its URN.

    A known species gets its table identity (and the tenant's EPPO ``AgriApp`` when it has an EPPO
    code). An unknown name is accepted only for a cover or catch crop (key ``x-{normalized}``, name =
    the text). A main crop without EPPO is refused: it would be unidentifiable downstream.
    ASSUMPTION: fallow (no EPPO by definition) is allowed in any role; it is declared, not sown.
    """
    _check_tenant(tenant)
    if role not in _ROLES:
        raise ValueError(f"unknown crop role: {role!r}")
    identity = resolve_identity(code_or_name)
    if identity is not None:
        if role == "main_crop" and not identity.eppo and identity.key != FALLOW_KEY:
            raise ValueError("a main crop needs an EPPO code")
        if identity.eppo:
            await _create_once(client, build_eppo_app(tenant))
        entity = build_crop(tenant, identity)
    else:
        if role not in _FREE_TEXT_ROLES:
            raise ValueError("unknown crop: only a cover or catch crop may be free text")
        text = code_or_name.strip() if isinstance(code_or_name, str) else ""
        slug = _normalize_name(text)
        if not slug:
            raise ValueError("crop name has no alphanumeric character")
        entity = {"id": crop_urn(tenant, f"x-{slug}"), "type": "AgriCrop", "name": _prop(text)}
    await _create_once(client, entity)
    return entity["id"]


async def ensure_variety(client: Any, tenant: str, crop_key: str, name: str) -> str:
    """Make sure the variety ``AgriProductType`` exists and return its URN (``name`` kept byte for byte)."""
    _check_tenant(tenant)
    if not isinstance(crop_key, str) or not _KEY.fullmatch(crop_key):
        raise ValueError("crop_key must match ^[A-Za-z0-9_-]+$")
    entity = build_variety(tenant, crop_key, name)
    await _create_once(client, entity)
    return entity["id"]
