"""Runtime check of NGSI-LD entity types against the closed list.

The list lives in `config/entity-types/` (`allowed.txt`, `legacy.txt`) and the
platform context in `config/ngsi-ld-context.json`. The CI checker
(`.github/scripts/check_entity_types.py`) parses the same files, but service
images do not contain `.github/`, so parsing is re-implemented here; a parity
test keeps the two in step.

No Flask import and no relative import: the module is loaded both as
`entity_type_guard` and as `common.entity_type_guard`, and by non-web jobs.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

logger = logging.getLogger(__name__)

CONFIG_DIR_ENV = "ENTITY_TYPE_CONFIG_DIR"
ENFORCE_ENV = "ENTITY_TYPE_GUARD_ENFORCE"
LISTS_SUBDIR = "entity-types"
ALLOWED_FILE = "allowed.txt"
LEGACY_FILE = "legacy.txt"
CONTEXT_FILE = "ngsi-ld-context.json"
BROKER_HEADER = "## broker"
CODE_HEADER = "## code"
# Bounds on what one rejection echoes (log line and 422 body): a client controls
# these strings, and log pipelines drop or cut oversized lines.
MAX_ECHOED_TYPES = 20
MAX_ECHOED_CHARS = 200
# JSON-LD only treats a term as a prefix when its IRI ends in one of these.
PREFIX_DELIMITERS = ("/", "#", ":", "?", "@", "[", "]")
# The one type without a term in the platform context: the broker stored it
# under the default vocabulary, so that IRI must keep matching.
ZONE_NAME = "AgriManagementZone"
ZONE_DEFAULT_IRI = "https://uri.etsi.org/ngsi-ld/default-context/" + ZONE_NAME


class GuardUnavailable(Exception):
    """The lists or the context could not be loaded; the guard must not judge."""


@dataclass(frozen=True)
class AcceptedTypes:
    names: frozenset[str]
    iris: frozenset[str]
    # Prefix term -> absolute IRI, from the same context: `nkz:X` is judged by
    # the IRI the broker will store, not by the bare name after the colon.
    prefixes: Mapping[str, str]


def parse_names(text: str) -> list[str]:
    """One name per line; blank lines and `#` comments are ignored."""
    names: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            names.append(line)
    return names


def parse_legacy(text: str) -> tuple[list[str], list[str]]:
    """Return (broker, code) names.

    A name before a header and any `## <other>` header are errors: an unknown
    header would otherwise be read as a comment and silently merge its names
    into the previous block.
    """
    blocks: dict[str, list[str]] = {BROKER_HEADER: [], CODE_HEADER: []}
    current: list[str] | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line in blocks:
            current = blocks[line]
        elif line.startswith("##"):
            raise ValueError(f"line {number}: unknown block header '{line}'")
        elif not line or line.startswith("#"):
            continue
        elif current is None:
            raise ValueError(f"line {number}: name {line!r} before a '## broker' or '## code' header")
        else:
            current.append(line)
    return blocks[BROKER_HEADER], blocks[CODE_HEADER]


def find_config_dir() -> Path | None:
    """The directory holding `entity-types/allowed.txt`, or None.

    An explicit override is the only candidate: falling back from a wrong
    override to a bundled copy would hide a misconfiguration.
    """
    override = os.environ.get(CONFIG_DIR_ENV, "")
    if override:
        candidates = [Path(override)]
    else:
        here = Path(__file__).resolve().parent
        candidates = [here.parent / "config", here.parent.parent / "config"]
    for candidate in candidates:
        if (candidate / LISTS_SUBDIR / ALLOWED_FILE).is_file():
            return candidate
    return None


def _context_terms(context: object) -> dict:
    """The term table of the platform context; unusable shapes are an error.

    A context that parses but yields no terms would load an empty table and
    silently reject every IRI-form type, so it is as unavailable as a missing file.
    """
    parts = context.get("@context") if isinstance(context, dict) else None
    if isinstance(parts, dict):
        parts = [parts]
    if not isinstance(parts, list):
        raise GuardUnavailable("platform context has no usable @context")
    terms: dict = {}
    for part in parts:
        if isinstance(part, dict):
            terms.update(part)
    if not terms:
        raise GuardUnavailable("platform context defines no terms")
    return terms


def _expand(terms: dict, name: str, depth: int = 0) -> str | None:
    """The IRI a short name expands to in the context, or None if it has no term."""
    value = terms.get(name)
    if isinstance(value, dict):
        value = value.get("@id")
    if not isinstance(value, str):
        return None
    prefix, sep, rest = value.partition(":")
    if sep and not rest.startswith("//") and prefix in terms and depth < 5:
        base = _expand(terms, prefix, depth + 1)
        if base:
            return base + rest
    return value


def load_accepted(config_dir: Path | None = None) -> AcceptedTypes:
    directory = config_dir if config_dir is not None else find_config_dir()
    if directory is None:
        raise GuardUnavailable("entity type config directory not found")
    try:
        allowed = parse_names((directory / LISTS_SUBDIR / ALLOWED_FILE).read_text(encoding="utf-8"))
        broker, _code = parse_legacy((directory / LISTS_SUBDIR / LEGACY_FILE).read_text(encoding="utf-8"))
        context = json.loads((directory / CONTEXT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise GuardUnavailable(f"cannot load entity type lists: {type(exc).__name__}: {exc}") from exc

    names = frozenset(allowed) | frozenset(broker)
    terms = _context_terms(context)
    iris: set[str] = set()
    for name in names:
        iri = _expand(terms, name)
        if iri:
            iris.add(iri)
    if ZONE_NAME in names:
        iris.add(ZONE_DEFAULT_IRI)
    prefixes: dict[str, str] = {}
    for term in terms:
        if term.startswith("@"):
            continue
        base = _expand(terms, term)
        if base and "://" in base and base.endswith(PREFIX_DELIMITERS):
            prefixes[term] = base
    return AcceptedTypes(names=names, iris=frozenset(iris), prefixes=MappingProxyType(prefixes))


def _judgeable(value: object) -> list[str] | None:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return list(value)
    return None


def declared_types(body: object) -> list[str] | None:
    """The type(s) a payload declares, or None when it cannot be judged.

    Both `type` and `@type` are read: a body carrying both must not get one
    past the check by hiding it under the key that is not looked at.
    """
    if not isinstance(body, dict):
        return None
    found: list[str] = []
    for key in ("type", "@type"):
        values = _judgeable(body.get(key))
        if values:
            found.extend(values)
    return found or None


def _is_accepted(value: str, accepted: AcceptedTypes) -> bool:
    if "://" in value:
        return value in accepted.iris
    prefix, sep, rest = value.partition(":")
    if sep:
        base = accepted.prefixes.get(prefix)
        return base is not None and (base + rest) in accepted.iris
    return value in accepted.names


def rejected_types(types: list[str], accepted: AcceptedTypes) -> list[str]:
    return [value for value in types if not _is_accepted(value, accepted)]


def _bounded(values: list[str]) -> list[str]:
    return [
        value if len(value) <= MAX_ECHOED_CHARS else value[:MAX_ECHOED_CHARS] + "\u2026"
        for value in values[:MAX_ECHOED_TYPES]
    ]


def enforce_enabled(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() == "true"


class EntityTypeGuard:
    def __init__(self, service: str, accepted: AcceptedTypes | None, enforce: bool):
        self.service = service
        self.accepted = accepted
        self.enforce = enforce

    @classmethod
    def from_env(cls, service: str) -> "EntityTypeGuard":
        """Never raises: a guard that cannot load its lists lets everything through, loudly."""
        enforce = enforce_enabled(os.environ.get(ENFORCE_ENV))
        try:
            accepted = load_accepted()
        except Exception as exc:  # noqa: BLE001 - this runs at import; any failure must degrade, not kill the worker
            # Class only: an exception message can carry a path or file content.
            logger.critical(
                "entity_type_guard %s",
                json.dumps({"enforced": enforce, "error": type(exc).__name__, "service": service, "status": "unavailable"}, sort_keys=True),
            )
            accepted = None
        return cls(service, accepted, enforce)

    def check(
        self,
        types: list[str] | None,
        *,
        tenant: str | None,
        path: str,
        module_id: str | None = None,
        foreign_context: bool = False,
    ) -> list[str]:
        """Names to reject: empty when fine, unavailable or not enforcing."""
        if self.accepted is None or not types:
            return []
        rejected = rejected_types(types, self.accepted)
        if not rejected and not foreign_context:
            return []
        rejected = _bounded(rejected)
        record = {
            "enforced": self.enforce,
            "foreign_context": foreign_context,
            "module_id": module_id,
            "path": path,
            "service": self.service,
            "tenant": tenant,
            "types": rejected,
        }
        level = logging.WARNING if rejected else logging.INFO
        logger.log(level, "entity_type_guard %s", json.dumps(record, sort_keys=True))
        return rejected if self.enforce else []
