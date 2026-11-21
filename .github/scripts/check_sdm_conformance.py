#!/usr/bin/env python3
"""Conformance of NGSI-LD entities with the official Smart Data Models.

The official JSON Schemas and subject contexts are not copied into this
repository. `config/sdm/pins.json` names every official file by repository,
commit and sha256; this module downloads them into a cache, refuses any file
whose checksum differs, and validates entities against them:

  type-not-official-iri      the platform context maps the type to another IRI
  schema                     the entity, in keyValues form, breaks the official schema
  attribute-outside-context  an attribute is not a term of the platform or core context,
                             so the broker stores it under the default vocabulary
  subattribute-outside-context  the same, for a property of a property
  not-normalized             an attribute is not written in the NGSI-LD normalized form

Of the JSON Schema formats only `date-time` and `date` are enforced (RFC 3339);
`uri`, `time`, `email` and the others are not.

A schema `$ref` that is not in the pinned set is an error, never a download.

`examples` subcommand: validate the example entities of a directory (one JSON
file per entity, or a list of entities). An official type must have an example
that validates, or be on the pending list; a pending type whose example
validates must leave the list.

Exit codes: 0 clean, 1 findings, 2 bad arguments, 3 internal error (download,
checksum, reference outside the pinned set, unexpected exception).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

RAW = "https://raw.githubusercontent.com/{repo}/{commit}/{path}"
NGSI_MEMBERS = frozenset({
    "type", "value", "object", "observedAt", "unitCode", "datasetId", "createdAt", "modifiedAt",
    "deletedAt", "instanceId", "languageMap", "previousValue", "previousObject", "objectType",
    "valueList", "objectList", "json", "vocab", "previousVocab", "entity", "previousLanguageMap",
})
ENTITY_MEMBERS = frozenset({"id", "type", "@context", "scope", "createdAt", "modifiedAt", "deletedAt"})
# NGSI-LD attribute type -> the member that carries its value.
ATTRIBUTE_MEMBER = {
    "Property": "value", "GeoProperty": "value", "Relationship": "object", "LanguageProperty": "languageMap",
    "VocabProperty": "vocab", "JsonProperty": "json", "ListProperty": "valueList", "ListRelationship": "objectList",
}
ATTRIBUTE_TYPES = frozenset(ATTRIBUTE_MEMBER)
# RFC 3339, as the official schemas mean by `date-time` and `date`.
_DATE_TIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:[Zz]|[+-](\d{2}):(\d{2}))$"
)
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


class PinError(RuntimeError):
    """A pinned file is missing, cannot be fetched, or does not match its checksum."""


@dataclass(frozen=True)
class Finding:
    entity_id: str
    entity_type: str
    kind: str
    where: str
    message: str

    def line(self) -> str:
        return f"{self.entity_type} {self.entity_id}: {self.kind} at {self.where}: {self.message}"


# ── pinned files ────────────────────────────────────────────────────────────


def load_pins(path: Path) -> dict:
    pins = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("subjects", "types", "shared", "core_context"):
        if key not in pins:
            raise PinError(f"{path}: missing section {key}")
    return pins


def _download(url: str, attempts: int = 3) -> bytes:
    """One pinned file. Retried, because a passing network hiccup must not look like a finding."""
    request = urllib.request.Request(url, headers={"User-Agent": "sdm-conformance-check"})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 (https URLs from the pins file)
                return response.read()
        except OSError:
            if attempt == attempts:
                raise
            time.sleep(2 * attempt)
    raise AssertionError("unreachable")


def pinned_files(pins: dict) -> dict[str, tuple[str, str]]:
    """Every pinned file as key -> (download URL, sha256).

    Keys: `context:<subject>`, `schema:<type>`, the `$ref` URL of a shared schema, `core`.
    """
    files: dict[str, tuple[str, str]] = {}
    for subject, entry in pins["subjects"].items():
        url = RAW.format(repo=entry["repo"], commit=entry["commit"], path=entry["context"]["path"])
        files[f"context:{subject}"] = (url, entry["context"]["sha256"])
    for name, entry in pins["types"].items():
        subject = pins["subjects"].get(entry["subject"])
        if subject is None:
            raise PinError(f"type {name}: unknown subject {entry['subject']}")
        url = RAW.format(repo=subject["repo"], commit=subject["commit"], path=entry["schema"]["path"])
        files[f"schema:{name}"] = (url, entry["schema"]["sha256"])
    for ref, entry in pins["shared"].items():
        files[ref] = (RAW.format(repo=entry["repo"], commit=entry["commit"], path=entry["path"]), entry["sha256"])
    files["core"] = (pins["core_context"]["url"], pins["core_context"]["sha256"])
    return files


def fetch(pins: dict, cache_dir: Path, download: Callable[[str], bytes] = _download) -> dict[str, Any]:
    """The pinned files, parsed, keyed as in `pinned_files`. Downloads what the cache lacks.

    A cached file is trusted only if it still matches its checksum; a download that
    does not match raises `PinError` and is not written to the cache.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    loaded: dict[str, Any] = {}
    for key, (url, digest) in pinned_files(pins).items():
        cached = cache_dir / digest
        data = cached.read_bytes() if cached.exists() else None
        if data is None or hashlib.sha256(data).hexdigest() != digest:
            try:
                data = download(url)
            except Exception as exc:
                raise PinError(f"{key}: could not fetch {url}: {exc}") from exc
            got = hashlib.sha256(data).hexdigest()
            if got != digest:
                raise PinError(f"{key}: checksum of {url} is {got}, pinned {digest}")
            cached.write_bytes(data)
        loaded[key] = json.loads(data.decode("utf-8"))
    return loaded


