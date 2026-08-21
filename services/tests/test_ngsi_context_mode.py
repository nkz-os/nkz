"""Regression tests for @context delivery mode in ngsi_headers.inject_fiware_headers.

ETSI GS CIM 009 mutual exclusivity:
  - @context in body  -> Content-Type: application/ld+json, NO Link header
  - @context NOT in body -> Content-Type: application/json + Link header

Orion-LD answers 400 (BadRequestData) when Content-Type is application/json and
the payload carries an @context member. The historical bug: callers set
Content-Type: application/ld+json and inject_fiware_headers() silently overwrote
it to application/json because has_context_in_body defaulted to False.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))

from ngsi_headers import inject_fiware_headers  # noqa: E402


CTX = "http://api-gateway-service:5000/ngsi-ld-context.json"


@pytest.fixture(autouse=True)
def _ctx_env(monkeypatch):
    monkeypatch.setenv("CONTEXT_URL", CTX)


class TestBodyDerivedMode:
    """body= lets the helper derive the mode instead of trusting a flag."""

    def test_body_with_context_selects_ld_json_and_no_link(self):
        entity = {"@context": CTX, "id": "urn:ngsi-ld:AgriParcel:t:1", "type": "AgriParcel"}
        h = inject_fiware_headers({}, "montiko", body=entity)
        assert h["Content-Type"] == "application/ld+json"
        assert "Link" not in h

    def test_body_without_context_selects_json_and_link(self):
        fragment = {"soilMoisture": {"type": "Property", "value": 12}}
        h = inject_fiware_headers({}, "montiko", body=fragment)
        assert h["Content-Type"] == "application/json"
        assert CTX in h["Link"]

    def test_body_list_with_context_selects_ld_json(self):
        """Batch operations post a list of entities."""
        batch = [{"@context": CTX, "id": "urn:ngsi-ld:X:1", "type": "X"}]
        h = inject_fiware_headers({}, "montiko", body=batch)
        assert h["Content-Type"] == "application/ld+json"
        assert "Link" not in h

    def test_empty_list_body_falls_back_to_json(self):
        h = inject_fiware_headers({}, "montiko", body=[])
        assert h["Content-Type"] == "application/json"

    def test_body_overrides_a_wrong_explicit_flag(self):
        """An explicit has_context_in_body=False must not win over a body that has one."""
        entity = {"@context": CTX, "id": "urn:ngsi-ld:X:1", "type": "X"}
        h = inject_fiware_headers({}, "montiko", has_context_in_body=False, body=entity)
        assert h["Content-Type"] == "application/ld+json"
        assert "Link" not in h


class TestContradictionDetection:
    """The silent overwrite that produced the Orion 400s must be observable."""

    def test_caller_ld_json_without_context_is_logged(self, caplog):
        h = inject_fiware_headers({"Content-Type": "application/ld+json"}, "montiko")
        assert h["Content-Type"] == "application/json"
        assert any(
            "Content-Type" in r.message or "Content-Type" in str(r.msg)
            for r in caplog.records
            if r.levelname == "ERROR"
        ), "overwriting a caller-set ld+json must log an ERROR"

    def test_no_false_alarm_when_modes_agree(self, caplog):
        entity = {"@context": CTX, "id": "urn:ngsi-ld:X:1", "type": "X"}
        inject_fiware_headers({"Content-Type": "application/ld+json"}, "montiko", body=entity)
        assert not [r for r in caplog.records if r.levelname == "ERROR"]


class TestBackwardCompatibility:
    """Existing call sites must keep working unchanged."""

    def test_flag_true_still_selects_ld_json(self):
        h = inject_fiware_headers({}, "montiko", has_context_in_body=True)
        assert h["Content-Type"] == "application/ld+json"
        assert "Link" not in h

    def test_flag_false_still_selects_json_with_link(self):
        h = inject_fiware_headers({}, "montiko", has_context_in_body=False)
        assert h["Content-Type"] == "application/json"
        assert CTX in h["Link"]

    def test_tenant_headers_still_injected(self):
        h = inject_fiware_headers({}, "montiko")
        assert h["NGSILD-Tenant"] == "montiko"
        assert h["Fiware-Service"] == "montiko"
        assert h["Fiware-ServicePath"] == "/"

    def test_accept_not_clobbered(self):
        h = inject_fiware_headers({"Accept": "application/json"}, "montiko")
        assert h["Accept"] == "application/json"

    def test_no_link_header_when_context_url_unset(self, monkeypatch):
        monkeypatch.delenv("CONTEXT_URL", raising=False)
        h = inject_fiware_headers({}, "montiko")
        assert "Link" not in h
