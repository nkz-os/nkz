"""CONTEXT_URL must not go missing silently.

With no CONTEXT_URL the helper emits application/json and no Link, so Orion
expands every term against the default vocabulary. Entities then land under
https://uri.etsi.org/ngsi-ld/default-context/<Type> and are invisible to any
read that does send the platform context -- the exact shape of the leaked
WeatherAlert entities found in the 2026-08-03 audit.
"""
import logging
import os
import sys

import pytest

_services_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_services_dir, os.path.join(_services_dir, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common import ngsi_headers


@pytest.fixture(autouse=True)
def _reset_warning_latch():
    ngsi_headers._MISSING_CONTEXT_WARNED = False
    yield
    ngsi_headers._MISSING_CONTEXT_WARNED = False


def test_link_header_is_sent_when_context_url_is_configured(monkeypatch):
    monkeypatch.setenv("CONTEXT_URL", "http://ctx.test/ngsi-ld-context.json")
    h = ngsi_headers.inject_fiware_headers({}, tenant="t1")
    assert h["Content-Type"] == "application/json"
    assert h["Link"].startswith("<http://ctx.test/ngsi-ld-context.json>;")


def test_missing_context_url_warns(monkeypatch, caplog):
    monkeypatch.delenv("CONTEXT_URL", raising=False)
    with caplog.at_level(logging.WARNING):
        h = ngsi_headers.inject_fiware_headers({}, tenant="t1")
    assert "Link" not in h
    assert "CONTEXT_URL is not set" in caplog.text


def test_the_warning_fires_once_per_process(monkeypatch, caplog):
    """One line per process, not one per request -- otherwise it is noise."""
    monkeypatch.delenv("CONTEXT_URL", raising=False)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            ngsi_headers.inject_fiware_headers({}, tenant="t1")
    assert caplog.text.count("CONTEXT_URL is not set") == 1


def test_context_in_body_does_not_warn(monkeypatch, caplog):
    """ld+json carries its own @context; the Link would be a spec violation."""
    monkeypatch.delenv("CONTEXT_URL", raising=False)
    with caplog.at_level(logging.WARNING):
        h = ngsi_headers.inject_fiware_headers({}, tenant="t1", body={"@context": "x"})
    assert h["Content-Type"] == "application/ld+json"
    assert "CONTEXT_URL is not set" not in caplog.text
