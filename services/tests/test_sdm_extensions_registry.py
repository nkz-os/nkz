"""El registro de extensiones debe cuadrar con el @context, no ser prosa suelta.

Una extensión sin declarar es indistinguible de un descuido. Este test obliga a que cada
término del vocabulario propio (nkz:) aparezca en el registro, y a que el registro no
invente términos que el contexto no define.
"""

import json
import pathlib
import re

import pytest

SERVICES_DIR = pathlib.Path(__file__).resolve().parents[1]
CONTEXT_FILE = SERVICES_DIR.parent / "config" / "ngsi-ld-context.json"
REGISTRY_FILE = SERVICES_DIR.parent / "docs" / "development" / "SDM_EXTENSIONS.md"

ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")


@pytest.fixture(scope="module")
def ctx():
    return json.loads(CONTEXT_FILE.read_text())["@context"][1]


@pytest.fixture(scope="module")
def registry():
    assert REGISTRY_FILE.is_file(), f"falta el registro de extensiones: {REGISTRY_FILE}"
    rows = {}
    for line in REGISTRY_FILE.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if m and m.group(2).strip() not in ("---", ":---"):
            rows[m.group(1)] = {"kind": m.group(2).strip(), "status": m.group(3).strip()}
    return rows


def _iri(v):
    return v.get("@id", "") if isinstance(v, dict) else (v if isinstance(v, str) else "")


def _own_terms(ctx) -> set:
    out = set()
    for term, value in ctx.items():
        if term.startswith("@") or term == "nkz":
            continue
        iri = _iri(value)
        if iri.startswith("nkz:") or iri.startswith("https://nkz-os.org/ns/"):
            out.add(term)
    return out


def test_every_own_term_is_registered(ctx, registry):
    missing = sorted(_own_terms(ctx) - set(registry))
    assert not missing, (
        f"{len(missing)} términos del vocabulario propio no están en SDM_EXTENSIONS.md: "
        f"{missing[:15]}. Una extensión sin declarar es indistinguible de un descuido."
    )


def test_registry_does_not_invent_terms(ctx, registry):
    unknown = sorted(set(registry) - set(ctx))
    assert not unknown, f"el registro lista términos que el contexto no define: {unknown}"


def test_status_values_are_from_the_closed_set(registry):
    legal = {"propuesta-sdm", "extension-declarada", "sin-revisar"}
    bad = {t: r["status"] for t, r in registry.items() if r["status"] not in legal}
    assert not bad, f"estados fuera del conjunto {sorted(legal)}: {bad}"
