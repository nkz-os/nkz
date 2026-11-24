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
  single-value-for-array     only when reading a broker: the schema asks for an array and the
                             broker returned one value. A broker may store a one-element array
                             as its element, and in JSON-LD the two are the same value, so the
                             value is validated as that array; reported apart, never as a failure

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

`upstream` subcommand: every pinned file is compared with the one its repository
serves today at its default branch. A difference is not an error of this
repository: it says the official model moved on and the pin is due a review.

`broker` subcommand: the same validation over everything one tenant holds in a
context broker, read with GET only. Each finding is reduced to a signature
(type, kind, place and rule, with no entity id and no value) and compared with
a baseline file: a signature the baseline lacks is a failure, a baseline line
that no longer occurs is reported so the file can lose it. An array the broker
returned as one value is validated as that array and listed apart.

Exit codes: 0 clean, 1 findings, 2 bad arguments or unresolved base ref,
3 internal error (download, checksum, reference outside the pinned set, a
broker that could not be read, unexpected exception).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import http.client
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

RAW = "https://raw.githubusercontent.com/{repo}/{commit}/{path}"
COMPARE = "https://github.com/{repo}/compare/{commit}...{ref}"
UPSTREAM_REF = "HEAD"  # the default branch, whatever each repository calls it
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


class BrokerError(RuntimeError):
    """The broker could not be read: never the same as "nothing found"."""


class PinError(RuntimeError):
    """A pinned file is missing, cannot be fetched, or does not match its checksum."""


@dataclass(frozen=True)
class Finding:
    entity_id: str
    entity_type: str
    kind: str
    where: str
    message: str
    rule: str = ""  # for a schema finding: the keyword it breaks, with what makes it a distinct defect

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


def upstream_files(pins: dict) -> dict[str, tuple[str, str, str]]:
    """Every pinned repository file as key -> (URL at the default branch, pinned sha256, where to see the changes).

    The core context is left out: its URL carries no commit, so every `fetch` already checks it.
    """
    files: dict[str, tuple[str, str, str]] = {}

    def add(key: str, repo: str, commit: str, path: str, digest: str) -> None:
        url = RAW.format(repo=repo, commit=UPSTREAM_REF, path=path)
        files[key] = (url, digest, COMPARE.format(repo=repo, commit=commit, ref=UPSTREAM_REF))

    for subject, entry in pins["subjects"].items():
        add(f"context:{subject}", entry["repo"], entry["commit"], entry["context"]["path"], entry["context"]["sha256"])
    for name, entry in pins["types"].items():
        subject = pins["subjects"].get(entry["subject"])
        if subject is None:
            raise PinError(f"type {name}: unknown subject {entry['subject']}")
        add(f"schema:{name}", subject["repo"], subject["commit"], entry["schema"]["path"], entry["schema"]["sha256"])
    for ref, entry in pins["shared"].items():
        add(ref, entry["repo"], entry["commit"], entry["path"], entry["sha256"])
    return files


def check_upstream(pins: dict, download: Callable[[str], bytes] | None = None) -> list[str]:
    """One line per pinned file that its repository no longer serves unchanged.

    A download that fails for any reason other than the file being gone raises
    `PinError`: an unreachable repository must not read as "nothing changed".
    """
    download = download or _download
    findings = []
    for key, (url, digest, compare) in upstream_files(pins).items():
        try:
            data = download(url)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise PinError(f"{key}: could not fetch {url}: {exc}") from exc
            findings.append(f"{key}: no longer at its pinned path in the official repository ({compare})")
            continue
        except Exception as exc:
            raise PinError(f"{key}: could not fetch {url}: {exc}") from exc
        if hashlib.sha256(data).hexdigest() != digest:
            findings.append(f"{key}: the official file changed since the pinned commit ({compare})")
    return findings


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


_MISSING_PROPERTY = re.compile(r"^'(.+)' is a required property$")


def _rule(error: Any) -> str:
    """The broken keyword, plus the missing attribute or the expected JSON type: never the offending value."""
    keyword = str(error.validator)
    if keyword == "required":
        missing = _MISSING_PROPERTY.match(error.message)
        return f"required {missing.group(1)}" if missing else keyword
    if keyword == "type":
        expected = error.validator_value
        names = expected if isinstance(expected, list) else [expected]
        return "type " + ",".join(str(name) for name in names)
    return keyword


