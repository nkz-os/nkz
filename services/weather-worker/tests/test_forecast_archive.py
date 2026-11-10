"""Tests for the daily parcel forecast archive (weather_worker.forecast_archive).

HTTP (Orion, Open-Meteo) and MinIO are mocked; no network. Synthetic tenants
and coordinates only.
"""

import gzip
import io
import json
import logging
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

import weather_worker.forecast_archive as fa

NOW = datetime(2030, 1, 10, 3, 30, tzinfo=timezone.utc)
MODELS = ("best_match", "ecmwf_ifs025")
START = datetime(2030, 1, 10)


def _resp(status, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else []
    r.text = ""
    return r


def _parcel(pid, lon, lat, elevation=None):
    p = {
        "id": pid,
        "type": "AgriParcel",
        "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [lon, lat]}},
    }
    if elevation is not None:
        p["elevation"] = {"type": "Property", "value": elevation}
    return p


def _hours(days=16):
    out = []
    for d in range(days):
        day = datetime(2030, 1, 10 + d) if d < 22 else None
        for h in range(24):
            out.append(f"{day.strftime('%Y-%m-%d')}T{h:02d}:00")
    return out


def _openmeteo(cloud=None, wind=None, tmin_missing_from=None, hourly_days=16, grid_elev=812.0):
    """Response shaped like Open-Meteo with a model list (suffixed keys)."""
    dates = [f"2030-01-{10 + d:02d}" for d in range(16)]
    times = _hours(hourly_days)
    body = {"elevation": grid_elev, "daily": {"time": dates}, "hourly": {"time": times}}
    for m in MODELS:
        tmin = [float(i) for i in range(16)]
        tmax = [float(i) + 10 for i in range(16)]
        if m == "ecmwf_ifs025" and tmin_missing_from is not None:
            for i in range(tmin_missing_from, 16):
                tmin[i] = None
                tmax[i] = None
        body["daily"][f"temperature_2m_min_{m}"] = tmin
        body["daily"][f"temperature_2m_max_{m}"] = tmax
        n = len(times)
        body["hourly"][f"cloud_cover_{m}"] = cloud(m, times) if cloud else [50.0] * n
        body["hourly"][f"wind_speed_10m_{m}"] = wind(m, times) if wind else [2.0] * n
    return body


class FakeMinio:
    def __init__(self, fail_for=None):
        self.puts = []
        self.buckets = set()
        self.fail_for = fail_for or set()

    def bucket_exists(self, b):
        return b in self.buckets

    def make_bucket(self, b):
        self.buckets.add(b)

    def put_object(self, bucket, name, data, length, **kw):
        if any(name.startswith(t + "/") for t in self.fail_for):
            raise OSError("upload failed")
        self.puts.append((bucket, name, data.read()))

    def objects(self):
        return {(b, n): json.loads(gzip.decompress(raw)) for b, n, raw in self.puts}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("MINIO_ENDPOINT", "minio.test:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "ak")
    monkeypatch.setenv("MINIO_SECRET_KEY", "sk")
    monkeypatch.setenv("MINIO_SECURE", "false")
    monkeypatch.setenv("PARCEL_ENGINE_TENANTS", "acme")
    monkeypatch.delenv("FORECAST_ARCHIVE_BUCKET", raising=False)
    monkeypatch.delenv("ELEVATION_SERVICE_URL", raising=False)
    monkeypatch.setattr(fa.time, "sleep", lambda s: None)


def _run(parcels_by_tenant, openmeteo, minio, monkeypatch=None):
    """Run the archive with mocked Orion / Open-Meteo / MinIO. Returns (code, calls)."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, timeout))
        if "/ngsi-ld/v1/entities" in url:
            tenant = (headers or {}).get("NGSILD-Tenant") or (headers or {}).get("Fiware-Service")
            val = parcels_by_tenant.get(tenant)
            if isinstance(val, Exception):
                raise val
            return _resp(200, val or [])
        result = openmeteo(params) if callable(openmeteo) else openmeteo
        if isinstance(result, Exception):
            raise result
        return _resp(200, result)

    with patch.object(fa.requests, "get", side_effect=fake_get), \
         patch.object(fa, "_make_minio_client", return_value=minio):
        code = fa.run(now=NOW)
    return code, calls


def test_archive_builds_both_models_with_16_days():
    m = FakeMinio()
    code, calls = _run({"acme": [_parcel("urn:p1", -1.5, 42.0, 400)]}, _openmeteo(), m)
    assert code == 0
    objs = m.objects()
    assert list(objs) == [("weather-forecast-archive", "acme/2030-01-10.json.gz")]
    doc = objs[("weather-forecast-archive", "acme/2030-01-10.json.gz")]
    assert doc["issued_at"] == "2030-01-10T03:30:00+00:00"
    p = doc["parcels"]["urn:p1"]
    assert (p["lat"], p["lon"]) == (42.0, -1.5)
    assert p["elevation"] == 400.0
    assert p["grid_elevation"] == 812.0
    assert set(p["models"]) == set(MODELS)
    for mname in MODELS:
        days = p["models"][mname]["days"]
        assert len(days) == 16
        assert set(days[0]) == {"date", "tmin_c", "tmax_c", "night_cloud_pct", "night_wind_ms"}
        assert days[0]["date"] == "2030-01-10"
        assert days[3]["tmin_c"] == 3.0 and days[3]["tmax_c"] == 13.0
    # request contract
    om = [c for c in calls if "/ngsi-ld/" not in c[0]]
    assert len(om) == 1
    url, params, timeout = om[0]
    assert url.endswith("/forecast")
    assert params["models"] == "best_match,ecmwf_ifs025"
    assert params["forecast_days"] == 16
    assert params["wind_speed_unit"] == "ms"
    assert params["timezone"] == "auto"
    assert params["daily"] == "temperature_2m_min,temperature_2m_max"
    assert params["hourly"] == "cloud_cover,wind_speed_10m"
    assert timeout == 60


def test_night_cloud_and_wind_use_18_to_08_window_in_ms():
    def cloud(m, times):
        # 100 inside the night of 2030-01-10 (18h..07h next day), 0 elsewhere
        out = []
        for t in times:
            d, h = t.split("T")
            hh = int(h[:2])
            inside = (d == "2030-01-10" and hh >= 18) or (d == "2030-01-11" and hh < 8)
            out.append(100.0 if inside else 0.0)
        return out

    def wind(m, times):
        out = []
        for t in times:
            d, h = t.split("T")
            hh = int(h[:2])
            if d == "2030-01-10" and hh >= 18:
                out.append(4.0)
            elif d == "2030-01-11" and hh < 8:
                out.append(6.0)
            elif d == "2030-01-11" and hh == 8:
                out.append(1000.0)  # hour 08 must be excluded
            else:
                out.append(0.0)
        return out

    m = FakeMinio()
    code, _ = _run({"acme": [_parcel("urn:p1", -1.5, 42.0)]}, _openmeteo(cloud, wind), m)
    assert code == 0
    doc = next(iter(m.objects().values()))
    d0 = doc["parcels"]["urn:p1"]["models"]["best_match"]["days"][0]
    assert d0["night_cloud_pct"] == 100.0
    assert d0["night_wind_ms"] == pytest.approx((6 * 4.0 + 8 * 6.0) / 14)
    d1 = doc["parcels"]["urn:p1"]["models"]["best_match"]["days"][1]
    assert d1["night_cloud_pct"] == 0.0


def test_rerun_same_day_overwrites_same_object():
    m = FakeMinio()
    for _ in range(2):
        code, _ = _run({"acme": [_parcel("urn:p1", -1.5, 42.0)]}, _openmeteo(), m)
        assert code == 0
    assert len(m.puts) == 2
    assert m.puts[0][:2] == m.puts[1][:2] == ("weather-forecast-archive", "acme/2030-01-10.json.gz")


def test_bucket_from_env_and_created_if_missing(monkeypatch):
    monkeypatch.setenv("FORECAST_ARCHIVE_BUCKET", "custom-bucket")
    m = FakeMinio()
    _run({"acme": [_parcel("urn:p1", -1.5, 42.0)]}, _openmeteo(), m)
    assert "custom-bucket" in m.buckets
    assert m.puts[0][0] == "custom-bucket"


def test_one_parcel_failure_keeps_the_others_and_logs(caplog):
    far = _parcel("urn:far", 10.0, 50.0)  # separate cluster
    near = _parcel("urn:near", -1.5, 42.0)

    def om(params):
        if abs(float(params["latitude"]) - 50.0) < 0.5:
            return OSError("boom")
        return _openmeteo()

    m = FakeMinio()
    with caplog.at_level(logging.INFO):
        code, _ = _run({"acme": [near, far]}, om, m)
    assert code == 0
    doc = next(iter(m.objects().values()))
    assert set(doc["parcels"]) == {"urn:near"}
    text = caplog.text
    assert "urn:far" in text or "cluster" in text.lower()
    assert "acme" in text and "archived=1" in text and "failed=1" in text


def test_tenant_failure_does_not_stop_other_tenants(monkeypatch):
    monkeypatch.setenv("PARCEL_ENGINE_TENANTS", "acme,tenant-a")
    m = FakeMinio()
    code, _ = _run(
        {"acme": OSError("orion down"), "tenant-a": [_parcel("urn:p2", -1.5, 42.0)]},
        _openmeteo(),
        m,
    )
    assert [n for _, n, _ in m.puts] == ["tenant-a/2030-01-10.json.gz"]
    assert code == 1  # a tenant failed


def test_upload_failure_of_one_tenant_does_not_stop_others(monkeypatch):
    monkeypatch.setenv("PARCEL_ENGINE_TENANTS", "acme,tenant-a")
    m = FakeMinio(fail_for={"acme"})
    code, _ = _run(
        {"acme": [_parcel("urn:p1", -1.5, 42.0)], "tenant-a": [_parcel("urn:p2", 3.0, 41.0)]},
        _openmeteo(),
        m,
    )
    assert [n for _, n, _ in m.puts] == ["tenant-a/2030-01-10.json.gz"]
    assert code == 1


def test_all_failed_exits_non_zero():
    m = FakeMinio()
    code, _ = _run({"acme": [_parcel("urn:p1", -1.5, 42.0)]}, OSError("down"), m)
    assert code == 1
    assert m.puts == []


def test_retries_three_times_then_gives_up():
    m = FakeMinio()
    code, calls = _run({"acme": [_parcel("urn:p1", -1.5, 42.0)]}, OSError("down"), m)
    assert len([c for c in calls if "/ngsi-ld/" not in c[0]]) == 3


def test_tenant_without_parcels_is_not_a_failure():
    m = FakeMinio()
    code, _ = _run({"acme": []}, _openmeteo(), m)
    assert code == 0
    assert m.puts == []


def test_no_write_to_orion_or_timeseries():
    m = FakeMinio()
    with patch.object(fa.requests, "post") as post, patch.object(fa.requests, "patch") as patch_, \
         patch.object(fa.requests, "put") as put, patch.object(fa.requests, "delete") as delete:
        _run({"acme": [_parcel("urn:p1", -1.5, 42.0)]}, _openmeteo(), m)
    for mock in (post, patch_, put, delete):
        mock.assert_not_called()


def test_missing_model_values_are_null():
    m = FakeMinio()
    code, _ = _run({"acme": [_parcel("urn:p1", -1.5, 42.0)]}, _openmeteo(tmin_missing_from=12), m)
    assert code == 0
    days = next(iter(m.objects().values()))["parcels"]["urn:p1"]["models"]["ecmwf_ifs025"]["days"]
    assert len(days) == 16
    assert days[11]["tmin_c"] == 11.0
    assert days[12]["tmin_c"] is None and days[15]["tmax_c"] is None
    best = next(iter(m.objects().values()))["parcels"]["urn:p1"]["models"]["best_match"]["days"]
    assert best[15]["tmin_c"] == 15.0


def test_incomplete_night_is_null():
    # hourly stops at the end of day 16: the last night lacks next-morning hours
    m = FakeMinio()
    code, _ = _run({"acme": [_parcel("urn:p1", -1.5, 42.0)]}, _openmeteo(), m)
    days = next(iter(m.objects().values()))["parcels"]["urn:p1"]["models"]["best_match"]["days"]
    assert days[14]["night_cloud_pct"] == 50.0
    assert days[15]["night_cloud_pct"] is None
    assert days[15]["night_wind_ms"] is None


def test_null_hour_makes_night_null():
    def cloud(m, times):
        out = [50.0] * len(times)
        out[times.index("2030-01-10T22:00")] = None
        return out

    m = FakeMinio()
    _run({"acme": [_parcel("urn:p1", -1.5, 42.0)]}, _openmeteo(cloud=cloud), m)
    days = next(iter(m.objects().values()))["parcels"]["urn:p1"]["models"]["best_match"]["days"]
    assert days[0]["night_cloud_pct"] is None
    assert days[0]["night_wind_ms"] == 2.0


@pytest.mark.parametrize("missing", ["MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"])
def test_missing_minio_env_exits_non_zero(monkeypatch, missing, caplog):
    monkeypatch.delenv(missing)
    with patch.object(fa.requests, "get") as get, caplog.at_level(logging.ERROR):
        code = fa.run(now=NOW)
    assert code != 0
    assert missing in caplog.text
    get.assert_not_called()


def test_logs_do_not_contain_coordinates_or_credentials(caplog):
    m = FakeMinio()
    with caplog.at_level(logging.DEBUG):
        _run({"acme": [_parcel("urn:p1", -1.5, 42.0)]}, _openmeteo(), m)
    assert "-1.5" not in caplog.text and "42.0" not in caplog.text
    assert "sk" not in caplog.text.split() and "ak" not in caplog.text.split()


def test_parcel_without_elevation_is_null_not_zero():
    m = FakeMinio()
    code, _ = _run({"acme": [_parcel("urn:p1", -1.5, 42.0)]}, _openmeteo(), m)
    assert code == 0
    doc = m.objects()[("weather-forecast-archive", "acme/2030-01-10.json.gz")]
    assert doc["parcels"]["urn:p1"]["elevation"] is None


def test_no_tenants_discovered_exits_non_zero(monkeypatch):
    monkeypatch.delenv("PARCEL_ENGINE_TENANTS", raising=False)
    monkeypatch.setattr(fa.ParcelWeatherEngine, "_get_active_tenants", lambda self: [])
    m = FakeMinio()
    code, _ = _run({}, _openmeteo(), m)
    assert code == 1
    assert m.puts == []
