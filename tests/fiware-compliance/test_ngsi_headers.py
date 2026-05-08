"""Tests for canonical NGSI-LD header injection (ngsi_headers.py).

Verifies ETSI NGSI-LD mutual exclusivity rule:
  - @context in body  → Content-Type: application/ld+json, NO Link header
  - @context NOT in body → Content-Type: application/json, Link header with @context URL
"""

from services.common.ngsi_headers import inject_fiware_headers


CONTEXT_URL_VALUE = "https://example.com/ngsi-ld-context.json"


class TestNGSIHeaders:
    """Unit tests for inject_fiware_headers()."""

    def test_inline_context_mode(self):
        """has_context_in_body=True → Content-Type is ld+json, NO Link header."""
        headers = inject_fiware_headers({}, has_context_in_body=True)
        assert headers["Content-Type"] == "application/ld+json"
        assert "Link" not in headers

    def test_link_header_mode(self, monkeypatch):
        """has_context_in_body=False → Content-Type is json, Link present."""
        monkeypatch.setenv("CONTEXT_URL", CONTEXT_URL_VALUE)
        headers = inject_fiware_headers({}, has_context_in_body=False)
        assert headers["Content-Type"] == "application/json"
        assert headers["Link"] == (
            f"<{CONTEXT_URL_VALUE}>; "
            f'rel="http://www.w3.org/ns/json-ld#context"; '
            f'type="application/ld+json"'
        )

    def test_tenant_headers_injected(self):
        """tenant="My-Tenant" → NGSILD-Tenant="my_tenant", Fiware-Service="my_tenant"."""
        headers = inject_fiware_headers({}, tenant="My-Tenant")
        assert headers["NGSILD-Tenant"] == "my_tenant"
        assert headers["Fiware-Service"] == "my_tenant"
        assert headers["Fiware-ServicePath"] == "/"

    def test_accept_header_always_ld_json(self):
        """Accept is always application/ld+json."""
        headers = inject_fiware_headers({})
        assert headers["Accept"] == "application/ld+json"

        headers2 = inject_fiware_headers({}, has_context_in_body=True)
        assert headers2["Accept"] == "application/ld+json"

        headers3 = inject_fiware_headers({}, tenant="test")
        assert headers3["Accept"] == "application/ld+json"

    def test_no_tenant_dont_crash(self):
        """tenant=None → no tenant headers, no crash."""
        headers = inject_fiware_headers({}, tenant=None)
        assert "NGSILD-Tenant" not in headers
        assert "Fiware-Service" not in headers
        assert "Fiware-ServicePath" not in headers

    def test_context_url_missing_skips_link(self, monkeypatch):
        """CONTEXT_URL unset → no Link header."""
        monkeypatch.delenv("CONTEXT_URL", raising=False)
        headers = inject_fiware_headers({}, has_context_in_body=False)
        assert headers["Content-Type"] == "application/json"
        assert "Link" not in headers
