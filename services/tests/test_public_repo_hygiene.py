"""The guard that keeps operational detail out of this repo.

Pins what it must catch and, just as importantly, what it must not: a guard
that fires on ordinary code gets switched off.

This repo is installed on other people's servers, so the rule is not "do not
name our deployment" -- it is "name no deployment at all". A hostname belongs in
configuration; only the software's own upstream dependencies may appear here.
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

ALL_RULES = hygiene.STRUCTURAL
ALLOWED = hygiene.load_allowed_hosts()


def _diff(path: str, *added: str) -> str:
    body = "".join(f"+{line}\n" for line in added)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n{body}"


def test_the_marker_is_per_line_not_per_file():
    """An exception must sit next to what it excuses, where review sees it."""
    assert not hygiene.scan(_diff("x.py", "sha256:" + "d" * 64 + "  # hygiene:allow"), ALL_RULES, ALLOWED)
    assert hygiene.scan(_diff("x.py", "sha256:" + "d" * 64), ALL_RULES, ALLOWED)


def test_the_check_names_no_deployment():
    """It must work for any installation, so it knows no one's hostnames."""
    src = _SCRIPT.read_text()
    assert "allowed-hosts.txt" in src
    allowed = hygiene.load_allowed_hosts()
    assert allowed, "the dependency list must load"


@pytest.mark.parametrize("host", ["localhost", "orion-ld-service", "github.com",
                                  "smartdatamodels.org", "api.open-meteo.com"])
def test_the_software_may_name_its_own_dependencies(host):
    assert hygiene.host_is_allowed(host, hygiene.load_allowed_hosts()), host


@pytest.mark.parametrize("host", ["app.acme-farms.io", "gateway.somewhere.cloud",  # hygiene:allow
                                  "n8n.somewhere.cloud"])
def test_a_deployment_hostname_is_rejected(host):
    assert not hygiene.host_is_allowed(host, hygiene.load_allowed_hosts()), host


@pytest.mark.parametrize(
    "path,line",
    [
        ("src/app.ts", 'const API = "https://api.acme-farms.io"'),  # hygiene:allow
        ("NOTES.md", "see https://console.acme-farms.io"),   # markdown is scanned  # hygiene:allow
        ("docs/deploy.md", "image: ghcr.io/x@sha256:" + "a" * 64),
        ("services/x.py", "# run kubectl get pods -n foo"),  # hygiene:allow
        ("k8s/svc.yaml", "  externalIP: 198.18.0.7"),  # hygiene:allow RFC 2544 range
    ],
)
def test_operational_detail_is_caught(path, line):
    assert hygiene.scan(_diff(path, line), ALL_RULES, ALLOWED), f"missed: {line}"


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
    assert not hygiene.scan(_diff("services/x.py", line), ALL_RULES, ALLOWED), line


def test_removed_lines_are_not_flagged():
    """Only additions. Deleting a leak must never fail the build."""
    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n"
        '-API = "https://api.acme-farms.io"\n'  # hygiene:allow
        '+API = os.getenv("API_URL")\n'
    )
    assert not hygiene.scan(diff, ALL_RULES, ALLOWED)


def test_generated_files_are_skipped():
    assert not hygiene.scan(_diff("apps/host/coverage/x.js", "https://a.acme-farms.io"), ALL_RULES, ALLOWED)  # hygiene:allow
    assert not hygiene.scan(_diff("pnpm-lock.yaml", "https://a.acme-farms.io"), ALL_RULES, ALLOWED)  # hygiene:allow


def test_the_finding_says_what_to_do_instead():
    out = hygiene.scan(_diff("x.py", 'A = "https://api.acme-farms.io"'), ALL_RULES, ALLOWED)[0]  # hygiene:allow
    assert "Instead:" in out and "configuration" in out



