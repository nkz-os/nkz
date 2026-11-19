#!/usr/bin/env python3
"""Closed list of NGSI-LD entity types.

Every entity type the platform uses must be on `config/entity-types/allowed.txt`
(Smart Data Models) or on `config/entity-types/legacy.txt`, which may only
shrink: a `## broker` block (types already persisted in the broker) and a
`## code` block (types named in code only).

This module holds the list parsing and loading, the `diff` subcommand, which
reads a unified diff on stdin and reports unknown type names on added lines, and
the `ratchet` subcommand, which compares the lists with their state on a base ref.

Exit codes: 0 clean, 1 findings.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ALLOWED_FILE = "allowed.txt"
LEGACY_FILE = "legacy.txt"
BROKER_HEADER = "## broker"
CODE_HEADER = "## code"


@dataclass(frozen=True)
class Lists:
    allowed: frozenset[str]
    legacy_broker: frozenset[str]
    legacy_code: frozenset[str]

    @property
    def known(self) -> frozenset[str]:
        return self.allowed | self.legacy_broker | self.legacy_code


def parse_allowed(text: str) -> list[str]:
    """One name per line; blank lines and `#` comments are ignored."""
    names: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            names.append(line)
    return names


def parse_legacy(text: str) -> tuple[list[str], list[str]]:
    """Return (broker, code) names; a name before any block header is an error."""
    blocks: dict[str, list[str]] = {BROKER_HEADER: [], CODE_HEADER: []}
    current: list[str] | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line in blocks:
            current = blocks[line]
        elif not line or line.startswith("#"):
            continue
        elif current is None:
            raise ValueError(f"line {number}: name {line!r} before a '## broker' or '## code' header")
        else:
            current.append(line)
    return blocks[BROKER_HEADER], blocks[CODE_HEADER]


def load_lists(directory: Path) -> Lists:
    broker, code = parse_legacy((directory / LEGACY_FILE).read_text(encoding="utf-8"))
    return Lists(
        allowed=frozenset(parse_allowed((directory / ALLOWED_FILE).read_text(encoding="utf-8"))),
        legacy_broker=frozenset(broker),
        legacy_code=frozenset(code),
    )


NAME = r"[A-Z][A-Za-z0-9]+"
NOT_A_TYPE_MARKER = "sdm:not-a-type"
SELF_PATH = ".github/scripts/check_entity_types.py"

_URN = re.compile(rf"urn:ngsi-ld:({NAME}):")
_TYPE_KEY = re.compile(rf"[Tt]ype[\"']?\]?\s*(?::|==|=)\s*[\"']({NAME})[\"']")
_TYPE_QUERY = re.compile(r"[?&]type=([^&\s\"']+)")
_NKZ = re.compile(rf"\bnkz:({NAME})(?![A-Za-z0-9])")
_NKZ_IRI = re.compile(rf"https://nkz-os\.org/ns/({NAME})(?![A-Za-z0-9])")
_ENTITIES = re.compile(r"\bentities\s*:\s*\[([^\]]*)\]")
_QUOTED_NAME = re.compile(rf"[\"'](?:nkz:)?({NAME})[\"']")
_FULL_NAME = re.compile(rf"(?:nkz:)?({NAME})")


def extract_type_names(line: str) -> list[str]:
    """Candidate entity type names in one line, in order of appearance, no duplicates."""
    found: list[tuple[int, str]] = []
    for rx in (_URN, _TYPE_KEY, _NKZ, _NKZ_IRI):
        found.extend((m.start(1), m.group(1)) for m in rx.finditer(line))
    for m in _TYPE_QUERY.finditer(line):
        offset = m.start(1)
        for item in m.group(1).split(","):
            full = _FULL_NAME.fullmatch(item)
            if full:
                found.append((offset + item.rfind(full.group(1)), full.group(1)))
            offset += len(item) + 1
    for m in _ENTITIES.finditer(line):
        found.extend((m.start(1) + q.start(1), q.group(1)) for q in _QUOTED_NAME.finditer(m.group(1)))
    names: list[str] = []
    for _, name in sorted(found):
        if name not in names:
            names.append(name)
    return names


# Strings that look like names but are never an entity type: NGSI-LD and GeoJSON
# vocabulary, JSON/SQL/Kubernetes words, and labels in other languages.
VOCABULARY = frozenset({
    "Property", "Relationship", "GeoProperty", "Point", "Polygon", "MultiPolygon", "MultiPoint", "LineString",
    "MultiLineString", "Feature", "FeatureCollection", "Subscription", "Notification", "Bearer", "DateTime",
    "Date", "Time", "Integer", "String", "Boolean", "Number", "Object", "Array", "JSON", "Text", "Float",
    "GeometryCollection", "LanguageProperty", "VocabProperty", "ListProperty", "URL", "Deployment", "Service",
    "ConfigMap", "Secret", "Ingress", "CronJob", "Job", "Opaque", "ClusterIP", "RollingUpdate", "Recreate",
    "Directory", "DirectoryOrCreate", "File", "Linear", "Module", "Program", "Literal", "Identifier", "ES256",
    "RS256", "List", "Query", "Unknown", "Type", "Tipo", "Tipus", "Mota",
})


def is_vocabulary(name: str) -> bool:
    return name.isupper() or name.endswith("Type") or name in VOCABULARY


EXCLUDED_DIR_SEGMENTS = frozenset({
    "test", "tests", "__tests__", "e2e", "fixtures", "__mocks__", "node_modules", "locales", "i18n",
    "docs", "k8s",
})
EXCLUDED_FILE_PATTERNS = ("conftest.py", "test_*.py", "*_test.py", "*.test.*", "*.spec.*", "package-lock.json")
EXCLUDED_SUFFIXES = (".md", ".svg", ".lock", ".min.js", ".yaml", ".yml")
EXCLUDED_PREFIXES = ("config/entity-types/", ".hygiene-tooling/")


def path_is_scanned(path: str) -> bool:
    if path == SELF_PATH or path.startswith(EXCLUDED_PREFIXES) or path.endswith(EXCLUDED_SUFFIXES):
        return False
    *directories, filename = path.split("/")
    if any(segment in EXCLUDED_DIR_SEGMENTS for segment in directories):
        return False
    return not any(fnmatch.fnmatchcase(filename, pattern) for pattern in EXCLUDED_FILE_PATTERNS)


@dataclass(frozen=True)
class Finding:
    path: str
    line_no: int
    name: str
    text: str


_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def scan_diff(diff: str, lists: Lists) -> list[Finding]:
    """Unknown entity type names on the added lines of a unified diff."""
    known = lists.known
    findings: list[Finding] = []
    path: str | None = None
    old_left = new_left = 0
    new_no = 0
    for raw in diff.splitlines():
        if old_left <= 0 and new_left <= 0:
            if raw.startswith("+++ "):
                target = raw[4:].split("\t")[0]
                path = target[2:] if target.startswith("b/") else None
            elif raw.startswith("diff --git "):
                path = None
            else:
                hunk = _HUNK.match(raw)
                if hunk:
                    old_left = int(hunk.group(1)) if hunk.group(1) is not None else 1
                    new_no = int(hunk.group(2))
                    new_left = int(hunk.group(3)) if hunk.group(3) is not None else 1
            continue
        if raw.startswith("\\"):
            continue
        marker = raw[:1]
        if marker == "+":
            new_left -= 1
            if path is not None and path_is_scanned(path):
                content = raw[1:]
                if NOT_A_TYPE_MARKER not in content:
                    for name in extract_type_names(content):
                        if not is_vocabulary(name) and name not in known:
                            findings.append(Finding(path, new_no, name, content.strip()))
            new_no += 1
        elif marker == "-":
            old_left -= 1
        else:
            old_left -= 1
            new_left -= 1
            new_no += 1
    return findings


CLOSING_PARAGRAPH = """\
Entity types must come from Smart Data Models. To use an existing model, add its name to
config/entity-types/allowed.txt in nkz-os/nkz first. A type outside Smart Data Models cannot be added.
If the string is not an entity type, put 'sdm:not-a-type' on that line."""


def run_diff(lists_dir: Path, diff: str) -> int:
    findings = scan_diff(diff, load_lists(lists_dir))
    for f in findings:
        print(f"{f.path}:{f.line_no}: entity type '{f.name}' is not in the lists")
    if findings:
        print()
        print(CLOSING_PARAGRAPH)
        return 1
    return 0


OFFICIAL_LIST_URL = (
    "https://raw.githubusercontent.com/smart-data-models/data-models/master/specs/AllSubjects/"
    "official_list_data_models.json"
)


def fetch_official_names(url: str = OFFICIAL_LIST_URL, timeout: int = 30) -> set[str]:
    """Every data model name in the official Smart Data Models list."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed https URL
            payload = json.loads(response.read().decode("utf-8"))
        return {name for entry in payload["officialList"] for name in entry["dataModels"]}
    except Exception as exc:  # network, HTTP, JSON or shape errors all mean the same thing here
        raise RuntimeError(f"could not download the official list: {exc}") from exc


