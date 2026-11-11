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
        self.meta = {}
        self.stat_error = None
        self.put_error = None

    def bucket_exists(self, b):
        return b in self.buckets

    def make_bucket(self, b):
        self.buckets.add(b)

    def stat_object(self, b, n):
        if self.stat_error is not None:
            raise self.stat_error
        if (b, n) not in self.objects:
            raise _Missing()

        class S:
            pass
        st = S()
        st.metadata = {"x-amz-meta-" + k: v for k, v in self.meta.get((b, n), {}).items()}
        return st

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

    def put_object(self, b, n, stream, length, content_type=None, metadata=None):
        if self.put_error is not None:
            raise self.put_error
        self.puts.append((b, n))
        self.objects[(b, n)] = stream.read()
        self.meta[(b, n)] = dict(metadata or {})


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
    assert json.loads(gzip.decompress(fake.objects[("weather-history", k)])) == {**doc, "closed": True}
    assert c.get(k) == {**doc, "closed": True}
    assert "weather-history" in fake.buckets


def test_closed_year_never_rewritten_same_cell():
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history")
    k = hc.cache_key(42.25, -1.5, 580, 2020)
    assert c.put(k, {"v": 1}, closed=True)
    assert c.put(k, {"v": 2}, closed=True) is False
    assert len(fake.puts) == 1
    assert c.get(k) == {"v": 1, "closed": True}


def test_open_year_is_overwritten():
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history")
    k = hc.cache_key(42.25, -1.5, 580, 2026)
    c.put(k, {"v": 1}, closed=False)
    c.put(k, {"v": 2}, closed=False)
    assert len(fake.puts) == 2 and c.get(k) == {"v": 2, "closed": False}


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


# ---- final fix wave: R2 stored closed flag, R4 breaker and client timeouts ----

CELL = {"cell_lat": 42.25, "cell_lon": -1.5}
K = "weather-history/era5/v1/42.25_-1.50_580/2020.json.gz"


def _doc(**kw):
    d = {"year": 2020, **CELL}
    d.update(kw)
    return d


def test_put_writes_closed_flag_in_metadata_and_json():
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history")
    assert c.put(K, _doc(closed=False), closed=True) is True
    assert fake.meta[("weather-history", K)]["closed"] == "1"
    assert c.get(K)["closed"] is True
    assert c.put("k2", _doc(closed=True), closed=False) is True
    assert fake.meta[("weather-history", "k2")]["closed"] == "0"
    assert c.get("k2")["closed"] is False


def test_open_object_upgrades_to_closed_and_stays():
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history")
    assert c.put(K, _doc(v=1), closed=False)
    assert c.put(K, _doc(v=2), closed=True) is True          # open -> closed
    assert c.get(K)["v"] == 2 and c.get(K)["closed"] is True
    assert c.put(K, _doc(v=3), closed=True) is False         # closed -> anything: never


def test_closed_object_is_never_downgraded_to_open():
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history")
    assert c.put(K, _doc(v=1), closed=True)
    assert c.put(K, _doc(v=2), closed=False) is False
    assert c.get(K)["v"] == 1 and len(fake.puts) == 1


def test_closed_object_survives_a_transient_read_failure_of_the_stat():
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history", clock=lambda: 0.0)
    c.put(K, _doc(v=1), closed=True)
    fake.stat_error = RuntimeError("blip")
    assert c.put(K, _doc(v=2), closed=False) is False         # cannot tell: do not overwrite
    fake.stat_error = None
    c2 = hc.HistoryCache(fake, "weather-history")
    assert c2.get(K)["v"] == 1


def test_closed_object_survives_when_get_failed_but_stat_works():
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history")
    c.put(K, _doc(v=1), closed=True)
    real_get = fake.get_object
    fake.get_object = lambda b, n: (_ for _ in ()).throw(RuntimeError("blip"))
    assert c.get(K) is None                                    # the caller treats it as a miss and refetches
    fake.get_object = real_get
    c._disabled_until = 0.0
    assert c.put(K, _doc(v=2), closed=False) is False
    assert c.get(K)["v"] == 1


