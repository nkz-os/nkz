"""Tests for parcel_reconcile — convergence engine. Orion + DB are mocked."""

import os
import sys
import unittest.mock
from unittest.mock import MagicMock, patch

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("CONTEXT_URL", "https://nekazari.robotika.cloud/ngsi-ld-context.json")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")

_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _dir)
sys.path.insert(0, os.path.join(_dir, "..", "common"))

# parcel_reconcile imports parcel_activation (common.tier_quotas) and, lazily,
# common.auth_middleware.inject_fiware_headers — stub both so the real packages
# are not required for unit tests.
unittest.mock.patch.dict(
    "sys.modules",
    {"common": MagicMock(), "common.tier_quotas": MagicMock(),
     "common.auth_middleware": MagicMock()},
).start()

import parcel_reconcile as pr


def _resp(status, payload):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


def test_get_live_parcel_ids_returns_set_of_urns():
    page = [
        {"id": "urn:ngsi-ld:AgriParcel:aaa", "type": "AgriParcel"},
        {"id": "urn:ngsi-ld:AgriParcel:bbb", "type": "AgriParcel"},
    ]
    with patch.object(pr.requests, "get", side_effect=[_resp(200, page), _resp(200, [])]):
        ids = pr.get_live_parcel_ids("montiko")
    assert ids == {"urn:ngsi-ld:AgriParcel:aaa", "urn:ngsi-ld:AgriParcel:bbb"}


def test_get_live_parcel_ids_returns_none_on_http_error():
    """CRITICAL false-zero guard: a non-200 must NOT look like 'zero parcels'."""
    with patch.object(pr.requests, "get", return_value=_resp(500, {})):
        assert pr.get_live_parcel_ids("montiko") is None


def test_get_live_parcel_ids_returns_none_on_exception():
    with patch.object(pr.requests, "get", side_effect=pr.requests.RequestException("boom")):
        assert pr.get_live_parcel_ids("montiko") is None
