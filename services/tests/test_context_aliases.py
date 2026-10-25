"""Context terms sharing one IRI: Orion-LD returns the last-defined one."""
import json
import re
from collections import defaultdict
from pathlib import Path

from common.context_aliases import CANONICAL, canonical_attribute

ROOT = Path(__file__).resolve().parents[2]


def _same_iri_pairs():
    ctx = json.loads((ROOT / "config" / "ngsi-ld-context.json").read_text())["@context"]
    terms = next(c for c in ctx if isinstance(c, dict))
    by_iri = defaultdict(list)
    for term, definition in terms.items():
        iri = definition.get("@id") if isinstance(definition, dict) else definition
        by_iri[iri].append(term)
    return {iri: names for iri, names in by_iri.items() if len(names) > 1}


def _profile_attributes():
    found = set()
    for path in (ROOT / "config" / "timescaledb" / "migrations").glob("*.sql"):
        found.update(re.findall(r'"?sdmAttribute"?\s*[:=]\s*[\'"]([A-Za-z0-9_]+)[\'"]',
                                path.read_text()))
    return found


def test_aliases_map_to_last_defined_term():
    pairs = _same_iri_pairs()
    used = _profile_attributes() | set(CANONICAL)
    checked = 0
    for names in pairs.values():
        hit = [n for n in names if n in used]
        if not hit:
            continue
        for name in hit:
            assert canonical_attribute(name) == names[-1], (name, names)
            checked += 1
    assert checked >= 2


def test_every_alias_target_is_in_the_context():
    ctx = json.loads((ROOT / "config" / "ngsi-ld-context.json").read_text())["@context"]
    terms = next(c for c in ctx if isinstance(c, dict))
    for source, target in CANONICAL.items():
        assert source in terms and target in terms
