"""Tests for MeteoAlertsEngine batch upsert status handling.

Orion-LD returns HTTP 207 (Multi-Status) for entityOperations/upsert even when
every entity succeeds — the body carries `success`/`errors` arrays. Treating 207
as a blanket failure logged a false error every cycle and reported
entities_upserted=0 despite full success. Incident follow-up 2026-07-25.
"""

import json as _json
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


# --- Payload chunking (Orion-LD rejects bodies > ~1 MB with "payload missing") ---

def _entities(n, filler_bytes=0):
    ents = []
    for i in range(n):
        e = {
            "id": f"urn:ngsi-ld:WeatherAlert:meteoalarm:{i:05d}",
            "type": "WeatherAlert",
            "severity": {"type": "Property", "value": "moderate"},
        }
        if filler_bytes:
            e["description"] = {"type": "Property", "value": "x" * filler_bytes}
        ents.append(e)
    return ents


def test_batch_over_size_limit_is_split_into_multiple_posts(monkeypatch):
    eng = _engine()
    monkeypatch.setattr(mae, "_MAX_UPSERT_BYTES", 1000, raising=False)
    ents = _entities(10, filler_bytes=200)  # each entity well over 200 bytes
    posted = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posted.append(json)
        return _resp(204)

    with patch.object(mae.requests, "post", side_effect=fake_post):
        assert eng._upsert_batch("default", ents) is True

    assert len(posted) > 1, "large batch must be split into multiple POSTs"
    for chunk in posted:
        assert len(_json.dumps(chunk).encode()) <= 1000, "each chunk must stay under the limit"
    flat = [e for chunk in posted for e in chunk]
    assert flat == ents, "every entity posted exactly once, order preserved"


def test_batch_under_size_limit_is_single_post(monkeypatch):
    eng = _engine()
    monkeypatch.setattr(mae, "_MAX_UPSERT_BYTES", 1_000_000, raising=False)
    ents = _entities(3)
    with patch.object(mae.requests, "post", return_value=_resp(204)) as m:
        assert eng._upsert_batch("default", ents) is True
    assert m.call_count == 1, "small batch must not be over-split"


def test_one_failing_chunk_returns_false_but_still_posts_the_rest(monkeypatch):
    eng = _engine()
    monkeypatch.setattr(mae, "_MAX_UPSERT_BYTES", 1000, raising=False)
    ents = _entities(10, filler_bytes=200)
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        return _resp(500 if calls["n"] == 1 else 204, text="boom")

    with patch.object(mae.requests, "post", side_effect=fake_post):
        assert eng._upsert_batch("default", ents) is False
    assert calls["n"] > 1, "must keep posting remaining chunks after one fails"
