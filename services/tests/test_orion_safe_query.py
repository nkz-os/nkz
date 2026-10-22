"""services/tests/test_orion_safe_query.py — Tests for false-zero-safe Orion queries."""

import os
import sys

_SERVICES_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _SERVICES_DIR)

import pytest
import requests
from unittest.mock import patch, Mock
from requests.exceptions import ConnectionError as RequestsConnectionError
from common.orion_safe_query import (
    safe_count_entities,
    safe_query_entities,
    OrionQueryError,
    QUERY_FAILED
)

# ---------------------------------------------------------------------------
# safe_count_entities tests
# ---------------------------------------------------------------------------


class TestSafeCountEntities:
    def test_returns_count_on_200_with_header(self):
        """Normal case: returns count from the NGSI-LD results-count header."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.headers = {"NGSILD-Results-Count": "42"}

        with patch("requests.get", return_value=mock_resp) as mock_get:
            result = safe_count_entities("http://orion:1026", "test", "AgriParcel")
            assert result == 42
            mock_get.assert_called_once()

    def test_returns_0_on_404(self):
        """404 means type doesn't exist — return 0 (legitimate)."""
        mock_resp = Mock()
        mock_resp.status_code = 404

        with patch("requests.get", return_value=mock_resp):
            result = safe_count_entities("http://orion:1026", "test", "NonexistentType")
            assert result == 0

    def test_returns_neg1_on_500_instead_of_0(self):
        """CRITICAL: On 5xx error, return -1 NEVER 0."""
        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal error"

        with patch("requests.get", return_value=mock_resp):
            result = safe_count_entities("http://orion:1026", "test", "AgriParcel")
            assert result == QUERY_FAILED, f"Must return {QUERY_FAILED} on error, got {result}"

    def test_returns_neg1_on_connection_error(self):
        """CRITICAL: On network error, return -1 NEVER 0."""
        with patch("requests.get", side_effect=RequestsConnectionError("DNS failed")):
            result = safe_count_entities("http://orion:1026", "test", "AgriParcel")
            assert result == QUERY_FAILED

    def test_raises_on_error_if_requested(self):
        """When raise_on_error=True, must raise OrionQueryError."""
        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_resp.text = "fail"

        with patch("requests.get", return_value=mock_resp):
            with pytest.raises(OrionQueryError):
                safe_count_entities("http://orion:1026", "test", "AgriParcel", raise_on_error=True)

    def test_never_counts_from_response_body(self):
        """CRITICAL: the body is capped by `limit`, so its length is not a count.

        Returning len(body) here would understate the total and reintroduce the
        false-zero class of bug this module exists to prevent.
        """
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.json.return_value = [{"id": "1"}, {"id": "2"}, {"id": "3"}]

        with patch("requests.get", return_value=mock_resp):
            result = safe_count_entities("http://orion:1026", "test", "AgriParcel")
            assert result == QUERY_FAILED

    def test_malformed_count_header_is_a_failure_not_a_guess(self):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.headers = {"NGSILD-Results-Count": "abc"}
        mock_resp.json.return_value = [{"id": "1"}, {"id": "2"}]

        with patch("requests.get", return_value=mock_resp):
            result = safe_count_entities("http://orion:1026", "test", "AgriParcel")
            assert result == QUERY_FAILED

    def test_ignores_ngsi_v2_count_header(self):
        """X-Total-Count is NGSI-v2. Trusting it silently produced wrong counts."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.headers = {"X-Total-Count": "42"}
        mock_resp.json.return_value = []

        with patch("requests.get", return_value=mock_resp):
            assert safe_count_entities("http://orion:1026", "test", "AgriParcel") == QUERY_FAILED

    def test_requests_count_true(self):
        """The count must be asked for explicitly, or Orion never sends the header."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.headers = {"NGSILD-Results-Count": "7"}

        with patch("requests.get", return_value=mock_resp) as mock_get:
            safe_count_entities("http://orion:1026", "test", "AgriParcel")
            url = mock_get.call_args[0][0]
            assert "count=true" in url

    def test_sends_correct_headers(self):
        """Must send NGSILD-Tenant and Link @context headers."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.headers = {"NGSILD-Results-Count": "0"}
        mock_resp.json.return_value = []

        with patch("requests.get", return_value=mock_resp) as mock_get:
            safe_count_entities("http://orion:1026", "tenant-x", "TestType")
            _, kwargs = mock_get.call_args
            headers = kwargs.get("headers", {})

            assert headers.get("NGSILD-Tenant") == "tenant-x", "Missing NGSILD-Tenant header"
            assert headers.get("Fiware-Service") == "tenant-x", "Missing Fiware-Service header"
            assert "Link" in headers, "Missing Link @context header"
            assert "json-ld#context" in headers["Link"], "Link header must reference @context"

    def test_returns_zero_for_empty_result(self):
        """Legitimate empty result returns 0, not -1."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.headers = {"NGSILD-Results-Count": "0"}

        with patch("requests.get", return_value=mock_resp):
            result = safe_count_entities("http://orion:1026", "test", "AgriParcel")
            assert result == 0


