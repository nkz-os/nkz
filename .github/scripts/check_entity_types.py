#!/usr/bin/env python3
"""Closed list of NGSI-LD entity types.

Every entity type the platform uses must be on `config/entity-types/allowed.txt`
(Smart Data Models) or on `config/entity-types/legacy.txt`, which may only
shrink: a `## broker` block (types already persisted in the broker) and a
`## code` block (types named in code only).

This module holds the list parsing and loading; subcommands build on it.

Exit codes: 0 clean, 1 findings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
