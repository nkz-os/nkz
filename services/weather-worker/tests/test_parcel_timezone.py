"""ParcelWeatherEngine writes AgriParcel.timeZone (IANA, from Open-Meteo timezone=auto) once per parcel."""

import os
import sys
from unittest.mock import MagicMock, patch

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

import weather_worker.parcel_engine as pe  # noqa: E402


def _engine():
    return pe.ParcelWeatherEngine(orion_url="http://orion.test", context_url="http://ctx.test/c.json",
                                  openmeteo_url="http://om.test/v1")


def _parcel(pid, tz=None):
    p = {"id": f"urn:ngsi-ld:AgriParcel:{pid}", "_tenant": "t-1", "_centroid": (-1.6, 42.8)}
    if tz:
        p["timeZone"] = {"type": "Property", "value": tz}
    return p


def _om(tz):
    r = MagicMock(status_code=200)
    r.json.return_value = {"timezone": tz, "utc_offset_seconds": 7200}
    return r


def test_missing_timezone_is_looked_up_and_written():
    eng = _engine()
    with patch.object(pe.requests, "get", return_value=_om("Europe/Madrid")) as get, \
         patch.object(pe.requests, "post", return_value=MagicMock(status_code=204)) as post:
        stats = eng._ensure_parcel_timezones([_parcel("p1")])
    assert stats == {"timezone_written": 1, "timezone_errors": 0}
    assert get.call_args.kwargs["params"]["timezone"] == "auto"
    url = post.call_args.args[0]
    assert url == "http://orion.test/ngsi-ld/v1/entities/urn:ngsi-ld:AgriParcel:p1/attrs"
    assert post.call_args.kwargs["json"] == {"timeZone": {"type": "Property", "value": "Europe/Madrid"}}


def test_parcel_with_timezone_is_left_alone():
    eng = _engine()
    with patch.object(pe.requests, "get") as get, patch.object(pe.requests, "post") as post:
        stats = eng._ensure_parcel_timezones([_parcel("p1", tz="Europe/Lisbon")])
    get.assert_not_called(); post.assert_not_called()
    assert stats == {"timezone_written": 0, "timezone_errors": 0}


def test_unusable_timezone_is_not_written():
    eng = _engine()
    with patch.object(pe.requests, "get", return_value=_om("not a zone; drop")), \
         patch.object(pe.requests, "post") as post:
        stats = eng._ensure_parcel_timezones([_parcel("p1")])
    post.assert_not_called()
    assert stats == {"timezone_written": 0, "timezone_errors": 1}


def test_lookups_are_capped_per_cycle():
    eng = _engine()
    parcels = [_parcel(f"p{i}") for i in range(eng.TIMEZONE_LOOKUPS_PER_CYCLE + 5)]
    with patch.object(pe.requests, "get", return_value=_om("Europe/Madrid")) as get, \
         patch.object(pe.requests, "post", return_value=MagicMock(status_code=204)):
        eng._ensure_parcel_timezones(parcels)
    assert get.call_count == eng.TIMEZONE_LOOKUPS_PER_CYCLE


def test_discovery_reads_the_timezone():
    eng = _engine()
    resp = MagicMock(status_code=200)
    resp.json.return_value = []
    with patch.object(eng, "_get_active_tenants", return_value=["t-1"]), \
         patch.object(pe.requests, "get", return_value=resp) as get:
        eng._fetch_all_parcels()
    assert "timeZone" in get.call_args.kwargs["params"]["attrs"].split(",")


def test_run_once_ensures_timezones_of_located_parcels():
    eng = _engine()
    parcel = {"id": "urn:ngsi-ld:AgriParcel:p1", "_tenant": "t-1",
              "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [-1.6, 42.8]}}}
    with patch.object(eng, "_fetch_all_parcels", return_value=[parcel]), \
         patch.object(eng, "_ensure_parcel_timezones", return_value={"timezone_written": 1, "timezone_errors": 0}) as ens, \
         patch.object(eng, "_fetch_openmeteo", return_value=None), \
         patch.object(eng, "_publish_closed_days", return_value={}), \
         patch.object(eng, "_prune_orphan_weather_observed", return_value=0):
        stats = eng.run_once()
    assert ens.call_count == 1 and ens.call_args.args[0][0]["id"] == "urn:ngsi-ld:AgriParcel:p1"
    assert stats["timezone_written"] == 1
