#!/usr/bin/env python3
"""Fail a diff that adds operational detail to a public repo.

Operational detail belongs in the private deploy configuration. This enforces
that mechanically rather than by convention: anything found in *added* lines
blocks the merge, including in markdown.

Diff-based on purpose, so it applies to new work without requiring a repo-wide
pass first.

Reads a unified diff on stdin. Exit 1 when something is found.
Deliberate additions are acknowledged with [prod-refs-ack] in the commit
message, handled by the caller.
"""

from __future__ import annotations

import os
import re
import sys

# Structural rules: shapes, not values. Safe to keep here because a digest or a
# kubectl invocation looks the same everywhere and names nothing on its own.
STRUCTURAL: list[tuple[str, re.Pattern, str, str]] = [
    (
        "image digest",
        re.compile(r"\bsha256:[0-9a-f]{64}\b"),
        "pins a specific build of the running system",
        "digests belong in the private deploy configuration",
    ),
    (
        "cluster command",
        re.compile(r"\b(?:kubectl|argocd)\s+(?:get|apply|exec|patch|delete|logs|sync|set)\b"),
        "an operator runbook, not source",
        "put runbooks in the internal notes",
    ),
    (
        "public IP",
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        "may be an address of the running system",
        "use a hostname from configuration",
    ),
]

# The values themselves -- tenant names, real domains -- are private, so they are
# supplied at run time and never written down here. CI passes them from an
# organisation secret, which also means a finding prints them masked.
PRIVATE_TERMS_ENV = "HYGIENE_PRIVATE_TERMS"


def private_rules(raw: str | None) -> list[tuple[str, re.Pattern, str, str]]:
    terms = [t.strip() for t in (raw or "").split(",") if t.strip()]
    if not terms:
        return []
    joined = "|".join(re.escape(t) for t in terms)
    return [
        (
            "private term",
            re.compile(rf"(?<![\w.-])(?:{joined})(?![\w-])", re.IGNORECASE),
            "names the real deployment or one of its tenants",
            "use a placeholder such as tenant-a, t1 or YOUR_DOMAIN",
        )
    ]


# Generated or vendored files nobody writes by hand.
SKIP_PATH = re.compile(
    r"(?:^|/)(?:node_modules|dist|coverage|\.venv)/|"
    r"\.(?:lock|png|jpe?g|gif|ico|svg|woff2?)$|"
    r"(?:^|/)pnpm-lock\.yaml$"
)

# Private ranges and loopback are cluster-internal examples, not addresses of
# the deployment; documentation ranges are explicitly for examples.
ALLOWED_IP = re.compile(
    r"^(?:10\.|127\.|169\.254\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|"
    r"0\.0\.0\.0|255\.|224\.|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)"
)


def scan(diff: str, rules: list | None = None) -> list[str]:
    findings: list[str] = []
    path = "<unknown>"
    skipping = False
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            skipping = bool(SKIP_PATH.search(path))
            continue
        if line.startswith(("---", "+++", "diff ", "index ", "@@")):
            continue
        if skipping or not line.startswith("+"):
            continue
        added = line[1:]
        for name, pattern, why, instead in (rules if rules is not None else STRUCTURAL):
            for m in pattern.finditer(added):
                hit = m.group(0)
                if name == "public IP" and ALLOWED_IP.match(hit):
                    continue
                findings.append(
                    f"{path}: {name} '{hit}' — {why}. Instead: {instead}\n"
                    f"    {added.strip()[:120]}"
                )
    return findings


def main() -> int:
    raw = os.getenv(PRIVATE_TERMS_ENV)
    rules = STRUCTURAL + private_rules(raw)
    if not raw:
        print(
            f"note: {PRIVATE_TERMS_ENV} is unset, so only structural rules ran. "
            "Names of real tenants and domains are not checked."
        )
    findings = scan(sys.stdin.read(), rules)
    if not findings:
        print("OK: no operational detail added.")
        return 0
    print(f"{len(findings)} addition(s) carry operational detail:\n")
    for f in findings:
        print(f"  {f}")
    print(
        "\nThis is a public repo. The technical rationale can stay; the "
        "infrastructure it names cannot. If an addition is genuinely needed, "
        "put [prod-refs-ack] in the commit message."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
