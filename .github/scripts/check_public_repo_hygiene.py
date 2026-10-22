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

import fnmatch
import os
import pathlib
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

# An email address may identify a person. Placeholder and service domains are
# allowed; everything else is treated like a private term.
EMAIL = (
    "email address",
    re.compile(r"\b[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})\b"),
    "may identify a person",
    "use user@example.com",
)
ALLOWED_EMAIL_DOMAIN = re.compile(
    r"(?:^|\.)(?:example\.(?:com|org|net)|test|invalid|local|localhost|"
    r"github\.com|noreply\.github\.com|users\.noreply\.github\.com)$",
    re.IGNORECASE,
)
STRUCTURAL.append(EMAIL)

# A hostname names a deployment unless it is a dependency of the software
# (a spec, a registry, a data source). The permitted hosts live next to this
# script, one per line, and cover their subdomains; placeholders are built in.
# Only common public TLDs are considered, and none that collides with ordinary
# attribute access in code (logger.info, to.be, env.dev).
ALLOWED_HOSTS_FILE = pathlib.Path(__file__).resolve().parent.parent / "allowed-hosts.txt"
PLACEHOLDER_HOST = re.compile(
    r"(?:^|\.)(?:example\.(?:com|org|net)|ejemplo\.com|test|invalid|local|localhost|"
    r"svc|cluster\.local|internal)$",
    re.IGNORECASE,
)
HOST = (
    "hostname",
    re.compile(
        r"(?<![\w@.-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
        r"(?:com|org|net|io|cloud|eu|es|de|fr|it|pt|nl|ch|uk|eus|cat|gal|int|gov|edu))"
        r"(?![\w(-])",
        re.IGNORECASE,
    ),
    "names a host outside the software's own dependencies",
    "put the address in configuration, or add the dependency to "
    ".github/allowed-hosts.txt",
)
STRUCTURAL.append(HOST)


def allowed_hosts(path: pathlib.Path = ALLOWED_HOSTS_FILE) -> set[str]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return set()
    return {l.strip().lower() for l in lines if l.strip() and not l.startswith("#")}


ALLOWED_HOSTS = allowed_hosts()


def host_allowed(host: str, allowed: set[str] | None = None) -> bool:
    host = host.lower()
    if PLACEHOLDER_HOST.search(host):
        return True
    for permitted in (ALLOWED_HOSTS if allowed is None else allowed):
        if host == permitted or host.endswith("." + permitted):
            return True
    return False

# Private findings. They are printed masked (CI logs of a
# public repo are public, and only the whole secret is masked by the runner, not
# each term), cannot be excused with the per-line marker, and make the script
# exit 2 so the caller cannot acknowledge them either.
PRIVATE_FINDINGS = {"private term", "email address"}
MASKED = "(masked)"

# The values themselves -- tenant names, real domains -- are private, so they are
# supplied at run time and never written down here. CI passes them from an
# organisation secret, which also means a finding prints them masked.
# A term may be followed by "|exclude=<glob>[;<glob>...]" to leave out paths
# where the same word is legitimate (a locale file, say); a rule then carries
# those globs as a fifth element.
PRIVATE_TERMS_ENV = "HYGIENE_PRIVATE_TERMS"

# Opt-out marker for a single line (see scan()).
FIXTURE_MARKER = "hygiene:allow"


def private_rules(raw: str | None) -> list[tuple]:
    rules: list[tuple] = []
    plain: list[str] = []
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        term, _, options = entry.partition("|")
        excludes = [g.strip() for opt in options.split("|") if opt.startswith("exclude=")
                    for g in opt[len("exclude="):].split(";") if g.strip()]
        if excludes:
            rules.append(_term_rule([term.strip()], excludes))
        else:
            plain.append(term.strip())
    if plain:
        rules.insert(0, _term_rule(plain, []))
    return rules


def _term_rule(terms: list[str], excludes: list[str]) -> tuple:
    joined = "|".join(re.escape(t) for t in terms)
    rule = (
        "private term",
        re.compile(rf"(?<![\w.-])(?:{joined})(?![\w-])", re.IGNORECASE),
        "names the real deployment or one of its tenants",
        "use a placeholder such as tenant-a, t1 or YOUR_DOMAIN",
    )
    return rule + (tuple(excludes),) if excludes else rule


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
        # A line may opt out with an explicit marker. Per line, never per file,
        # so the exception is visible in review next to what it covers. Used by
        # this guard's own fixtures, which have to contain what it looks for.
        # It never excuses a private finding.
        marked = FIXTURE_MARKER in added
        for rule in (rules if rules is not None else STRUCTURAL):
            name, pattern, why, instead = rule[:4]
            excludes = rule[4] if len(rule) > 4 else ()
            if any(fnmatch.fnmatch(path, g) for g in excludes):
                continue
            private = name in PRIVATE_FINDINGS
            if marked and not private:
                continue
            for m in pattern.finditer(added):
                hit = m.group(0)
                if name == "public IP" and ALLOWED_IP.match(hit):
                    continue
                if name == "email address" and ALLOWED_EMAIL_DOMAIN.search(m.group(1)):
                    continue
                if name == "hostname" and host_allowed(m.group(1)):
                    continue
                if private:
                    findings.append(f"{path}: {name} {MASKED} — {why}. Instead: {instead}")
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
    if any(MASKED in f for f in findings):
        print(
            "\nA private finding cannot be acknowledged: remove it and use a "
            "synthetic value."
        )
        return 2
    print(
        "\nThis is a public repo. The technical rationale can stay; the "
        "infrastructure it names cannot. If an addition is genuinely needed, "
        "put [prod-refs-ack] in the commit message."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