def context_iri(context: dict, name: str) -> str | None:
    """The IRI a short name expands to in the platform context, or None if it has no term."""
    terms: dict = {}
    for part in context.get("@context", []):
        if isinstance(part, dict):
            terms.update(part)

    def resolve(term: str, depth: int = 0) -> str | None:
        value = terms.get(term)
        if isinstance(value, dict):
            value = value.get("@id")
        if not isinstance(value, str):
            return None
        prefix, sep, rest = value.partition(":")
        if sep and not rest.startswith("//") and prefix in terms and depth < 5:
            base = resolve(prefix, depth + 1)
            if base:
                return base + rest
        return value

    return resolve(name)


def _duplicates(names: list[str]) -> list[str]:
    seen: set[str] = set()
    repeated: list[str] = []
    for name in names:
        if name in seen and name not in repeated:
            repeated.append(name)
        seen.add(name)
    return repeated


def check_ratchet(
    base_allowed: str | None,
    base_legacy: str | None,
    head_allowed: str,
    head_legacy: str,
    official_names: Callable[[], set[str]],
) -> list[str]:
    """One message per offence; a base of None means the file is new (no growth check)."""
    problems: list[str] = []
    allowed = parse_allowed(head_allowed)
    broker, code = parse_legacy(head_legacy)

    if base_legacy is not None:
        base_broker, base_code = parse_legacy(base_legacy)
        old = set(base_broker) | set(base_code)
        for name in dict.fromkeys(broker + code):
            if name not in old:
                problems.append(f"legacy.txt may only lose names: '{name}' was added")

    base_names = set(parse_allowed(base_allowed)) if base_allowed is not None else set()
    added = [name for name in dict.fromkeys(allowed) if name not in base_names]
    if added:
        try:
            official = official_names()
        except RuntimeError as exc:
            problems.append(str(exc))
        else:
            problems.extend(f"'{n}' is not in the official list of Smart Data Models" for n in added if n not in official)

    legacy = broker + code
    for name in sorted(set(allowed) & set(legacy)):
        problems.append(f"'{name}' is in both allowed.txt and legacy.txt")
    for name in sorted(set(broker) & set(code)):
        problems.append(f"'{name}' is in both the broker and the code block of legacy.txt")
    for label, names in (("allowed.txt", allowed), ("legacy.txt broker block", broker), ("legacy.txt code block", code)):
        problems.extend(f"{label}: duplicate '{name}'" for name in _duplicates(names))
        if names != sorted(names):
            problems.append(f"{label}: names are not sorted")
    return problems


