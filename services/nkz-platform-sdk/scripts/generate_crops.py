#!/usr/bin/env python3
"""Generate nkz_platform_sdk/data/crops.json from bioorchestrator's species_registry.yaml.

Usage: generate_crops.py <species_registry.yaml> [output.json]

The output is committed and must never be edited by hand: a test regenerates it from the
registry and compares. Fallow is not a species and is never in the registry; it is the only
entry the SUPPLEMENT below adds.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "nkz_platform_sdk" / "data" / "crops.json"

SUPPLEMENT = [
    {
        "key": "c_34007",
        "eppo": None,
        "aliases": [],
        "slug": "fallow",
        "name": "fallow",
        "scientific_name": None,
        "agrovoc": "http://aims.fao.org/aos/agrovoc/c_34007",
        "group": None,
        "lifecycle": None,
        "woody": False,
        "cover_only": False,
    },
]


def build(registry_path: Path) -> list[dict]:
    with open(registry_path, encoding="utf-8") as handle:
        species = yaml.safe_load(handle)["species"]
    entries = []
    for slug, spec in species.items():
        eppo = spec["eppo_code"]
        english = (spec.get("common_names") or {}).get("en")
        entries.append({
            "key": eppo,
            "eppo": eppo,
            "aliases": sorted(spec.get("eppo_aliases") or []),
            "slug": slug,
            "name": english or slug.replace("_", " "),
            "scientific_name": spec.get("scientific_name"),
            "agrovoc": spec.get("agrovoc_uri"),
            "group": spec.get("crop_group"),
            "lifecycle": spec.get("lifecycle"),
            "woody": bool(spec.get("woody", False)),
            "cover_only": bool(spec.get("cover_only", False)),
        })
    entries.extend(dict(item) for item in SUPPLEMENT)
    entries.sort(key=lambda item: item["key"])
    keys = [item["key"] for item in entries]
    if len(keys) != len(set(keys)):
        raise SystemExit("duplicate crop key in registry")
    return entries


def render(entries: list[dict]) -> str:
    return json.dumps(entries, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(argv[2]) if len(argv) == 3 else DEFAULT_OUT
    out.write_text(render(build(Path(argv[1]))), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