def test_wrong_cell_or_unreadable_object_is_overwritten():
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history")
    fake.buckets.add("weather-history")
    fake.objects[("weather-history", K)] = b"garbage"
    fake.meta[("weather-history", K)] = {"closed": "1", "cell": "40.00,3.00"}      # closed but another cell
    assert c.put(K, _doc(v=2), closed=True) is True
    fake.meta[("weather-history", "u")] = {}                                       # no flag at all
    fake.objects[("weather-history", "u")] = b"x"
    assert c.put("u", _doc(v=3), closed=False) is True


def test_breaker_disables_the_cache_for_60s_with_one_warning(caplog):
    now = [1000.0]
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history", clock=lambda: now[0])
    fake.put_error = RuntimeError("down")
    with caplog.at_level(logging.WARNING):
        assert c.put("a", _doc(), closed=True) is False
        assert c.enabled is False
        n_calls = len(fake.puts)
        assert c.get("a") is None and c.put("b", _doc(), closed=True) is False
        assert c.put("c", _doc(), closed=True) is False
    assert sum("circuit" in r.getMessage() or "disabled" in r.getMessage() for r in caplog.records) == 1
    fake.put_error = None
    assert len(fake.puts) == n_calls                           # nothing reached MinIO while open
    now[0] += 59
    assert c.enabled is False
    now[0] += 2
    assert c.enabled is True and c.put("d", _doc(), closed=True) is True


def test_breaker_trips_on_read_errors_but_not_on_missing_keys():
    now = [0.0]

    class Boom(FakeMinio):
        def get_object(self, b, n):
            raise RuntimeError("down")
    c = hc.HistoryCache(FakeMinio(), "weather-history", clock=lambda: now[0])
    assert c.get("missing") is None and c.enabled is True
    c2 = hc.HistoryCache(Boom(), "weather-history", clock=lambda: now[0])
    assert c2.get("x") is None and c2.enabled is False


def test_last_put_ok_tracks_the_latest_put():
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history", clock=lambda: 0.0)
    assert c.last_put_ok is None
    c.put("a", _doc(), closed=True)
    assert c.last_put_ok is True
    c.put("a", _doc(), closed=True)                            # already stored closed: still in the cache
    assert c.last_put_ok is True
    fake.put_error = RuntimeError("x")
    c.put("b", _doc(), closed=True)
    assert c.last_put_ok is False


def test_minio_client_gets_short_timeouts_and_no_retry(monkeypatch):
    import minio, urllib3
    seen = {}

    class FakeClient:
        def __init__(self, *a, **k):
            seen.update(k)
    monkeypatch.setattr(minio, "Minio", FakeClient)
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "a")
    monkeypatch.setenv("MINIO_SECRET_KEY", "b")
    hc.reset_cache()
    hc.get_cache()
    hc.reset_cache()
    pm = seen["http_client"]
    assert isinstance(pm, urllib3.PoolManager)
    t = pm.connection_pool_kw["timeout"]
    assert t.connect_timeout == 2 and t.read_timeout == 5
    r = pm.connection_pool_kw["retries"]
    assert r.total == 0 and r.backoff_factor == 0


def test_request_ok_counts_a_cache_hit_without_any_put():
    # A healthy cache that only served gets in this request still lets a retry find the work.
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history", clock=lambda: 0.0)
    c.put("a", _doc(), closed=True)
    c.begin_request()
    assert c.request_ok() is False
    assert c.get("a") is not None
    assert c.request_ok() is True


def test_request_ok_is_per_request_and_false_after_failed_put():
    fake = FakeMinio()
    c = hc.HistoryCache(fake, "weather-history", clock=lambda: 0.0)
    c.begin_request()
    c.put("a", _doc(), closed=True)
    assert c.request_ok() is True
    c.begin_request()
    assert c.request_ok() is False
    fake.put_error = RuntimeError("x")
    c.put("b", _doc(), closed=True)
    assert c.request_ok() is False


def test_make_bucket_race_does_not_trip_the_breaker():
    class Raced(FakeMinio):
        def make_bucket(self, b):
            e = Exception("exists")
            e.code = "BucketAlreadyOwnedByYou"
            self.buckets.add(b)
            raise e
    c = hc.HistoryCache(Raced(), "weather-history", clock=lambda: 0.0)
    assert c.put("a", _doc(), closed=True) is True
    assert c.enabled is True
