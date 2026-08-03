"""Characterization ("golden") test suite for the api-gateway.

Purpose: freeze the CURRENT observable behavior of every route registered on
``fiware_api_gateway.app`` (plus the ``module_routes``/``storage`` blueprints)
so the upcoming gateway refactor (Phase 0 of the hardening workstream) can be
verified against a known-good baseline. This suite makes NO judgment about
whether the current behavior is correct — it only records it.

Regenerating the golden file
-----------------------------
    GOLDEN_UPDATE=1 PYTHONPATH=<services> python3 -m pytest \
        tests/test_gateway_characterization.py -q

Every route (Flask rule) is probed once per HTTP method it accepts (limited
to GET/POST/PUT/PATCH/DELETE — OPTIONS/HEAD are not characterized). URL
parameters are substituted with fixed dummy values so every rule maps to a
single concrete path. All outbound HTTP from the gateway (the ``requests``
module it imports) is patched to return a fixed 599 status — this makes any
route that would proxy to a backend *without requiring authentication*
plainly visible in the golden file as 599, which is itself a signal about
that route's auth posture and must not be hidden.
"""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

_services_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (
    _services_dir,
    os.path.join(_services_dir, "common"),
    os.path.join(_services_dir, "api-gateway"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "gateway_routes.json")

# HTTP methods this suite characterizes. OPTIONS (CORS preflight) and HEAD
# are excluded — they are handled generically by Flask/CORS and are not
# meaningful per-route signal here.
CHARACTERIZED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


# =============================================================================
# Fixture: import the gateway app with mandatory env vars mocked.
#
# Mirrors the exact pattern in test_gateway_hardening.py — same env vars,
# same psycopg2 stub-via-sys.modules approach, same reload order
# (keycloak_auth then fiware_api_gateway). Keeping this identical avoids a
# second, subtly-different import path for the same module in the test
# suite.
# =============================================================================
@pytest.fixture
def gateway(monkeypatch):
    """Import the gateway module with mandatory env vars mocked."""
    monkeypatch.setenv("ORION_URL", "http://orion-test:1026")
    monkeypatch.setenv("KEYCLOAK_URL", "http://keycloak-test:8080")
    monkeypatch.setenv("CONTEXT_URL", "http://context-test/ngsi-context.jsonld")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-for-testing-only")
    monkeypatch.setenv("HMAC_SECRET", "test-hmac-secret")
    monkeypatch.setenv("KEYCLOAK_REALM", "nekazari")
    monkeypatch.setenv("TRUST_API_GATEWAY", "false")
    monkeypatch.setenv("ALLOW_JWT_FALLBACK", "false")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pass@localhost:5432/db")

    sys.modules["psycopg2"] = MagicMock()
    sys.modules["psycopg2.extras"] = MagicMock()

    import importlib

    import keycloak_auth

    importlib.reload(keycloak_auth)

    import fiware_api_gateway as gw

    importlib.reload(gw)
    return gw


class _FakeUpstreamResponse:
    """Deterministic stand-in for a ``requests.Response``.

    status_code=599 is not a real HTTP status — it's chosen precisely
    because no real backend or error handler in the gateway would ever
    produce it, so its appearance in the golden file unambiguously means
    "this route reached out to a backend without an auth gate blocking it
    first". text is empty per the design spec; content/headers/json are
    filled in only so routes that touch those attributes (dict(resp.headers),
    resp.json(), resp.content) don't blow up with an AttributeError, which
    would just hide the 599 signal behind an unrelated 500.
    """

    status_code = 599
    text = ""
    content = b""
    headers: dict = {}

    def json(self):
        return {}


def _patch_outbound_requests(gateway, monkeypatch):
    """Block all real outbound HTTP from the gateway module.

    Patches every requests function actually used in fiware_api_gateway.py
    (get/post/put/patch/delete/request) so nothing ever leaves the process
    during this suite, regardless of auth state.
    """
    fake_call = MagicMock(return_value=_FakeUpstreamResponse())
    for fn_name in ("get", "post", "put", "patch", "delete", "request"):
        monkeypatch.setattr(gateway.requests, fn_name, fake_call)


def _build_concrete_path(rule_str: str) -> str:
    """Substitute Flask/Werkzeug URL converters with fixed dummy values.

    <path:x>  -> "dummy/sub"   (multi-segment)
    <int:x>   -> "1"
    <x>       -> "dummy"       (default string converter, any other name)
    """
    import re

    pattern = re.compile(r"<(?:(?P<conv>[a-zA-Z_]+):)?(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)>")

    def _repl(m):
        conv = m.group("conv")
        if conv == "path":
            return "dummy/sub"
        if conv == "int":
            return "1"
        return "dummy"

    return pattern.sub(_repl, rule_str)


def _collect_route_statuses(gateway) -> dict:
    """Enumerate every (rule, method) and record the unauthenticated status.

    No Authorization header, no cookies, no body is sent — this is a pure
    "what does an anonymous caller get" sweep.
    """
    client = gateway.app.test_client()
    statuses = {}
    for rule in gateway.app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        methods = (rule.methods or set()) & set(CHARACTERIZED_METHODS)
        for method in methods:
            key = f"{method} {rule.rule}"
            path = _build_concrete_path(rule.rule)
            resp = client.open(path, method=method)
            statuses[key] = resp.status_code
    return statuses


# =============================================================================
# Public allowlist — routes that legitimately return 2xx with NO
# Authorization header, each with the reason it's intentionally public.
# Format: "<METHOD> <rule>": "why".
# =============================================================================
PUBLIC_ALLOWLIST_UNAUTH_2XX = {
    # n8n root landing page — static text, no tenant/user data.
    "GET /": "n8n tenant-proxy landing page; static text, no sensitive data.",
    # K8s liveness/readiness probe target — must be reachable pre-auth or the
    # pod never becomes Ready.
    "GET /health": "K8s health probe endpoint; must work without auth.",
    # Service version banner — no sensitive data.
    "GET /version": "Service version endpoint; no sensitive data.",
    # Logout: clearing a cookie that may not even exist is idempotent and
    # carries no information disclosure or state-mutation risk.
    "DELETE /api/auth/session": "Logout/cookie-clear; idempotent and harmless without a session.",
    # Explicitly named + documented in code as public (needed pre-login).
    "GET /api/public/platform-settings": "Documented public endpoint, needed before login (code comment: 'Public endpoint for non-sensitive platform settings used before login').",
    # Explicitly named + documented in code as public (registration flow).
    "GET /api/terms/<language>": "Documented public endpoint for terms & conditions used during registration.",
    # Documented in code: tile URL's job UUID is itself the unguessable
    # access token; Cesium's tile loader can't send httpOnly cookies anyway.
    "GET /api/vegetation/tiles/<path:path>": "Documented public tile proxy; job UUID in the URL is the access token (Cesium can't send cookies).",
    # Documented in code: Orion-LD subscription webhook, no JWT by design —
    # auth is deferred to the greenhouse-dt backend via the subscription
    # payload itself.
    "POST /api/ngsi-ld/notify": "Documented Orion-LD subscription webhook; no JWT by design, validated downstream by greenhouse-dt.",
}

# Routes that return 2xx with NO Authorization header and do NOT look
# intentionally public (mutating, and/or literally no auth check of any
# kind). Frozen here so this characterization suite passes deterministically
# on CURRENT behavior — judging/fixing them is the refactor orchestrator's
# job, not this suite's.
#
# *** SUSPICIOUS — REVIEW BEFORE/DURING REFACTOR ***
# (empty — POST /internal/cache/invalidate was fixed to require
# X-Internal-Service-Secret via hmac.compare_digest(), so it no longer
# returns 2xx unauthenticated. See golden: now 401.)
SUSPICIOUS_UNAUTH_2XX = {}


def test_gateway_characterization_matches_golden(gateway, monkeypatch):
    """Freeze (or verify against) the golden file of unauthenticated
    responses for every route+method on the gateway.

    With GOLDEN_UPDATE=1 this (re)writes the golden file and passes.
    Otherwise it asserts the current sweep matches the committed golden
    file exactly, with a clear listing of any mismatches, additions, or
    removals.
    """
    _patch_outbound_requests(gateway, monkeypatch)
    actual = _collect_route_statuses(gateway)

    assert len(actual) > 150, (
        f"Expected roughly 86 routes x ~2 methods each (~190+ combos), got "
        f"only {len(actual)}. The gateway app or blueprints may have failed "
        f"to load fully — investigate before trusting this golden run."
    )

    if os.environ.get("GOLDEN_UPDATE") == "1":
        os.makedirs(os.path.dirname(GOLDEN_PATH), exist_ok=True)
        with open(GOLDEN_PATH, "w") as f:
            json.dump(actual, f, indent=2, sort_keys=True)
            f.write("\n")
        return

    with open(GOLDEN_PATH) as f:
        golden = json.load(f)

    golden_keys = set(golden)
    actual_keys = set(actual)

    changed = {
        k: {"golden": golden[k], "actual": actual[k]}
        for k in (golden_keys & actual_keys)
        if golden[k] != actual[k]
    }
    added = sorted(actual_keys - golden_keys)
    removed = sorted(golden_keys - actual_keys)

    if changed or added or removed:
        lines = ["Gateway characterization drifted from golden/gateway_routes.json:"]
        if changed:
            lines.append(f"\n{len(changed)} status changed:")
            for k in sorted(changed):
                lines.append(f"  {k}: golden={changed[k]['golden']} actual={changed[k]['actual']}")
        if added:
            lines.append(f"\n{len(added)} route(s) present now but not in golden (new route, or golden is stale):")
            for k in added:
                lines.append(f"  + {k}: {actual[k]}")
        if removed:
            lines.append(f"\n{len(removed)} route(s) in golden but not observed now (removed route, or golden is stale):")
            for k in removed:
                lines.append(f"  - {k}: {golden[k]}")
        lines.append(
            "\nIf this drift is an intentional part of the refactor, "
            "regenerate with: GOLDEN_UPDATE=1 python3 -m pytest "
            "services/tests/test_gateway_characterization.py"
        )
        pytest.fail("\n".join(lines))


def test_no_unauthenticated_2xx(gateway, monkeypatch):
    """No route may return 2xx with no Authorization header, except the
    explicit, justified allowlist above.

    This is a characterization test, not a security gate: anything found
    here that isn't obviously fine is still allowlisted (tagged SUSPICIOUS)
    rather than failed, because judging/fixing it is the refactor
    orchestrator's job. What this test DOES guarantee is that the refactor
    can't silently make a NEW route unauthenticated-2xx without that being
    a visible, reviewed diff to this file.
    """
    _patch_outbound_requests(gateway, monkeypatch)
    actual = _collect_route_statuses(gateway)

    observed_2xx = {k for k, status in actual.items() if 200 <= status < 300}
    allowed = set(PUBLIC_ALLOWLIST_UNAUTH_2XX) | set(SUSPICIOUS_UNAUTH_2XX)

    unexpected = sorted(observed_2xx - allowed)
    assert not unexpected, (
        "Route(s) return 2xx with NO Authorization header and are NOT in "
        "either PUBLIC_ALLOWLIST_UNAUTH_2XX or SUSPICIOUS_UNAUTH_2XX in "
        "this file:\n"
        + "\n".join(f"  {k} -> {actual[k]}" for k in unexpected)
        + "\n\nIf this is intentionally public, add it to "
        "PUBLIC_ALLOWLIST_UNAUTH_2XX with a reason. If it looks like an "
        "auth gap, add it to SUSPICIOUS_UNAUTH_2XX tagged for review — do "
        "not silently let it pass unlisted."
    )
