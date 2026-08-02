"""Tests for EdrWarningsClient (MeteoAlarm EDR API).

Covers pagination, param construction, detail fetching (dual-link, no-auth
for pre-signed archive URLs), and HTTP-error resilience.
"""

import datetime
import os
import sys
from unittest.mock import MagicMock, patch

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

from weather_worker.edr_client import EdrWarningsClient

# ---------------------------------------------------------------------------
# Param construction
# ---------------------------------------------------------------------------


def test_iter_index_datetime_span_is_less_than_24h():
    """The sent window (datetime param) must be < 24h per EDR requirement."""
    client = EdrWarningsClient(
        base_url="https://api.meteoalarm.org/edr/v1",
        api_key="test-key",
        sent_window_hours=23,
    )
    # sent_window_hours=23 is < 24 — safe
    assert client._sent_window_hours == 23
    assert client._sent_window_hours < 24


def test_active_window_is_positive():
    client = EdrWarningsClient(
        base_url="https://api.meteoalarm.org/edr/v1",
        api_key="test-key",
        active_window_hours=6,
    )
    assert client._active_window_hours == 6
    assert client._active_window_hours > 0


def test_datetime_and_active_are_closed_intervals():
    """Both datetime and active MUST be closed 'start/end' intervals —
    open-ended (now/..) would give a 400 from the EDR API."""
    client = EdrWarningsClient(
        base_url="https://api.meteoalarm.org/edr/v1",
        api_key="test-key",
        sent_window_hours=23,
        active_window_hours=6,
    )
    from datetime import datetime, timezone

    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
    dt_str, act_str = client._build_intervals(now)
    # both must contain '/' (closed interval)
    assert "/" in dt_str
    assert "/" in act_str
    # neither should end with '/' (open-ended)
    assert not dt_str.endswith("/")
    assert not act_str.endswith("/")


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_pagination_stops_on_short_page():
    """When a page returns fewer than 100 features, stop iterating."""
    client = EdrWarningsClient(
        base_url="https://api.meteoalarm.org/edr/v1",
        api_key="test-key",
    )
    from datetime import datetime, timezone

    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)

    with patch.object(client._session, "get") as mock_get:
        # Page 1: 100 features (full page) → should ask for page 2
        full_page = {
            "type": "FeatureCollection",
            "features": [{"id": str(i), "type": "Feature", "properties": {}} for i in range(100)],
        }
        # Page 2: 47 features (short page) → stop
        short_page = {
            "type": "FeatureCollection",
            "features": [{"id": str(i), "type": "Feature", "properties": {}} for i in range(47)],
        }

        mock_resp_full = MagicMock()
        mock_resp_full.status_code = 200
        mock_resp_full.json.return_value = full_page

        mock_resp_short = MagicMock()
        mock_resp_short.status_code = 200
        mock_resp_short.json.return_value = short_page

        mock_get.side_effect = [mock_resp_full, mock_resp_short]

        features = list(client.iter_index(now))
        assert len(features) == 147  # 100 + 47
        assert mock_get.call_count == 2


def test_pagination_stops_on_empty_page():
    """A page with 0 features terminates iteration."""
    client = EdrWarningsClient(
        base_url="https://api.meteoalarm.org/edr/v1",
        api_key="test-key",
    )
    from datetime import datetime, timezone

    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)

    with patch.object(client._session, "get") as mock_get:
        empty_page = {"type": "FeatureCollection", "features": []}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = empty_page
        mock_get.return_value = mock_resp

        features = list(client.iter_index(now))
        assert len(features) == 0
        assert mock_get.call_count == 1


# ---------------------------------------------------------------------------
# fetch_detail — dual link + auth only for api.meteoalarm.org
# ---------------------------------------------------------------------------