def _git_show(ref: str, path: str) -> str | None:
    result = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else None


def run_ratchet(base: str, lists_dir: Path) -> int:
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
    ).stdout.strip()
    relative = lists_dir.resolve().relative_to(Path(top).resolve()).as_posix()
    problems = check_ratchet(
        _git_show(base, f"{relative}/{ALLOWED_FILE}"),
        _git_show(base, f"{relative}/{LEGACY_FILE}"),
        (lists_dir / ALLOWED_FILE).read_text(encoding="utf-8"),
        (lists_dir / LEGACY_FILE).read_text(encoding="utf-8"),
        fetch_official_names,
    )
    for problem in problems:
        print(problem)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    diff_cmd = commands.add_parser("diff", help="check the added lines of a unified diff read on stdin")
    diff_cmd.add_argument("--lists", type=Path, required=True, help="directory with allowed.txt and legacy.txt")
    ratchet_cmd = commands.add_parser("ratchet", help="check the lists against their state on a base ref")
    ratchet_cmd.add_argument("--base", required=True, help="git ref to compare against")
    ratchet_cmd.add_argument("--lists", type=Path, required=True, help="directory with allowed.txt and legacy.txt")
    args = parser.parse_args(argv)
    if args.command == "diff":
        return run_diff(args.lists, sys.stdin.read())
    if args.command == "ratchet":
        return run_ratchet(args.base, args.lists)
    return 2


if __name__ == "__main__":
    sys.exit(main())
