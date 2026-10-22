#!/usr/bin/env python3
"""Row-level-security ratchet for config/timescaledb.

Every table a new migration creates must name its owner role (`ALTER TABLE …
OWNER TO nkz_core` or a module role: the owner skips ENABLE policies but not
FORCE, so a table without an explicit owner would belong to the superuser and
sit outside the model) and, when it carries `tenant_id`, enable row-level
security with a policy in the same file. The list of tables under FORCE ROW
LEVEL SECURITY (`rls_forced_tables.txt`) may only grow.

Usage: check_rls_ratchet.py --base <git ref>
Exit 1 with one line per offender.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

MIG_DIR = Path("config/timescaledb/migrations")
FORCED = Path("config/timescaledb/rls_forced_tables.txt")
OWNER_ROLES = r"(?:nkz_core|nkz_module_[a-z0-9_]+)"

CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?((?:[a-z_][\w]*\.)?[a-z_][\w]*)\s*\(",
    re.IGNORECASE,
)
TEMP_RE = re.compile(r"CREATE\s+(?:TEMP|TEMPORARY)\s+TABLE", re.IGNORECASE)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL)


def _bare(name: str) -> str:
    return name.split(".")[-1].lower()


def _statement_after(text: str, start: int) -> str:
    end = text.find(";", start)
    return text[start : end if end != -1 else len(text)]


def check_migration(path: Path, text: str) -> list[str]:
    offenders: list[str] = []
    for m in CREATE_RE.finditer(text):
        prefix = text[max(0, m.start() - 20) : m.start() + 12]
        if TEMP_RE.search(prefix):
            continue
        table = m.group(1)
        bare = _bare(table)
        body = _statement_after(text, m.start())
        owner_re = re.compile(
            rf"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:[a-z_]\w*\.)?{bare}\s+OWNER\s+TO\s+{OWNER_ROLES}",
            re.IGNORECASE,
        )
        if not owner_re.search(text):
            offenders.append(f"{path}: {table}: missing ALTER TABLE ... OWNER TO nkz_core|nkz_module_<id>")
        if re.search(r"\btenant_id\b", body, re.IGNORECASE):
            enable_re = re.compile(
                rf"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:[a-z_]\w*\.)?{bare}\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
                re.IGNORECASE,
            )
            policy_re = re.compile(rf"CREATE\s+POLICY\s+\w+\s+ON\s+(?:[a-z_]\w*\.)?{bare}\b", re.IGNORECASE)
            if not enable_re.search(text):
                offenders.append(f"{path}: {table}: has tenant_id but no ENABLE ROW LEVEL SECURITY")
            if not policy_re.search(text):
                offenders.append(f"{path}: {table}: has tenant_id but no CREATE POLICY")
    return offenders


def _forced(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")}


def check_forced_list(base: str) -> list[str]:
    try:
        before = _forced(_git("show", f"{base}:{FORCED.as_posix()}"))
    except subprocess.CalledProcessError:
        before = set()
    after = _forced(FORCED.read_text()) if FORCED.exists() else set()
    return [f"{FORCED}: {t}: removed from the forced list (the ratchet only grows)" for t in sorted(before - after)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    args = ap.parse_args()
    added = _git("diff", "--name-only", "--diff-filter=A", args.base, "--", MIG_DIR.as_posix()).split()
    offenders: list[str] = []
    for rel in added:
        p = Path(rel)
        if p.suffix.lower() != ".sql" or not p.exists():
            continue
        offenders += check_migration(p, p.read_text())
    offenders += check_forced_list(args.base)
    for line in offenders:
        print(line)
    if offenders:
        print(f"rls ratchet: {len(offenders)} problem(s)")
        return 1
    print("rls ratchet: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