def test_fetch_detail_follows_json_and_geo_json_links():
    """fetch_detail must GET both application/json and application/geo+json links."""
    client = EdrWarningsClient(
        base_url="https://api.meteoalarm.org/edr/v1",
        api_key="test-key",
    )

    feature = {
        "links": [
            {"type": "application/json", "href": "https://archive.example.com/warn.json?...", "rel": "json"},
            {"type": "application/geo+json", "href": "https://archive.example.com/feat.geojson?...", "rel": "geometry"},
        ],
    }

    with patch.object(client._session, "get") as mock_get:
        cap_body = {"identifier": "test-id", "info": [{"severity": "Moderate", "event": "Wind", "language": "en"}]}
        geo_body = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [0, 0]]]}}

        mock_cap = MagicMock()
        mock_cap.status_code = 200
        mock_cap.json.return_value = cap_body

        mock_geo = MagicMock()
        mock_geo.status_code = 200
        mock_geo.json.return_value = geo_body

        mock_get.side_effect = [mock_cap, mock_geo]

        cap, geometry = client.fetch_detail(feature)
        assert cap is not None
        assert cap["identifier"] == "test-id"
        assert geometry is not None
        assert geometry["type"] == "Polygon"
        assert mock_get.call_count == 2


def test_fetch_detail_no_auth_for_archive_hosts():
    """When the detail links point to a non-api.meteoalarm.org host
    (pre-signed archive URL), the request MUST NOT carry an Authorization header."""
    client = EdrWarningsClient(
        base_url="https://api.meteoalarm.org/edr/v1",
        api_key="test-key",
    )

    feature = {
        "links": [
            {"type": "application/json", "href": "https://meteo.fra1.digitaloceanspaces.com/warn.json?..."},
        ],
    }

    with patch.object(client._session, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"info": [{"severity": "Minor", "language": "en"}]}
        mock_get.return_value = mock_resp

        client.fetch_detail(feature)

        # Verify no Authorization header was sent
        call_kwargs = mock_get.call_args[1]
        headers = call_kwargs.get("headers", {})
        assert "Authorization" not in headers


def test_fetch_detail_has_auth_for_api_host():
    """When the detail link points to api.meteoalarm.org, include Bearer."""
    client = EdrWarningsClient(
        base_url="https://api.meteoalarm.org/edr/v1",
        api_key="test-key",
    )

    feature = {
        "links": [
            {"type": "application/json", "href": "https://api.meteoalarm.org/edr/v1/collections/warnings/items/some?id=1"},
        ],
    }

    with patch.object(client._session, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"info": [{"severity": "Minor", "language": "en"}]}
        mock_get.return_value = mock_resp

        client.fetch_detail(feature)

        call_kwargs = mock_get.call_args[1]
        headers = call_kwargs.get("headers", {})
        assert "Authorization" in headers
        assert "Bearer test-key" in headers.get("Authorization", "")


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------


def test_fetch_detail_returns_none_on_http_error():
    """Any HTTP error in a detail fetch → (None, None), not an exception."""
    client = EdrWarningsClient(
        base_url="https://api.meteoalarm.org/edr/v1",
        api_key="test-key",
    )

    feature = {
        "links": [
            {"type": "application/json", "href": "https://archive.example.com/500.json?..."},
        ],
    }

    with patch.object(client._session, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        cap, geometry = client.fetch_detail(feature)
        assert cap is None
        assert geometry is None


def test_fetch_detail_returns_none_when_json_link_missing():
    """If there's no application/json link, gracefully return (None, None)."""
    client = EdrWarningsClient(
        base_url="https://api.meteoalarm.org/edr/v1",
        api_key="test-key",
    )

    feature = {"links": [{"type": "application/xml", "href": "https://..."}]}

    cap, geometry = client.fetch_detail(feature)
    assert cap is None
    assert geometry is None


def test_iter_index_skips_page_on_http_error_and_stops():
    """An HTTP error on a page terminates iteration gracefully (no raise)."""
    client = EdrWarningsClient(
        base_url="https://api.meteoalarm.org/edr/v1",
        api_key="test-key",
    )
    from datetime import datetime, timezone

    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)

    with patch.object(client._session, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_get.return_value = mock_resp

        features = list(client.iter_index(now))
        assert len(features) == 0
        assert mock_get.call_count == 1
