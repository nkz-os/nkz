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

`validate` subcommand: the same validation for a repository that only holds the
examples of its own entities (a module). Every example must conform, or its type
be listed in `pending.txt` next to the examples; nothing is required about types
the repository has no example of.

`deviations` subcommand: every official attribute or type that the platform
context maps to another IRI than the official one must be recorded, one by one,
in `config/sdm/deviations.json`; nothing else may be recorded there.

`ratchet` subcommand: against a base git ref, the pending list and the recorded
deviations may only shrink.

Exit codes: 0 clean, 1 findings, 2 bad arguments or unresolved base ref,
3 internal error (download, checksum, reference outside the pinned set,
unexpected exception).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
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


# ── deviations ──────────────────────────────────────────────────────────────


def schema_properties(schema: dict, shared: dict[str, Any]) -> set[str]:
    """Attribute names an official schema defines, following `allOf` and pinned `$ref`s."""
    names: set[str] = set()
    seen: set[tuple[int, str]] = set()

    def visit(node: Any, document: dict) -> None:
        if not isinstance(node, dict):
            return
        names.update(node.get("properties", {}))
        for key in ("allOf", "anyOf", "oneOf"):
            for part in node.get(key, []):
                visit(part, document)
        ref = node.get("$ref")
        if not isinstance(ref, str):
            return
        base, _, pointer = ref.partition("#")
        target_document = shared.get(base) if base else document
        if target_document is None:
            raise PinError(f"schema reference outside the pinned set: {base}")
        marker = (id(target_document), pointer)
        if marker in seen:
            return
        seen.add(marker)
        target: Any = target_document
        for token in (part for part in pointer.split("/") if part):
            target = target.get(token) if isinstance(target, dict) else None
        visit(target, target_document)

    visit(schema, schema)
    return names - {"id", "type"}


def compute_deviations(pins: dict, files: dict[str, Any], platform_context: Any, allowed: Iterable[str]) -> dict:
    """Where the platform context departs from the official IRIs, for the allowed official types.

    `types`: type name -> {platform, official}. `attributes`: a sorted list of
    {term, platform, official, types}: one entry per official IRI of a term,
    with the types whose official model uses it. A term of the NGSI-LD core
    context always means the core IRI, whatever the platform context says:
    that is how the broker stores it (measured on Orion-LD for `status`).

    An official attribute that the platform context does not declare at all is
    not listed: nothing is mapped. It shows up as `attribute-outside-context`
    as soon as an entity carries it.

    Smart Data Models gives some names a different IRI in each subject, and a
    context can map a name only once. An entry whose platform IRI is the
    official one for other allowed types carries them in `officialFor`: that
    deviation cannot be avoided, only moved.
    """
    platform = context_terms(platform_context)
    core = context_terms(files["core"])
    shared = {ref: files[ref] for ref in pins["shared"]}
    types: dict[str, dict[str, str]] = {}
    by_pair: dict[tuple[str, str, str], list[str]] = {}
    conforming: dict[tuple[str, str], list[str]] = {}
    for name in sorted(set(allowed) & set(pins["types"])):
        subject_terms = context_terms(files[f"context:{pins['types'][name]['subject']}"])
        official_type = subject_terms.get(name)
        mapped_type = platform.get(name)
        if official_type and mapped_type and mapped_type != official_type:
            types[name] = {"platform": mapped_type, "official": official_type}
        for term in sorted(schema_properties(files[f"schema:{name}"], shared)):
            official = subject_terms.get(term)
            mapped = core.get(term, platform.get(term))
            if official is None or mapped is None:
                continue
            if mapped == official:
                conforming.setdefault((term, mapped), []).append(name)
                continue
            by_pair.setdefault((term, mapped, official), []).append(name)
    attributes = []
    for (term, mapped, official), names in sorted(by_pair.items()):
        entry = {"term": term, "platform": mapped, "official": official, "types": names}
        if (term, mapped) in conforming:
            entry["officialFor"] = conforming[(term, mapped)]
        attributes.append(entry)
    return {"types": types, "attributes": attributes}