def _wrap_single_values(validator: Any, values: dict) -> list[str]:
    """Wrap, in place, each value that is not a list where the schema asks for an array; the places wrapped.

    Repeated because a wrapped value can hold another one further down. What is
    wrapped is then validated as the array: an element that is itself wrong stays a finding.
    """
    wrapped: list[str] = []
    for _ in range(10):
        pending = [error for error in validator.iter_errors(values)
                   if error.validator == "type" and error.absolute_path and not isinstance(error.instance, list)
                   and "array" in (error.validator_value if isinstance(error.validator_value, list)
                                   else [error.validator_value])]
        if not pending:
            break
        for error in pending:
            path = list(error.absolute_path)
            holder: Any = values
            for part in path[:-1]:
                holder = holder[part]
            if not isinstance(holder[path[-1]], list):
                holder[path[-1]] = [holder[path[-1]]]
                wrapped.append("/".join(str(part) for part in path))
    return sorted(set(wrapped))


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

    def check_entity(self, entity: dict, single_for_array: bool = False) -> list[Finding]:
        from jsonschema import Draft7Validator

        entity_id = str(entity.get("id", "?"))
        kind = entity.get("type")
        name = kind if isinstance(kind, str) else "?"
        found: list[Finding] = []

        def add(what: str, where: str, message: str, rule: str = "") -> None:
            found.append(Finding(entity_id, name, what, where, message, rule))

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
            values = key_values(entity)
            if single_for_array:
                for where in _wrap_single_values(validator, values):
                    add("single-value-for-array", where, "one value where the schema asks for an array")
            for error in sorted(validator.iter_errors(values), key=lambda e: list(map(str, e.absolute_path))):
                where = "/".join(str(part) for part in error.absolute_path) or "(entity)"
                message = error.message if len(error.message) <= 200 else error.message[:197] + "..."
                add("schema", where, f"{error.validator}: {message}", _rule(error))
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


# ── a tenant read from the broker ───────────────────────────────────────────

BROKER_PAGE = 1000
_LIST_INDEX = re.compile(r"(^|/)\d+(?=/|$)")


def signature(finding: Finding) -> str:
    """What a finding is about, without what changes from one entity or one day to the next.

    `type | kind | place`, and for a schema finding its rule: the JSON Schema
    keyword it breaks, with the missing attribute or the expected type.
    """
    parts = [finding.entity_type, finding.kind, finding.where]
    if finding.kind == "schema":
        parts[2] = _LIST_INDEX.sub(r"\1*", finding.where)
        if finding.rule:
            parts.append(finding.rule)
    return " | ".join(parts)


