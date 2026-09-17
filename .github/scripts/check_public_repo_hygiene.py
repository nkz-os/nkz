#!/usr/bin/env python3
"""Fail a diff that adds operational detail to a public repo.

Rules alone did not hold. The task list reached this repo inside an unrelated
frontend PR and stayed for three months; the domain check that should have
caught it excluded `*.md` and looked for one pattern. So the rule is enforced
here instead of being remembered: anything this finds in *added* lines blocks
the merge.

Diff-based on purpose. Existing occurrences are not flagged, so the guard can
land without a repo-wide cleanup first, and every new one is stopped.

Reads a unified diff on stdin. Exit 1 when something is found.
Deliberate additions are acknowledged with [prod-refs-ack] in the commit
message, handled by the caller.
"""

from __future__ import annotations

import re
import sys

# (name, pattern, why it must not be here, what to do instead)
RULES: list[tuple[str, re.Pattern, str, str]] = [
    (
        "production domain",
        re.compile(r"\b[a-z0-9-]+\.robotika\.cloud\b"),
        "names the running deployment",
        "read it from an env var, or use YOUR_DOMAIN",
    ),
    (
        "tenant name",
        re.compile(r"\b(?:montiko|allotarra)\b", re.IGNORECASE),
        "a real customer tenant",
        "use a placeholder such as tenant-a, t1 or acme",
    ),
    (
        "image digest",
        re.compile(r"\bsha256:[0-9a-f]{64}\b"),
        "pins a specific build of the running cluster",
        "digests belong in the private deploy config",
    ),
    (
        "cluster command",
        re.compile(r"\b(?:kubectl|argocd)\s+(?:get|apply|exec|patch|delete|logs|sync|set)\b"),
        "an operator runbook, not source",
        "put runbooks in the internal notes",
    ),
    (
        "public IP",
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b(?<!0\.0\.0\.0)"),
        "may be a production address",
        "use a hostname from configuration",
    ),
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


def scan(diff: str) -> list[str]:
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
        for name, pattern, why, instead in RULES:
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
    findings = scan(sys.stdin.read())
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