def deviation_keys(deviations: dict) -> set[tuple[str, str, str, str]]:
    """Each recorded deviation as (kind, name, type, official IRI)."""
    keys = {("type", name, name, entry["official"]) for name, entry in deviations.get("types", {}).items()}
    for entry in deviations.get("attributes", []):
        keys.update(("attribute", entry["term"], type_name, entry["official"]) for type_name in entry["types"])
    return keys


def check_deviations(computed: dict, recorded: dict) -> list[str]:
    """Problems between what the context does and what the deviations file records."""
    errors: list[str] = []
    now, written = deviation_keys(computed), deviation_keys(recorded)
    for kind, name, type_name, official in sorted(now - written):
        errors.append(f"{type_name}: {kind} {name} is not mapped to its official IRI {official} and is not recorded")
    for kind, name, type_name, official in sorted(written - now):
        errors.append(f"{type_name}: {kind} {name} is recorded as a deviation but no longer is one; remove it")
    if not errors and computed != recorded:
        errors.append("the deviations file does not match the computed one; regenerate it with `deviations --write`")
    return errors


def _at_ref(base: str, path: Path) -> str | None:
    """A file's content at a git ref; None only when the file did not exist there.

    The path is taken relative to the repository root, so a file outside the
    repository, or any git failure, is an error and never reads as "new file".
    """
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
    inside = path.resolve().relative_to(Path(top.stdout.strip()).resolve()).as_posix()
    listed = subprocess.run(["git", "ls-tree", "--name-only", base, "--", inside], capture_output=True, text=True, check=True)
    if not listed.stdout.strip():
        return None
    return subprocess.run(["git", "show", f"{base}:{inside}"], capture_output=True, text=True, check=True).stdout


def _names(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")}


def check_ratchet(base: str, pending: Path, deviations: Path, allowed: Path | None = None) -> list[str]:
    """Names and deviations that exist now and did not exist at the base ref.

    A type that joins the allowed list after the base ref brings the deviations of
    the terms it shares with the rest; those are accepted, and visible in the diff.
    So is a deviation whose platform IRI is the official one for another allowed
    type (`officialFor`): the name is defined twice upstream and one side must lose.
    An already allowed type may not gain any other.
    """
    errors: list[str] = []
    newly_allowed: set[str] = set()
    if allowed is not None:
        old_allowed = _at_ref(base, allowed)
        if old_allowed is not None:
            newly_allowed = set(read_names(allowed)) - _names(old_allowed)
    old_pending = _at_ref(base, pending)
    if old_pending is not None:
        for name in sorted(set(read_names(pending)) - _names(old_pending) - newly_allowed):
            errors.append(f"{name}: added to the pending list, which may only shrink")
    old_deviations = _at_ref(base, deviations)
    if old_deviations is not None:
        before_keys = deviation_keys(json.loads(old_deviations))
        recorded = json.loads(deviations.read_text(encoding="utf-8"))
        current = deviation_keys(recorded)
        forced = {("attribute", entry["term"], type_name, entry["official"])
                  for entry in recorded.get("attributes", []) if entry.get("officialFor")
                  for type_name in entry["types"]}
        for kind, name, type_name, official in sorted(current - before_keys):
            if type_name in newly_allowed or (kind, name, type_name, official) in forced:
                continue
            errors.append(f"{type_name}: {kind} {name} added to the recorded deviations, which may only shrink")
    return errors


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
    validator: Validator,
    examples: Iterable[tuple[str, dict]],
    allowed: Iterable[str],
    pending: Iterable[str],
    complete: bool = True,
) -> tuple[list[str], list[str]]:
    """(errors, notes) for a set of example entities.

    Errors fail the check; notes report what the pending list still covers.
    `complete` is for the repository that owns the allowed list: every official
    type must then have an example or be pending. Without it only the examples
    given are judged, and any type that has an example may be pending.
    """
    errors: list[str] = []
    notes: list[str] = []
    allowed = list(allowed)
    pending = set(pending)
    official = validator.official_types()

    if complete:
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

    if complete:
        for name in sorted(official & set(allowed)):
            if name not in by_type and name not in pending:
                errors.append(f"{name}: official type with no example and not on the pending list")
    else:
        for name in sorted(pending - set(by_type)):
            errors.append(f"{name}: on the pending list but there is no example of it")
    return errors, notes