def _broker_get(url: str, headers: dict[str, str], *, opener: Callable | None = None,
                sleep: Callable[[float], None] | None = None, attempts: int = 5, delay: float = 10.0,
                timeout: float = 60.0) -> Any:
    """One GET, parsed. Connection failures and 5xx are retried with a growing wait,

    because a job can start before its network policy is programmed. Every way of
    not getting an answer ends in `BrokerError`; messages carry no address.
    """
    opener = opener or urllib.request.urlopen
    sleep = sleep or time.sleep
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise BrokerError("the broker address needs an http or https scheme")
    request = urllib.request.Request(url, headers=headers, method="GET")
    last: Exception | None = None
    for attempt in range(attempts):
        if attempt:
            sleep(delay * attempt)
        try:
            with opener(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:  # before OSError: it is a subclass
            if exc.code < 500 and exc.code not in (408, 429):
                raise BrokerError(f"the broker answered HTTP {exc.code}") from exc
            last = exc
            continue
        except (OSError, http.client.HTTPException) as exc:
            last = exc
            continue
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise BrokerError("the broker answer is not JSON") from exc
    raise BrokerError(f"the broker could not be reached ({type(last).__name__})") from last


def _tenant_headers(tenant: str) -> dict[str, str]:
    return {"NGSILD-Tenant": tenant, "Fiware-Service": tenant, "Fiware-ServicePath": "/", "Accept": "application/json"}


def broker_types(orion_url: str, tenant: str, **transport: Any) -> list[str]:
    """The IRIs one tenant stores entity types under. No `Link`: the `id` of each row is the stored IRI."""
    query = urllib.parse.urlencode({"details": "true", "limit": BROKER_PAGE})
    body = _broker_get(f"{orion_url.rstrip('/')}/ngsi-ld/v1/types?{query}", _tenant_headers(tenant), **transport)
    if not isinstance(body, list) or not all(isinstance(row, dict) and isinstance(row.get("id"), str) for row in body):
        raise BrokerError("the broker answer is not a list of types")
    if len(body) >= BROKER_PAGE:
        raise BrokerError("the list of types fills a page: there may be more")
    return [row["id"] for row in body]


def broker_entities(orion_url: str, tenant: str, type_iri: str, link_context: str, *, page: int = BROKER_PAGE,
                    **transport: Any) -> Iterable[dict]:
    """Every entity of one stored type, normalized, with names compacted by the platform context."""
    headers = _tenant_headers(tenant)
    headers["Link"] = f'<{link_context}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'
    entities: list[dict] = []
    while True:
        query = urllib.parse.urlencode({"type": type_iri, "limit": page, "offset": len(entities)})
        body = _broker_get(f"{orion_url.rstrip('/')}/ngsi-ld/v1/entities?{query}", headers, **transport)
        if not isinstance(body, list) or not all(isinstance(entity, dict) for entity in body):
            raise BrokerError("the broker answer is not a list of entities")
        entities.extend(body)
        if len(body) < page:
            return entities


def read_baseline(path: Path) -> list[str]:
    """Signatures of a baseline file: one per line, `#` comments and blank lines ignored."""
    return read_names(path)


def check_broker(validator: Validator, entities: Iterable[dict], baseline: Iterable[str]) -> tuple[
        list[str], list[str], list[str], list[str]]:
    """(new, stale, current, single): what the baseline lacks, what it can lose, every signature found,

    and apart from all three the arrays the broker returned as one value.
    """
    seen: dict[str, list] = {}
    single: dict[str, list] = {}
    for entity in entities:
        findings = validator.check_entity(entity, single_for_array=True)
        for found in {signature(finding): finding for finding in findings}.values():
            target = single if found.kind == "single-value-for-array" else seen
            entry = target.setdefault(signature(found), [0, found.entity_id])
            entry[0] += 1

    def lines(counted: dict[str, list], names: Iterable[str]) -> list[str]:
        return [f"{name}: {counted[name][0]} entities, e.g. {counted[name][1]}" for name in sorted(names)]

    known = set(baseline)
    return lines(seen, set(seen) - known), sorted(known - set(seen)), sorted(seen), lines(single, single)


def _report(errors: list[str], notes: list[str], ok: str, level: str = "error", title: str = "conformance") -> int:
    for note in notes:
        print(f"note: {note}")
    for error in errors:
        print(f"::{level} title=Smart Data Models {title}::{error}")
    if errors:
        print(f"{len(errors)} problem(s).")
        return 1
    print(ok)
    return 0


def _broker_command(args: argparse.Namespace, validator: Validator) -> int:
    baseline = [] if args.print_signatures else read_baseline(args.baseline)
    types = broker_types(args.orion_url, args.tenant)
    if not types:
        # A tenant name is typed by hand: a wrong one would otherwise read as clean every day.
        raise BrokerError("the tenant holds no entity types")
    count = 0

    def every_entity() -> Iterable[dict]:
        nonlocal count
        for type_iri in types:
            for entity in broker_entities(args.orion_url, args.tenant, type_iri, args.link_context):
                count += 1
                yield entity

    new, stale, current, single = check_broker(validator, every_entity(), baseline)
    if not count:
        # The broker lists a type only while it holds entities of it: reading none back is a failed read.
        raise BrokerError("the tenant lists entity types but no entity could be read")
    if args.print_signatures:
        for name in current:
            print(name, flush=True)
        return 0
    for line in single:
        print(f"SINGLE {line}", flush=True)
    for name in stale:
        print(f"STALE {name}: no longer found, the baseline can lose it", flush=True)
    for line in new:
        print(f"NEW {line}", flush=True)
    print(f"{args.tenant}: {len(types)} types, {count} entities, {len(current)} signatures, "
          f"{len(new)} new, {len(stale)} stale, {len(single)} single values", flush=True)
    return 1 if new else 0


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
    upstream = commands.add_parser("upstream", help="compare the pinned files with the official repositories today")
    common(upstream)
    broker = commands.add_parser("broker", help="validate what one tenant holds in a context broker")
    broker.add_argument("--orion-url", required=True, help="base URL of the broker")
    broker.add_argument("--tenant", required=True)
    broker.add_argument("--link-context", required=True, help="URL of the platform context, as the broker reaches it")
    broker.add_argument("--baseline", type=Path, required=True, help="signatures already known, one per line")
    broker.add_argument("--print-signatures", action="store_true",
                        help="print the current signatures, to write a first baseline, and exit")
    common(broker)
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

        if args.command == "upstream":
            pins = load_pins(args.pins)
            return _report(check_upstream(pins), [],
                           f"OK: the {len(upstream_files(pins))} pinned files are the ones served upstream today.",
                           level="warning", title="upstream")

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
        if args.command == "broker":
            return _broker_command(args, validator)
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
