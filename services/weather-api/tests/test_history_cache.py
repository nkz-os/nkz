"""MinIO cache with fakes: key shape, put-once for closed years, disabled mode."""
import os, sys
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import gzip, io, json, logging
import pytest

from app.services.history import cache as hc


class _Missing(Exception):
    code = "NoSuchKey"


class FakeMinio:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.buckets = set()

    def bucket_exists(self, b):
        return b in self.buckets

    def make_bucket(self, b):
        self.buckets.add(b)

    def stat_object(self, b, n):
        if (b, n) not in self.objects:
            raise _Missing()
        return object()

    def get_object(self, b, n):
        if (b, n) not in self.objects:
            raise _Missing()
        data = self.objects[(b, n)]

        class R:
            def read(self_):
                return data

            def close(self_):
                pass

            def release_conn(self_):
                pass
        return R()

    def put_object(self, b, n, stream, length, content_type=None):
        self.puts.append((b, n))
        self.objects[(b, n)] = stream.read()


def test_key_shape_has_no_tenant_or_parcel():
    k = hc.cache_key(42.25, -1.5, 579.6, 2020)
    assert k == "weather-history/era5/v1/42.25_-1.50_580/2020.json.gz"


def test_roundtrip_gzip_json():
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history")
    doc = {"year": 2020, "t": [1.0, None]}
    k = hc.cache_key(42.25, -1.5, 580, 2020)
    assert c.get(k) is None
    assert c.put(k, doc, closed=True) is True
    assert json.loads(gzip.decompress(fake.objects[("weather-history", k)])) == doc
    assert c.get(k) == doc
    assert "weather-history" in fake.buckets


def test_closed_year_never_rewritten():
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history")
    k = hc.cache_key(42.25, -1.5, 580, 2020)
    assert c.put(k, {"v": 1}, closed=True)
    assert c.put(k, {"v": 2}, closed=True) is False
    assert len(fake.puts) == 1
    assert c.get(k) == {"v": 1}


def test_open_year_is_overwritten():
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history")
    k = hc.cache_key(42.25, -1.5, 580, 2026)
    c.put(k, {"v": 1}, closed=False)
    c.put(k, {"v": 2}, closed=False)
    assert len(fake.puts) == 2 and c.get(k) == {"v": 2}


def test_backend_errors_degrade_to_miss(caplog):
    class Boom(FakeMinio):
        def get_object(self, b, n):
            raise RuntimeError("down")

        def stat_object(self, b, n):
            raise RuntimeError("down")
    c = hc.HistoryCache(Boom(), "weather-history")
    with caplog.at_level(logging.WARNING):
        assert c.get("k") is None
        assert c.put("k", {}, closed=True) is False


def test_missing_env_disables_cache_with_one_warning(monkeypatch, caplog):
    for v in ("MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"):
        monkeypatch.delenv(v, raising=False)
    hc.reset_cache()
    with caplog.at_level(logging.WARNING):
        c1 = hc.get_cache()
        c2 = hc.get_cache()
    assert c1 is c2 and c1.enabled is False
    assert sum("disabled" in r.message for r in caplog.records) == 1
    assert c1.get("k") is None and c1.put("k", {}, closed=True) is False
    hc.reset_cache()


def test_env_builds_client_with_default_bucket(monkeypatch):
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "a")
    monkeypatch.setenv("MINIO_SECRET_KEY", "b")
    monkeypatch.delenv("HISTORY_BUCKET", raising=False)
    hc.reset_cache()
    c = hc.get_cache()
    assert c.enabled and c.bucket == "weather-history"
    hc.reset_cache()