def _report(errors: list[str], notes: list[str], ok: str, level: str = "error") -> int:
    for note in notes:
        print(f"note: {note}")
    for error in errors:
        print(f"::{level} title=Smart Data Models conformance::{error}")
    if errors:
        print(f"{len(errors)} problem(s).")
        return 1
    print(ok)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--pins", type=Path, default=Path("config/sdm/pins.json"))
        command.add_argument("--context", type=Path, default=Path("config/ngsi-ld-context.json"))
        command.add_argument("--allowed", type=Path, default=Path("config/entity-types/allowed.txt"))
        command.add_argument("--pending", type=Path, default=Path("config/sdm/pending.txt"))
        command.add_argument("--deviations", type=Path, default=Path("config/sdm/deviations.json"))
        command.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "sdm-conformance")

    examples = commands.add_parser("examples", help="validate the example entities of a directory")
    examples.add_argument("directory", type=Path)
    common(examples)
    validate = commands.add_parser("validate", help="validate a module's own example entities")
    validate.add_argument("directory", type=Path)
    validate.add_argument("--annotate", choices=("error", "warning"), default="error",
                          help="how findings are annotated; the exit code is the same either way")
    common(validate)
    deviations = commands.add_parser("deviations", help="check the recorded deviations from the official IRIs")
    deviations.add_argument("--write", action="store_true", help="rewrite the deviations file from the context")
    common(deviations)
    ratchet = commands.add_parser("ratchet", help="the pending list and the deviations may only shrink")
    ratchet.add_argument("--base", required=True, help="git ref to compare with")
    common(ratchet)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        if args.command == "ratchet":
            resolved = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}"], capture_output=True, text=True
            )
            if resolved.returncode != 0:
                print(f"base ref {args.base} does not resolve", file=sys.stderr)
                return 2
            return _report(check_ratchet(args.base, args.pending, args.deviations, args.allowed), [],
                           "OK: the pending list and the deviations did not grow.")

        from referencing.exceptions import Unresolvable

        pins = load_pins(args.pins)
        files = fetch(pins, args.cache)
        platform_context = json.loads(args.context.read_text(encoding="utf-8"))
        if args.command == "deviations":
            computed = compute_deviations(pins, files, platform_context, read_names(args.allowed))
            if args.write:
                args.deviations.write_text(json.dumps(computed, indent=2) + "\n", encoding="utf-8")
                print(f"wrote {args.deviations}")
                return 0
            recorded = json.loads(args.deviations.read_text(encoding="utf-8"))
            return _report(check_deviations(computed, recorded), [],
                           f"OK: {len(deviation_keys(recorded))} recorded deviations, none missing.")

        validator = Validator(pins, files, platform_context)
        examples_found = read_examples(args.directory)
        if not examples_found:
            raise FileNotFoundError(f"no example entities (*.json) in {args.directory}")
        complete = args.command == "examples"
        if complete:
            pending = read_names(args.pending)
        else:
            own_pending = args.directory / "pending.txt"
            pending = read_names(own_pending) if own_pending.exists() else []
        try:
            errors, notes = check_examples(validator, examples_found, read_names(args.allowed), pending, complete)
        except Unresolvable as exc:
            print(f"internal error: a schema refers to a file outside the pinned set: {exc}", file=sys.stderr)
            return 3
    except Exception as exc:  # a broken check must never pass as a clean one
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    return _report(errors, notes, "OK: every example conforms or is on the pending list.",
                   getattr(args, "annotate", "error"))


if __name__ == "__main__":
    sys.exit(main())
