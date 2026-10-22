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


def test_the_marker_is_per_line_not_per_file():
    """An exception must sit next to what it excuses, where review sees it."""
    assert not hygiene.scan(_diff("x.py", "sha256:" + "d" * 64 + "  # hygiene:allow"), ALL_RULES)
    assert hygiene.scan(_diff("x.py", "sha256:" + "d" * 64), ALL_RULES)


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
        ("services/x.py", "# run kubectl get pods -n foo"),  # hygiene:allow
        ("k8s/svc.yaml", "  externalIP: 198.18.0.7"),  # hygiene:allow RFC 2544 range
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


def test_a_private_term_is_never_printed():
    """CI logs of a public repo are public: the finding must not echo the term."""
    out = hygiene.scan(_diff("x.py", 'T = "acme-tenant"'), ALL_RULES)
    assert out and all("acme-tenant" not in f for f in out)
    assert hygiene.MASKED in out[0]


def test_the_marker_never_excuses_a_private_term():
    assert hygiene.scan(_diff("x.py", 'T = "acme-tenant"  # hygiene:allow'), ALL_RULES)


# Built at run time so this file's own diff carries no address the guard flags.
_AT = "@"


@pytest.mark.parametrize("line", [
    "contact: jane.doe" + _AT + "acme-farm.eu",
    "billing = 'billing" + _AT + "coop-sample.es'",
])
def test_a_real_looking_email_is_caught_masked(line):
    out = hygiene.scan(_diff("x.py", line), hygiene.STRUCTURAL)
    address = line.split()[-1].strip("'")
    assert out and hygiene.MASKED in out[0] and address not in out[0]


@pytest.mark.parametrize("line", [
    "user@example.com",
    "ops@tenant-a.test",
    "git@github.com:owner/repo.git",
    "123+bot@users.noreply.github.com",
    'import x from "react@18.3.1"',
])
def test_placeholder_emails_and_package_specs_pass(line):
    assert not hygiene.scan(_diff("x.py", line), hygiene.STRUCTURAL), line


def test_a_private_finding_exits_2(monkeypatch, capsys):
    """2 means the caller must fail even with an acknowledgement marker."""
    import io
    monkeypatch.setenv("HYGIENE_PRIVATE_TERMS", TERMS)
    monkeypatch.setattr("sys.stdin", io.StringIO(_diff("x.py", 'T = "acme-tenant"')))
    assert hygiene.main() == 2
    assert "acme-tenant" not in capsys.readouterr().out


def test_operational_detail_alone_exits_1(monkeypatch):
    import io
    monkeypatch.delenv("HYGIENE_PRIVATE_TERMS", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(_diff("x.py", "sha256:" + "e" * 64)))
    assert hygiene.main() == 1


@pytest.mark.parametrize("line", [
    'ORION_URL = os.getenv("ORION_URL", "https://api.farm-sample.eu")',
    "see https://portal.coop-sample.es/docs",
    "host: mqtt.some-cluster.io",
])
def test_a_hostname_outside_the_dependencies_is_caught(line):
    out = hygiene.scan(_diff("services/x.py", line), hygiene.STRUCTURAL)
    assert out and "hostname" in out[0] and "allowed-hosts.txt" in out[0], line


@pytest.mark.parametrize("line", [
    "see https://smartdatamodels.org/dataModel.Agrifood",   # listed dependency
    "image: ghcr.io/nkz-os/nkz/host:latest",                 # listed, subdomain
    "api: https://api.open-meteo.com/v1/forecast",           # listed, subdomain
    "CORS_ORIGINS=https://app.example.com,https://auth.YOUR_DOMAIN",
    "orion: http://orion-ld-service.nekazari.svc.cluster.local:1026",
    "logger.info('x'); to.be.true; env.dev; baselogger.info",  # attribute access, not hosts
    "version 1.2.3 of package.json",
])
def test_dependencies_placeholders_and_code_pass_the_hostname_rule(line):
    assert not hygiene.scan(_diff("services/x.py", line), hygiene.STRUCTURAL), line


def test_the_allowed_hosts_file_exists_and_lists_no_placeholder_domain():
    hosts = hygiene.allowed_hosts()
    assert "smartdatamodels.org" in hosts
    assert not any(h.endswith(("example.com", ".test", ".invalid")) for h in hosts)


def test_a_private_term_can_be_excluded_by_path():
    rules = hygiene.private_rules("acme-tenant,udara|exclude=*/eu.json;**/eu/*.json")
    assert hygiene.scan(_diff("src/locales/es.json", '"label": "udara"'), rules)
    assert not hygiene.scan(_diff("src/locales/eu.json", '"label": "udara"'), rules)
    assert not hygiene.scan(_diff("src/locales/eu/common.json", '"label": "udara"'), rules)
    assert hygiene.scan(_diff("src/locales/eu.json", "acme-tenant"), rules), "the exclusion is per term"
