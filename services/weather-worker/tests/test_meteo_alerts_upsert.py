"""Tests for MeteoAlertsEngine batch upsert status handling.

Orion-LD returns HTTP 207 (Multi-Status) for entityOperations/upsert even when
every entity succeeds — the body carries `success`/`errors` arrays. Treating 207
as a blanket failure logged a false error every cycle and reported
entities_upserted=0 despite full success. Incident follow-up 2026-07-25.
"""

import os
import sys
from unittest.mock import MagicMock, patch

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

import weather_worker.meteo_alerts_engine as mae


def _engine():
    return mae.MeteoAlertsEngine(orion_url="http://orion.test:1026")


def _resp(status, payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.content = b"x" if payload is not None else b""
    r.json.return_value = payload if payload is not None else {}
    return r


def test_207_full_success_returns_true():
    eng = _engine()
    ents = [{"id": "urn:ngsi-ld:WeatherAlert:1", "type": "WeatherAlert"}]
    with patch.object(mae.requests, "post",
                      return_value=_resp(207, {"success": ["urn:ngsi-ld:WeatherAlert:1"], "errors": []})):
        assert eng._upsert_batch("default", ents) is True


def test_207_with_errors_returns_false():
    eng = _engine()
    ents = [{"id": "urn:ngsi-ld:WeatherAlert:1", "type": "WeatherAlert"}]
    with patch.object(mae.requests, "post",
                      return_value=_resp(207, {"success": [], "errors": [{"entityId": "x", "error": {"title": "bad"}}]})):
        assert eng._upsert_batch("default", ents) is False


def test_204_returns_true():
    eng = _engine()
    ents = [{"id": "urn:ngsi-ld:WeatherAlert:1", "type": "WeatherAlert"}]
    with patch.object(mae.requests, "post", return_value=_resp(204)):
        assert eng._upsert_batch("default", ents) is True


def test_500_returns_false():
    eng = _engine()
    ents = [{"id": "urn:ngsi-ld:WeatherAlert:1", "type": "WeatherAlert"}]
    with patch.object(mae.requests, "post", return_value=_resp(500, text="boom")):
        assert eng._upsert_batch("default", ents) is False