# ── contexts ────────────────────────────────────────────────────────────────


def context_terms(document: Any) -> dict[str, str]:
    """Term -> IRI of a JSON-LD context document, prefixes expanded."""
    raw: dict[str, str] = {}
    context = document.get("@context", document) if isinstance(document, dict) else document
    for item in context if isinstance(context, list) else [context]:
        if not isinstance(item, dict):
            continue
        for term, value in item.items():
            if term.startswith("@"):
                continue
            iri = value.get("@id") if isinstance(value, dict) else value
            if isinstance(iri, str):
                raw[term] = iri

    def expand(iri: str) -> str:
        if "://" in iri or ":" not in iri:
            return iri
        prefix, rest = iri.split(":", 1)
        base = raw.get(prefix)
        return base + rest if isinstance(base, str) and "://" in base else iri

    return {term: expand(iri) for term, iri in raw.items()}


# ── keyValues form ──────────────────────────────────────────────────────────


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"@type", "@value"}:
            return value["@value"]
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _attribute_type(instance: Any) -> str | None:
    """The NGSI-LD type of a normalized attribute instance, or None if it is not one."""
    kind = instance.get("type") if isinstance(instance, dict) else None
    return kind if isinstance(kind, str) and kind in ATTRIBUTE_TYPES and ATTRIBUTE_MEMBER[kind] in instance else None


def _attribute_value(attr: Any) -> Any:
    if isinstance(attr, list):  # several instances of one attribute
        return [_attribute_value(item) for item in attr]
    kind = _attribute_type(attr)
    return _plain(attr[ATTRIBUTE_MEMBER[kind]]) if kind else _plain(attr)


def key_values(entity: dict) -> dict:
    """The entity as the official schemas describe it: attribute name -> bare value."""
    return {key: (value if key in ("id", "type") else _attribute_value(value))
            for key, value in entity.items() if key != "@context"}


# ── validation ──────────────────────────────────────────────────────────────


