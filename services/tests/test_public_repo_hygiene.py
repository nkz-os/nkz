"""The guard that keeps operational detail out of this repo.

Pins what it must catch and, just as importantly, what it must not: a guard
that fires on ordinary code gets switched off.
"""

import importlib.util
import pathlib

import pytest

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2]
    / ".github" / "scripts" / "check_public_repo_hygiene.py"
)
_spec = importlib.util.spec_from_file_location("hygiene", _SCRIPT)
hygiene = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hygiene)


def _diff(path: str, *added: str) -> str:
    body = "".join(f"+{line}\n" for line in added)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n{body}"


@pytest.mark.parametrize(
    "path,line",
    [
        ("src/app.ts", 'const API = "https://nkz.robotika.cloud"'),
        ("PENDING.md", "- montiko: 16 EOProduct entities"),          # .md must be scanned
        ("docs/deploy.md", "image: ghcr.io/x@sha256:" + "a" * 64),
        ("services/x.py", '# run kubectl get pods -n nekazari'),
        ("k8s/svc.yaml", "  externalIP: 109.123.252.120"),
    ],
)
def test_operational_detail_is_caught(path, line):
    assert hygiene.scan(_diff(path, line)), f"missed: {line}"


@pytest.mark.parametrize(
    "line",
    [
        "# The broker rejects an attribute fragment with a body @context.",
        'ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")',
        'tenant_id = "tenant-a"',
        "connect to 10.43.0.1 inside the cluster",      # private range
        "listen on 0.0.0.0:8080",
        "see https://smartdatamodels.org/dataModel.Weather/gustSpeed",
    ],
)
def test_ordinary_code_and_rationale_pass(line):
    assert not hygiene.scan(_diff("services/x.py", line)), f"false positive: {line}"


def test_removed_lines_are_not_flagged():
    """Only additions. Deleting a leak must never fail the build."""
    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n"
        '-API = "https://nkz.robotika.cloud"\n'
        '+API = os.getenv("API_URL")\n'
    )
    assert not hygiene.scan(diff)


def test_generated_files_are_skipped():
    assert not hygiene.scan(_diff("apps/host/coverage/x.js", "montiko"))
    assert not hygiene.scan(_diff("pnpm-lock.yaml", "montiko"))


def test_markdown_is_not_excluded():
    """Prose is scanned too: markdown carries the same detail as code."""
    assert hygiene.scan(_diff("notes.md", "tenant montiko, digest sha256:" + "b" * 64))


def test_the_finding_says_what_to_do_instead():
    out = hygiene.scan(_diff("x.py", 'T = "montiko"'))[0]
    assert "Instead:" in out and "placeholder" in out
