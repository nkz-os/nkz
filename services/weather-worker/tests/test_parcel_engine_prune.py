"""Tests for ParcelWeatherEngine orphan-WeatherObserved pruning.

The engine writes one WeatherObserved per parcel (id ...:parcel-<id>,
locatedAt -> parcel). When a parcel is deleted its WeatherObserved is orphaned;
the prune step removes those whose parcel is no longer live — with a false-zero
guard (no prune on query error or missing @context).
"""

import os
import sys
from unittest.mock import MagicMock, patch

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # services/weather-worker
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))  # services/ — for `common.ngsi_headers`

import weather_worker.parcel_engine as pe


def _resp(status, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else []
    return r


def _engine():
    return pe.ParcelWeatherEngine(
        orion_url="http://orion.test:1026",
        context_url="http://ctx.test/ngsi-ld-context.json",
    )


def test_prune_deletes_only_orphans():
    eng = _engine()

    def fake_get(url, params=None, headers=None, timeout=None):
        if params and params.get("type") == "AgriParcel":
            return _resp(200, [{"id": "urn:ngsi-ld:AgriParcel:P1"}])
        if params and params.get("type") == "WeatherObserved":
            return _resp(200, [
                {"id": "urn:ngsi-ld:WeatherObserved:t:parcel-P1",
                 "locatedAt": {"type": "Relationship", "object": "urn:ngsi-ld:AgriParcel:P1"}},
                {"id": "urn:ngsi-ld:WeatherObserved:t:parcel-GONE",
                 "locatedAt": {"type": "Relationship", "object": "urn:ngsi-ld:AgriParcel:GONE"}},
            ])
        return _resp(404)

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["ids"] = json
        return _resp(204)

    with patch.object(pe.requests, "get", side_effect=fake_get), \
         patch.object(pe.requests, "post", side_effect=fake_post):
        deleted = eng._prune_orphan_weather_observed("t")

    assert deleted == 1
    assert "entityOperations/delete" in captured["url"]
    assert captured["ids"] == ["urn:ngsi-ld:WeatherObserved:t:parcel-GONE"]


def test_prune_skips_without_context_url():
    """No @context => a type query can false-zero => never prune."""
    eng = pe.ParcelWeatherEngine(orion_url="http://orion.test:1026", context_url="")
    with patch.object(pe.requests, "get") as g, patch.object(pe.requests, "post") as p:
        deleted = eng._prune_orphan_weather_observed("t")
    assert deleted == 0
    g.assert_not_called()
    p.assert_not_called()


def test_prune_skips_on_parcel_fetch_error():
    """If the live-parcel query is not 200, do not prune (false-zero guard)."""
    eng = _engine()

    def fake_get(url, params=None, headers=None, timeout=None):
        if params and params.get("type") == "AgriParcel":
            return _resp(500)
        return _resp(200, [])

    with patch.object(pe.requests, "get", side_effect=fake_get), \
         patch.object(pe.requests, "post") as p:
        deleted = eng._prune_orphan_weather_observed("t")
    assert deleted == 0
    p.assert_not_called()