def _format_checker():
    from jsonschema import FormatChecker

    checker = FormatChecker(formats=())

    @checker.checks("date-time", raises=ValueError)
    def _date_time(value: Any) -> bool:
        if not isinstance(value, str):
            return True
        match = _DATE_TIME.match(value)
        if not match:
            raise ValueError("not an RFC 3339 date-time")
        year, month, day, hour, minute, second = (int(part) for part in match.groups()[:6])
        date(year, month, day)  # a real calendar day
        # Ranges are checked here, not left to the standard library: what it accepts
        # (hour 24, for one) changes between Python versions. Second 60 is a leap second.
        if hour > 23 or minute > 59 or second > 60:
            raise ValueError("time out of range")
        if match.group(7) is not None and (int(match.group(7)) > 23 or int(match.group(8)) > 59):
            raise ValueError("offset out of range")
        return True

    @checker.checks("date", raises=ValueError)
    def _date(value: Any) -> bool:
        if isinstance(value, str):
            match = _DATE.match(value)
            if not match:
                raise ValueError("not an RFC 3339 full-date")
            date(*(int(part) for part in match.groups()))
        return True

    return checker


class Validator:
    """Checks entities against the pinned official files and the platform context."""

    def __init__(self, pins: dict, files: dict[str, Any], platform_context: Any):
        from referencing import Registry, Resource
        from referencing.exceptions import NoSuchResource
        from referencing.jsonschema import DRAFT7

        self.pins = pins
        self.platform = context_terms(platform_context)
        self.core = context_terms(files["core"])
        self._schemas = {name: files[f"schema:{name}"] for name in pins["types"]}
        self._subject_terms = {subject: context_terms(files[f"context:{subject}"]) for subject in pins["subjects"]}

        def refuse(uri: str):
            raise NoSuchResource(ref=uri)

        resources = [(ref, Resource.from_contents(files[ref], default_specification=DRAFT7)) for ref in pins["shared"]]
        self._registry = Registry(retrieve=refuse).with_resources(resources)
        self._formats = _format_checker()

    def official_types(self) -> frozenset[str]:
        return frozenset(self.pins["types"])

    def official_type_iri(self, name: str) -> str | None:
        subject = self.pins["types"][name]["subject"]
        return self._subject_terms[subject].get(name)

    def _known_term(self, name: str) -> bool:
        return name in self.platform or name in self.core

    def check_entity(self, entity: dict) -> list[Finding]:
        from jsonschema import Draft7Validator

        entity_id = str(entity.get("id", "?"))
        kind = entity.get("type")
        name = kind if isinstance(kind, str) else "?"
        found: list[Finding] = []

        def add(what: str, where: str, message: str) -> None:
            found.append(Finding(entity_id, name, what, where, message))

        if not isinstance(kind, str) or "://" in kind:
            add("schema", "type", "the type must be one short name")
            return found
        if name not in self.platform:
            add("attribute-outside-context", "type", "the type is not a term of the platform context")

        for attr_name, attr in entity.items():
            if attr_name in ENTITY_MEMBERS:
                continue
            if not self._known_term(attr_name):
                add("attribute-outside-context", attr_name, "not a term of the platform context")
            for instance in attr if isinstance(attr, list) else [attr]:
                if _attribute_type(instance) is None:
                    add("not-normalized", attr_name,
                        "expected an object with an NGSI-LD attribute type and its value member")
                    continue
                for member in instance:
                    if member not in NGSI_MEMBERS and not self._known_term(member):
                        add("subattribute-outside-context", f"{attr_name}.{member}",
                            "not a term of the platform context")

        if name in self._schemas:
            official = self.official_type_iri(name)
            mapped = self.platform.get(name)
            if mapped is not None and official is not None and mapped != official:
                add("type-not-official-iri", "type", f"mapped to {mapped}, official is {official}")
            validator = Draft7Validator(self._schemas[name], registry=self._registry, format_checker=self._formats)
            for error in sorted(validator.iter_errors(key_values(entity)), key=lambda e: list(map(str, e.absolute_path))):
                where = "/".join(str(part) for part in error.absolute_path) or "(entity)"
                message = error.message if len(error.message) <= 200 else error.message[:197] + "..."
                add("schema", where, f"{error.validator}: {message}")
        return found


