"""Las expansiones almacenadas en el broker deben coincidir con el @context vigente.

Una suscripción guarda el tipo ya expandido en Orion y no lo re-expande nunca. Si el
contexto cambia, queda huérfana y deja de disparar en silencio. El test de contrato estático
no lo ve: valida código, no el broker.

Contrato vigente (segunda versión — la primera trataba cualquier término compactado como
sano, lo que es un falso negativo: ver `blueprints/diagnostics.py`): las suscripciones se
piden a Orion SIN el @context de la plataforma en el Link, solo con el core context que
Orion pone siempre. Bajo esa condición, un término corto SOLO puede venir de que el `@vocab`
del core context (`default-context/`) haya compactado el IRI almacenado — así que es
SIEMPRE stale. Un IRI completo es lo que el core context no pudo compactar por `@vocab`; se
compara contra el @context vigente para decidir si es sano.
"""

import os
import sys
from unittest.mock import MagicMock, patch

from flask import Flask

_test_dir = os.path.dirname(os.path.abspath(__file__))
_svc_dir = os.path.normpath(os.path.join(_test_dir, ".."))
_services_dir = os.path.normpath(os.path.join(_svc_dir, ".."))
for _p in (_svc_dir, _services_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from blueprints.diagnostics import (  # noqa: E402
    DEFAULT_CONTEXT_VOCAB,
    ORION_PAGE_SIZE,
    audit_expansions,
    diagnostics_bp,
)

CTX = {
    "AgriSensor": {"@id": "nkz:AgriSensor"},
    "WeatherObserved": {"@id": "nkz:WeatherObserved"},
    "nkz": "https://nkz-os.org/ns/",
}


def _sub(description, entity_type):
    return {"description": description, "entities": [{"type": entity_type}]}


# ── audit_expansions: one test per real behaviour ──────────────────────────────────────


def test_a_bare_term_is_stale_with_the_platform_iri_as_expected():
    """A bare term can only come from the core context's @vocab compacting a
    default-context/ IRI — that means stale, unconditionally, even though the platform
    context does define this term under its own namespace."""
    subs = [_sub("bare-known", "AgriSensor")]
    with patch("blueprints.diagnostics._load_context", return_value=CTX):
        result = audit_expansions(subs)
    assert result["checked"] == 1
    assert len(result["stale"]) == 1
    entry = result["stale"][0]
    assert entry["description"] == "bare-known"
    assert entry["stored"] == f"{DEFAULT_CONTEXT_VOCAB}AgriSensor"
    assert entry["expected"] == "https://nkz-os.org/ns/AgriSensor"


def test_a_bare_term_the_platform_context_does_not_define_is_stale_with_expected_none():
    """The dangerous case: a default-context/ orphan for a type the platform context does
    not even recognize. Cannot be silenced — expected must surface as None, not skipped."""
    subs = [_sub("bare-unknown", "Device")]
    with patch("blueprints.diagnostics._load_context", return_value=CTX):
        result = audit_expansions(subs)
    assert result["checked"] == 1
    assert len(result["stale"]) == 1
    entry = result["stale"][0]
    assert entry["description"] == "bare-unknown"
    assert entry["stored"] == f"{DEFAULT_CONTEXT_VOCAB}Device"
    assert entry["expected"] is None


def test_a_full_iri_matching_the_platform_context_is_healthy():
    """A full IRI the core context couldn't compact, because it's the platform's own
    namespace — and it matches what the platform context gives that local name. Healthy."""
    subs = [_sub("healthy", "https://nkz-os.org/ns/AgriSensor")]
    with patch("blueprints.diagnostics._load_context", return_value=CTX):
        result = audit_expansions(subs)
    assert result["checked"] == 1
    assert result["stale"] == []


def test_a_full_iri_from_a_retired_namespace_is_stale():
    """A full IRI under a namespace the platform no longer uses. The core context can't
    compact it (not @vocab), and it doesn't match what the platform context gives the local
    name either — stale, with the platform's current IRI surfaced as `expected`."""
    subs = [_sub("retired-namespace", "https://saref.example.invalid/saref4agri/WeatherObserved")]
    with patch("blueprints.diagnostics._load_context", return_value=CTX):
        result = audit_expansions(subs)
    assert result["checked"] == 1
    assert len(result["stale"]) == 1
    entry = result["stale"][0]
    assert entry["description"] == "retired-namespace"
    assert entry["stored"] == "https://saref.example.invalid/saref4agri/WeatherObserved"
    assert entry["expected"] == "https://nkz-os.org/ns/WeatherObserved"


def test_checked_counts_every_entity_examined_healthy_included():
    subs = [
        _sub("healthy", "https://nkz-os.org/ns/AgriSensor"),
        _sub("bare-known", "AgriSensor"),
        _sub("retired-namespace", "https://saref.example.invalid/saref4agri/WeatherObserved"),
    ]
    with patch("blueprints.diagnostics._load_context", return_value=CTX):
        result = audit_expansions(subs)
    assert result["checked"] == 3
    stale_descriptions = {entry["description"] for entry in result["stale"]}
    assert stale_descriptions == {"bare-known", "retired-namespace"}


# ── Flask route tests ───────────────────────────────────────────────────────────────────
#
# The route requires X-Internal-Service-Secret before it does anything else. Each test sets
# INTERNAL_SERVICE_SECRET explicitly via monkeypatch — never relies on ambient environment.
_SECRET = "test-internal-secret"


@patch("blueprints.diagnostics._load_context")
@patch("blueprints.diagnostics._fetch_all_subscriptions")
def test_expansions_route_no_secret_configured_returns_500(mock_fetch, mock_load_ctx, monkeypatch):
    """GET /api/diagnostics/expansions with no INTERNAL_SERVICE_SECRET configured returns 500."""
    monkeypatch.delenv("INTERNAL_SERVICE_SECRET", raising=False)
    app = Flask(__name__)
    app.register_blueprint(diagnostics_bp)
    client = app.test_client()

    response = client.get(
        "/api/diagnostics/expansions",
        headers={"X-Internal-Service-Secret": "whatever", "X-Tenant-ID": "test-tenant"},
    )
    assert response.status_code == 500
    mock_fetch.assert_not_called()
    mock_load_ctx.assert_not_called()


@patch("blueprints.diagnostics._load_context")
@patch("blueprints.diagnostics._fetch_all_subscriptions")
def test_expansions_route_wrong_secret_returns_401(mock_fetch, mock_load_ctx, monkeypatch):
    """GET /api/diagnostics/expansions with a non-matching secret returns 401."""
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", _SECRET)
    app = Flask(__name__)
    app.register_blueprint(diagnostics_bp)
    client = app.test_client()

    response = client.get(
        "/api/diagnostics/expansions",
        headers={"X-Internal-Service-Secret": "wrong-secret", "X-Tenant-ID": "test-tenant"},
    )
    assert response.status_code == 401
    assert response.get_json()["error"] == "Unauthorized"
    mock_fetch.assert_not_called()
    mock_load_ctx.assert_not_called()


@patch("blueprints.diagnostics._load_context")
@patch("blueprints.diagnostics._fetch_all_subscriptions")
def test_expansions_route_missing_tenant_header(mock_fetch, mock_load_ctx, monkeypatch):
    """GET /api/diagnostics/expansions with a valid secret but no X-Tenant-ID header returns 400."""
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", _SECRET)
    app = Flask(__name__)
    app.register_blueprint(diagnostics_bp)
    client = app.test_client()

    response = client.get(
        "/api/diagnostics/expansions",
        headers={"X-Internal-Service-Secret": _SECRET},
    )
    assert response.status_code == 400
    assert "X-Tenant-ID required" in response.get_json()["error"]
    mock_fetch.assert_not_called()
    mock_load_ctx.assert_not_called()


@patch("blueprints.diagnostics._load_context")
@patch("blueprints.diagnostics._fetch_all_subscriptions")
def test_expansions_route_success_with_stale_subscriptions(mock_fetch, mock_load_ctx, monkeypatch):
    """GET /api/diagnostics/expansions with a valid secret and tenant returns 200 and audit payload."""
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", _SECRET)
    app = Flask(__name__)
    app.register_blueprint(diagnostics_bp)
    client = app.test_client()

    # A full default-context IRI, as `audit_expansions` would receive it if `_fetch_all_subscriptions`
    # were mocked with the raw stored value rather than what Orion returns under @vocab compaction.
    mock_fetch.return_value = [
        _sub("legacy", "https://uri.etsi.org/ngsi-ld/default-context/AgriSensor")
    ]
    mock_load_ctx.return_value = CTX

    response = client.get(
        "/api/diagnostics/expansions",
        headers={"X-Internal-Service-Secret": _SECRET, "X-Tenant-ID": "test-tenant"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["checked"] == 1
    assert len(data["stale"]) == 1
    assert data["stale"][0]["description"] == "legacy"
    assert data["stale"][0]["stored"] == "https://uri.etsi.org/ngsi-ld/default-context/AgriSensor"
    assert data["stale"][0]["expected"] == "https://nkz-os.org/ns/AgriSensor"


@patch("blueprints.diagnostics._load_context")
@patch("blueprints.diagnostics._fetch_all_subscriptions")
def test_expansions_route_subscription_fetch_fails(mock_fetch, mock_load_ctx, monkeypatch):
    """GET /api/diagnostics/expansions returns 502 when Orion is unreachable, no exception leak."""
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", _SECRET)
    app = Flask(__name__)
    app.register_blueprint(diagnostics_bp)
    client = app.test_client()

    mock_fetch.side_effect = Exception("Orion connection failed")
    response = client.get(
        "/api/diagnostics/expansions",
        headers={"X-Internal-Service-Secret": _SECRET, "X-Tenant-ID": "test-tenant"},
    )
    assert response.status_code == 502
    data = response.get_json()
    assert "subscriptions" in data["error"].lower()
    # Ensure exception text is not leaked
    assert "Orion connection failed" not in data["error"]
    assert "connection" not in data["error"].lower()


@patch("blueprints.diagnostics._load_context")
@patch("blueprints.diagnostics._fetch_all_subscriptions")
def test_expansions_route_context_load_fails(mock_fetch, mock_load_ctx, monkeypatch):
    """GET /api/diagnostics/expansions returns 502 with a distinct message when the context fails."""
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", _SECRET)
    app = Flask(__name__)
    app.register_blueprint(diagnostics_bp)
    client = app.test_client()

    mock_fetch.return_value = []
    mock_load_ctx.side_effect = Exception("Context server unavailable")
    response = client.get(
        "/api/diagnostics/expansions",
        headers={"X-Internal-Service-Secret": _SECRET, "X-Tenant-ID": "test-tenant"},
    )
    assert response.status_code == 502
    data = response.get_json()
    assert "context" in data["error"].lower()
    # Distinct from the subscription-fetch failure message, and no exception leak
    assert "subscriptions" not in data["error"].lower()
    assert "Context server unavailable" not in data["error"]


@patch("blueprints.diagnostics._load_context")
@patch("blueprints.diagnostics.requests.get")
def test_expansions_route_lists_subscriptions_without_the_platform_context_link(
    mock_get, mock_load_ctx, monkeypatch
):
    """The crux of this fix: the LIST request must carry tenant headers but NO Link header.

    Sending the platform @context here makes stale and healthy subscriptions
    indistinguishable (see the module docstring) — that was the false negative. This test
    goes through the real route and the real `_fetch_all_subscriptions`, patching only
    `requests.get`, so a regression that reintroduces `inject_fiware_headers` (or any Link
    header) here fails loudly instead of silently.
    """
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", _SECRET)
    mock_load_ctx.return_value = CTX

    orion_response = MagicMock()
    orion_response.json.return_value = []
    orion_response.raise_for_status = MagicMock()
    mock_get.return_value = orion_response

    app = Flask(__name__)
    app.register_blueprint(diagnostics_bp)
    client = app.test_client()

    response = client.get(
        "/api/diagnostics/expansions",
        headers={"X-Internal-Service-Secret": _SECRET, "X-Tenant-ID": "test-tenant"},
    )
    assert response.status_code == 200

    assert mock_get.call_count == 1
    sent_headers = mock_get.call_args.kwargs["headers"]
    assert sent_headers["NGSILD-Tenant"] == "test-tenant"
    assert sent_headers["Fiware-Service"] == "test-tenant"
    assert "Link" not in sent_headers


@patch("blueprints.diagnostics.requests.get")
def test_fetch_all_subscriptions_pagination(mock_get):
    """_fetch_all_subscriptions issues a second request with offset when first page is full."""
    from blueprints.diagnostics import _fetch_all_subscriptions

    # Mock response: first page has ORION_PAGE_SIZE items, second has fewer
    first_page_data = [{"id": f"sub{i}"} for i in range(ORION_PAGE_SIZE)]
    second_page_data = [{"id": "sub_last"}]

    # Create separate response objects for each call
    response1 = MagicMock()
    response1.json.return_value = first_page_data
    response1.raise_for_status = MagicMock()

    response2 = MagicMock()
    response2.json.return_value = second_page_data
    response2.raise_for_status = MagicMock()

    mock_get.side_effect = [response1, response2]

    headers = {"X-Tenant-ID": "test"}
    result = _fetch_all_subscriptions(headers)

    # Verify result contains all subscriptions from both pages
    assert len(result) == ORION_PAGE_SIZE + 1
    assert result[-1]["id"] == "sub_last"

    # Verify requests were made with correct offsets
    assert mock_get.call_count == 2
    first_call = mock_get.call_args_list[0]
    second_call = mock_get.call_args_list[1]

    # First call should have offset=0 (implicit in params)
    assert first_call[1]["params"]["offset"] == 0
    assert first_call[1]["params"]["limit"] == ORION_PAGE_SIZE

    # Second call should have offset=ORION_PAGE_SIZE
    assert second_call[1]["params"]["offset"] == ORION_PAGE_SIZE
    assert second_call[1]["params"]["limit"] == ORION_PAGE_SIZE


@patch("blueprints.diagnostics.requests.get")
def test_fetch_all_subscriptions_stops_at_short_page(mock_get):
    """_fetch_all_subscriptions stops fetching after receiving a page shorter than ORION_PAGE_SIZE."""
    from blueprints.diagnostics import _fetch_all_subscriptions

    # Mock response: single short page (fewer than ORION_PAGE_SIZE items)
    short_page = [{"id": "sub1"}, {"id": "sub2"}]
    response = MagicMock()
    response.json.return_value = short_page
    response.raise_for_status = MagicMock()
    mock_get.return_value = response

    headers = {"X-Tenant-ID": "test"}
    result = _fetch_all_subscriptions(headers)

    assert len(result) == 2
    assert mock_get.call_count == 1
