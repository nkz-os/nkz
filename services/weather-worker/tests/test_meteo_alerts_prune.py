"""Tests for MeteoAlertsEngine expired WeatherAlert pruning."""

import os
import sys
from unittest.mock import MagicMock, patch

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

import weather_worker.meteo_alerts_engine as mae


def _resp(status, payload=None):
    r = MagicMock()
    r.status_code = status
    r.text = ""
    r.json.return_value = payload if payload is not None else []
    return r


def _engine():
    return mae.MeteoAlertsEngine(orion_url="http://orion.test:1026")


def test_prune_deletes_expired_weather_alerts():
    eng = _engine()
    expired = [
        {"id": "urn:ngsi-ld:WeatherAlert:meteoalarm:old-1"},
        {"id": "urn:ngsi-ld:WeatherAlert:meteoalarm:old-2"},
    ]

    def fake_get(url, params=None, headers=None, timeout=None):
        if params and params.get("type") == "WeatherAlert":
            return _resp(200, expired)
        return _resp(404)

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["ids"] = json
        return _resp(204)

    with patch.object(eng._session, "get", side_effect=fake_get), \
         patch.object(eng._session, "post", side_effect=fake_post):
        deleted = eng._prune_expired_alerts("default")

    assert deleted == 2
    assert "entityOperations/delete" in captured["url"]
    assert captured["ids"] == [e["id"] for e in expired]


def test_prune_skips_on_query_error():
    eng = _engine()

    with patch.object(eng._session, "get", return_value=_resp(500)), \
         patch.object(eng._session, "post") as post:
        deleted = eng._prune_expired_alerts("default")

    assert deleted == 0
    post.assert_not_called()


def test_run_once_prunes_even_when_no_alerts_fetched():
    eng = _engine()

    with patch.object(eng, "_fetch_all_alerts", return_value=[]), \
         patch.object(eng, "_prune_expired_alerts", return_value=3) as prune:
        stats = eng.run_once()

    prune.assert_called_once_with("default")
    assert stats["entities_pruned"] == 3
    assert stats["alerts_fetched"] == 0