# ---------------------------------------------------------------------------
# safe_query_entities tests
# ---------------------------------------------------------------------------


class TestSafeQueryEntities:
    def test_returns_list_on_200(self):
        """Normal case: returns list of entities."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"id": "urn:ngsi-ld:AgriParcel:001"}]

        with patch("requests.get", return_value=mock_resp):
            result = safe_query_entities("http://orion:1026", "test", "AgriParcel")
            assert result == [{"id": "urn:ngsi-ld:AgriParcel:001"}]

    def test_returns_none_on_500_instead_of_empty_list(self):
        """CRITICAL: On 5xx, return None NEVER []."""
        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_resp.text = "fail"

        with patch("requests.get", return_value=mock_resp):
            result = safe_query_entities("http://orion:1026", "test", "AgriParcel")
            assert result is None, "Must return None on error, never []"

    def test_returns_empty_list_on_404(self):
        """404 for non-existent type returns [] (legitimate), not None."""
        mock_resp = Mock()
        mock_resp.status_code = 404

        with patch("requests.get", return_value=mock_resp):
            result = safe_query_entities("http://orion:1026", "test", "NoType")
            assert result == []

    def test_returns_none_on_connection_error(self):
        """Network error returns None."""
        with patch("requests.get", side_effect=RequestsConnectionError("refused")):
            result = safe_query_entities("http://orion:1026", "test", "AgriParcel")
            assert result is None

    def test_raises_on_error(self):
        """With raise_on_error=True, connection error raises."""
        with patch("requests.get", side_effect=RequestsConnectionError("fail")):
            with pytest.raises(OrionQueryError):
                safe_query_entities("http://orion:1026", "test", "AgriParcel", raise_on_error=True)

    def test_passes_limit_and_offset(self):
        """Verify limit and offset are forwarded to Orion."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []

        with patch("requests.get", return_value=mock_resp) as mock_get:
            safe_query_entities("http://orion:1026", "test", "AgriParcel", limit=50, offset=100)
            url = mock_get.call_args[0][0]
            assert "limit=50" in url, f"Expected limit=50 in URL, got {url}"
            assert "offset=100" in url, f"Expected offset=100 in URL, got {url}"

    def test_sends_correct_headers(self):
        """Must send NGSILD-Tenant and Link headers."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []

        with patch("requests.get", return_value=mock_resp) as mock_get:
            safe_query_entities("http://orion:1026", "tenant-y", "TestType")
            _, kwargs = mock_get.call_args
            headers = kwargs.get("headers", {})
            assert headers.get("NGSILD-Tenant") == "tenant-y"
            assert "Link" in headers


# ---------------------------------------------------------------------------
# Integration-level tests (mock Orion, not real)
# ---------------------------------------------------------------------------


class TestSafeQueryResponsePatterns:
    """Test that callers of safe_count_entities handle -1 correctly.

    These tests use mock to verify the sentinel value is correctly propagated
    and that callers don't accidentally treat -1 as "zero entities".
    """

    def test_neg1_caller_must_not_treat_as_zero(self):
        """Verify that -1 cannot be used as count without explicit check."""
        from requests.exceptions import ConnectionError as RequestsConnectionError
        with patch("requests.get", side_effect=RequestsConnectionError("simulated")):
            count = safe_count_entities("http://orion:1026", "test", "AgriParcel")

        # WRONG pattern that must NOT happen:
        # if count == 0: delete_all_parcels()
        # We assert that -1 is NOT equal to 0
        assert count == QUERY_FAILED, "QUERY_FAILED must be -1 on error"
        assert count != 0, "QUERY_FAILED must not equal 0"

    def test_none_caller_must_not_treat_as_empty_list(self):
        """Verify that None cannot be iterated like []."""
        from requests.exceptions import ConnectionError as RequestsConnectionError
        with patch("requests.get", side_effect=RequestsConnectionError("simulated")):
            result = safe_query_entities("http://orion:1026", "test", "AgriParcel")

        # WRONG patterns that must NOT happen:
        # for entity in result:   # TypeError: None is not iterable
        # if not result: ...      # True for both None and [] — ambiguous!
        assert result is None

    def test_consumer_pattern_guard(self):
        """Demonstrate the correct consumer pattern when using safe_count."""

        # If a service needs to check "are there any parcels?"
        # CORRECT pattern:
        def count_parcels(orion_url, tenant_id):
            count = safe_count_entities(orion_url, tenant_id, "AgriParcel")
            if count == QUERY_FAILED:
                # Don't proceed — we don't know the answer
                raise RuntimeError(f"Cannot verify parcel count for tenant {tenant_id}")
            return count

        # Patch the reference in the test module (where count_parcels looks it up)
        with patch("tests.test_orion_safe_query.safe_count_entities", return_value=5):
            result = count_parcels("http://orion:1026", "test")
            assert result == 5

        with patch("tests.test_orion_safe_query.safe_count_entities", return_value=QUERY_FAILED):
            with pytest.raises(RuntimeError):
                count_parcels("http://orion:1026", "test")