# ── examples ────────────────────────────────────────────────────────────────


def read_names(path: Path) -> list[str]:
    """Names of a list file: one per line, `#` comments and blank lines ignored."""
    return [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def read_examples(directory: Path) -> list[tuple[str, dict]]:
    examples: list[tuple[str, dict]] = []
    for path in sorted(Path(directory).glob("*.json")):
        content = json.loads(path.read_text(encoding="utf-8"))
        for entity in content if isinstance(content, list) else [content]:
            if not isinstance(entity, dict):
                raise ValueError(f"{path.name}: an example must be an entity object")
            examples.append((path.name, entity))
    return examples


def check_examples(
    validator: Validator, examples: Iterable[tuple[str, dict]], allowed: Iterable[str], pending: Iterable[str]
) -> tuple[list[str], list[str]]:
    """(errors, notes) for a set of example entities.

    Errors fail the check; notes report what the pending list still covers.
    """
    errors: list[str] = []
    notes: list[str] = []
    allowed = list(allowed)
    pending = set(pending)
    official = validator.official_types()

    for name in sorted(set(allowed) - official):
        errors.append(f"{name}: allowed entity type without a pinned official schema")
    for name in sorted(pending - official):
        errors.append(f"{name}: on the pending list but not an official type with a pinned schema")

    by_type: dict[str, list[tuple[str, list[Finding]]]] = {}
    for file_name, entity in examples:
        findings = validator.check_entity(entity)
        by_type.setdefault(str(entity.get("type")), []).append((file_name, findings))

    for name, checked in sorted(by_type.items()):
        failing = [(file_name, findings) for file_name, findings in checked if findings]
        if name in pending:
            if not failing:
                errors.append(f"{name}: every example validates; remove it from the pending list")
            else:
                notes.append(f"{name}: pending, {sum(len(f) for _, f in failing)} findings in {len(failing)} example(s)")
            continue
        for file_name, findings in failing:
            errors.extend(f"{file_name}: {finding.line()}" for finding in findings)

    for name in sorted(official & set(allowed)):
        if name not in by_type and name not in pending:
            errors.append(f"{name}: official type with no example and not on the pending list")
    return errors, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    examples = commands.add_parser("examples", help="validate the example entities of a directory")
    examples.add_argument("directory", type=Path)
    examples.add_argument("--pins", type=Path, default=Path("config/sdm/pins.json"))
    examples.add_argument("--context", type=Path, default=Path("config/ngsi-ld-context.json"))
    examples.add_argument("--allowed", type=Path, default=Path("config/entity-types/allowed.txt"))
    examples.add_argument("--pending", type=Path, default=Path("config/sdm/pending.txt"))
    examples.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "sdm-conformance")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        from referencing.exceptions import Unresolvable

        pins = load_pins(args.pins)
        validator = Validator(pins, fetch(pins, args.cache), json.loads(args.context.read_text(encoding="utf-8")))
        examples_found = read_examples(args.directory)
        if not examples_found:
            raise FileNotFoundError(f"no example entities (*.json) in {args.directory}")
        try:
            errors, notes = check_examples(
                validator, examples_found, read_names(args.allowed), read_names(args.pending)
            )
        except Unresolvable as exc:
            print(f"internal error: a schema refers to a file outside the pinned set: {exc}", file=sys.stderr)
            return 3
    except Exception as exc:  # a broken check must never pass as a clean one
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    for note in notes:
        print(f"note: {note}")
    for error in errors:
        print(f"::error title=Smart Data Models conformance::{error}")
    if errors:
        print(f"{len(errors)} problem(s).")
        return 1
    print("OK: every example conforms or is on the pending list.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
