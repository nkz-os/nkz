"""Tests for MQTT notification handling in MeteoAlertsEngine.

Uses the real WIS2 notification sample captured live on 2026-08-02
and the existing EDR CAP-JSON fixture to verify handle_notification,
_delete_entity, and prune_once.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

import weather_worker.meteo_alerts_engine as mae

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(path: str):
    with open(os.path.join(_FIXTURES, path)) as f:
        return json.load(f)


_CAP = _load("edr_cap_detail.json")
_NOTIFICATION = _load("mqtt_wis2_notification.json")


def _engine():
    eng = mae.MeteoAlertsEngine(orion_url="http://orion.test:1026")
    # Replace _get_detail for all detail-fetch tests
    eng._client._get_detail = MagicMock()
    return eng


def _patch_upsert(engine):
    engine._upsert_batch = MagicMock(return_value=True)


def _patch_delete(engine):
    engine._delete_entity = MagicMock(return_value=True)


def _polygon_geo_feature():
    """Return a geo+json Feature wrapper whose .geometry is a test polygon."""
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 1], [2, 0], [0, 0]]],
        },
        "properties": {},
    }


# ---------------------------------------------------------------------------
# handle_notification — happy path
# ---------------------------------------------------------------------------


def test_handle_notification_builds_and_upserts():
    """A real WIS2 notification + CAP/geometry fixtures → entity built & upserted."""
    eng = _engine()
    _patch_upsert(eng)

    def get_detail(href):
        if href and "/warnings/" in href:
            return _CAP  # CAP-JSON link
        if href and "/features/" in href:
            return _polygon_geo_feature()  # geo+json link
        return None

    eng._client._get_detail.side_effect = get_detail

    result = eng.handle_notification(_NOTIFICATION)
    assert result is True
    eng._upsert_batch.assert_called_once()
    args, _ = eng._upsert_batch.call_args
    tenant, entities = args[0], args[1]
    assert tenant == "default"
    assert len(entities) == 1
    entity = entities[0]
    assert entity["type"] == "WeatherAlert"
    assert entity["id"] == (
        f'urn:ngsi-ld:WeatherAlert:meteoalarm:'
        f'{_NOTIFICATION["properties"]["alertId"]}'
    )
    assert "location" in entity
    assert entity["location"]["value"]["type"] == "Polygon"
    # Must NOT use the notification's bbox-with-crs geometry
    assert "crs" not in entity["location"]["value"]


def test_handle_notification_ignores_notification_bbox():
    """The notification's own geometry (bbox+crs) is NEVER used as location."""
    eng = _engine()
    _patch_upsert(eng)
    geo_feat = _polygon_geo_feature()

    def get_detail(href):
        if href and "/warnings/" in href:
            return _CAP
        if href and "/features/" in href:
            return geo_feat
        return None

    eng._client._get_detail.side_effect = get_detail

    eng.handle_notification(_NOTIFICATION)
    entity = eng._upsert_batch.call_args[0][1][0]
    # The geo+json geometry is our test polygon
    assert entity["location"]["value"]["coordinates"][0][0] == [0, 0]
    # The notification bbox is NL coordinates — must NOT appear
    # (we check by ensuring the location comes from the geo+json mock)
    assert entity["location"]["value"]["coordinates"] != (
        _NOTIFICATION["geometry"]["coordinates"]
    )


# ---------------------------------------------------------------------------
# handle_notification — dedup
# ---------------------------------------------------------------------------


def test_handle_notification_dedup_same_alert_hubtime():
    """Same (alertId, hubTime) twice → only one upsert; second returns True."""
    eng = _engine()
    _patch_upsert(eng)

    def get_detail(href):
        if href and "/warnings/" in href:
            return _CAP
        if href and "/features/" in href:
            return _polygon_geo_feature()
        return None

    eng._client._get_detail.side_effect = get_detail

    # First call
    assert eng.handle_notification(_NOTIFICATION) is True
    assert eng._upsert_batch.call_count == 1

    # Second call — same alertId + hubTime → deduped
    assert eng.handle_notification(_NOTIFICATION) is True
    assert eng._upsert_batch.call_count == 1  # no second upsert


# ---------------------------------------------------------------------------
# handle_notification — supersede deletes
# ---------------------------------------------------------------------------


