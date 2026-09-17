"""The guard that keeps operational detail out of this repo.

Pins what it must catch and, just as importantly, what it must not: a guard
that fires on ordinary code gets switched off.

Every fixture below is synthetic. The real terms are supplied at run time from
private configuration and are deliberately absent from this file.
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

# Stand-ins for the private terms CI injects.
TERMS = "acme-tenant,example-corp,app.example.invalid"
ALL_RULES = hygiene.STRUCTURAL + hygiene.private_rules(TERMS)


def _diff(path: str, *added: str) -> str:
    body = "".join(f"+{line}\n" for line in added)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n{body}"


def test_the_script_holds_no_private_values():
    """The whole point: shapes live here, values do not."""
    src = _SCRIPT.read_text()
    assert "HYGIENE_PRIVATE_TERMS" in src
    assert not hygiene.private_rules(None), "no terms must mean no name rule"


@pytest.mark.parametrize(
    "path,line",
    [
        ("src/app.ts", 'const API = "https://app.example.invalid"'),
        ("NOTES.md", "- acme-tenant: 16 records"),            # markdown is scanned
        ("docs/deploy.md", "image: ghcr.io/x@sha256:" + "a" * 64),
        ("services/x.py", "# run kubectl get pods -n foo"),
        ("k8s/svc.yaml", "  externalIP: 198.18.0.7"),   # RFC 2544 benchmark range
    ],
)
def test_operational_detail_is_caught(path, line):
    assert hygiene.scan(_diff(path, line), ALL_RULES), f"missed: {line}"


@pytest.mark.parametrize(
    "line",
    [
        "# The broker rejects an attribute fragment with a body @context.",
        'ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")',
        'tenant_id = "tenant-a"',
        "see https://smartdatamodels.org/dataModel.Weather/gustSpeed",
    ],
)
def test_ordinary_code_and_rationale_pass(line):
    assert not hygiene.scan(_diff("services/x.py", line), ALL_RULES), line


def test_removed_lines_are_not_flagged():
    """Only additions. Deleting a leak must never fail the build."""
    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n"
        '-API = "https://app.example.invalid"\n'
        '+API = os.getenv("API_URL")\n'
    )
    assert not hygiene.scan(diff, ALL_RULES)


def test_generated_files_are_skipped():
    assert not hygiene.scan(_diff("apps/host/coverage/x.js", "acme-tenant"), ALL_RULES)
    assert not hygiene.scan(_diff("pnpm-lock.yaml", "acme-tenant"), ALL_RULES)


def test_the_finding_says_what_to_do_instead():
    out = hygiene.scan(_diff("x.py", 'T = "acme-tenant"'), ALL_RULES)[0]
    assert "Instead:" in out and "placeholder" in out


def test_without_terms_only_shapes_are_checked():
    """A missing secret must not silently disable the structural half."""
    only = hygiene.STRUCTURAL
    assert not hygiene.scan(_diff("x.py", 'T = "acme-tenant"'), only)
    assert hygiene.scan(_diff("x.py", "sha256:" + "c" * 64), only)