def test_handle_notification_deletes_referenced_alert_ids():
    """A notification with referencedAlertIds → _delete_entity called per ref."""
    eng = _engine()
    _patch_upsert(eng)
    _patch_delete(eng)

    notification = json.loads(json.dumps(_NOTIFICATION))
    notification["properties"]["referencedAlertIds"] = [
        "aaa-bbb-ccc",
        "ddd-eee-fff",
    ]

    def get_detail(href):
        if href and "/warnings/" in href:
            return _CAP
        if href and "/features/" in href:
            return _polygon_geo_feature()
        return None

    eng._client._get_detail.side_effect = get_detail

    eng.handle_notification(notification)

    expected_calls = [
        "urn:ngsi-ld:WeatherAlert:meteoalarm:aaa-bbb-ccc",
        "urn:ngsi-ld:WeatherAlert:meteoalarm:ddd-eee-fff",
    ]
    actual_calls = [
        c[0][1] for c in eng._delete_entity.call_args_list
    ]
    assert actual_calls == expected_calls

    # Upsert still happens
    eng._upsert_batch.assert_called_once()


def test_handle_notification_empty_referenced_alert_ids_no_delete():
    """Empty referencedAlertIds → no delete calls."""
    eng = _engine()
    _patch_upsert(eng)
    _patch_delete(eng)

    notification = json.loads(json.dumps(_NOTIFICATION))
    notification["properties"]["referencedAlertIds"] = []

    def get_detail(href):
        if href and "/warnings/" in href:
            return _CAP
        return None

    eng._client._get_detail.side_effect = get_detail

    eng.handle_notification(notification)
    eng._delete_entity.assert_not_called()


# ---------------------------------------------------------------------------
# handle_notification — boundary / error cases
# ---------------------------------------------------------------------------


def test_handle_notification_no_geo_json_link_still_upserts():
    """No geo+json link → entity built without location, upserted anyway."""
    eng = _engine()
    _patch_upsert(eng)

    # Notification with no geometry link in its links array
    notification = json.loads(json.dumps(_NOTIFICATION))
    notification["links"] = [
        l for l in notification["links"]
        if l.get("type") != "application/geo+json"
    ]

    def get_detail(href):
        return _CAP  # CAP-JSON only

    eng._client._get_detail.side_effect = get_detail

    assert eng.handle_notification(notification) is True
    eng._upsert_batch.assert_called_once()
    entity = eng._upsert_batch.call_args[0][1][0]
    assert "location" not in entity


def test_handle_notification_no_cap_json_link_returns_false():
    """No application/json link → False, no upsert."""
    eng = _engine()
    _patch_upsert(eng)

    notification = json.loads(json.dumps(_NOTIFICATION))
    notification["links"] = [
        l for l in notification["links"]
        if l.get("type") != "application/json"
    ]

    assert eng.handle_notification(notification) is False
    eng._upsert_batch.assert_not_called()


def test_handle_notification_missing_alert_id_returns_false():
    """No alertId in properties → False, no upsert."""
    eng = _engine()
    _patch_upsert(eng)

    notification = json.loads(json.dumps(_NOTIFICATION))
    del notification["properties"]["alertId"]

    assert eng.handle_notification(notification) is False
    eng._upsert_batch.assert_not_called()


def test_handle_notification_cap_fetch_fails_returns_false():
    """_get_detail returns None for CAP-JSON → False, no upsert."""
    eng = _engine()
    _patch_upsert(eng)

    def get_detail(href):
        return None  # simulate fetch failure

    eng._client._get_detail.side_effect = get_detail

    assert eng.handle_notification(_NOTIFICATION) is False
    eng._upsert_batch.assert_not_called()


# ---------------------------------------------------------------------------
# prune_once
# ---------------------------------------------------------------------------


def test_prune_once_calls_prune_expired():
    eng = _engine()
    eng._prune_expired_alerts = MagicMock(return_value=5)

    result = eng.prune_once()
    assert result == 5
    eng._prune_expired_alerts.assert_called_once_with("default")


# ---------------------------------------------------------------------------
# _delete_entity
# ---------------------------------------------------------------------------


def test_delete_entity_204_returns_true():
    eng = _engine()
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    eng._session.delete = MagicMock(return_value=mock_resp)

    assert eng._delete_entity("default", "urn:ngsi-ld:WeatherAlert:meteoalarm:xyz") is True


def test_delete_entity_404_returns_true():
    eng = _engine()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    eng._session.delete = MagicMock(return_value=mock_resp)

    assert eng._delete_entity("default", "urn:ngsi-ld:WeatherAlert:meteoalarm:xyz") is True


def test_delete_entity_500_returns_false():
    eng = _engine()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "internal error"
    eng._session.delete = MagicMock(return_value=mock_resp)

    assert eng._delete_entity("default", "urn:ngsi-ld:WeatherAlert:meteoalarm:xyz") is False


def test_delete_entity_exception_returns_false():
    eng = _engine()
    eng._session.delete = MagicMock(side_effect=Exception("connection lost"))

    assert eng._delete_entity("default", "urn:ngsi-ld:WeatherAlert:meteoalarm:xyz") is False
