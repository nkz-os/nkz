"""MinIO cache of blended hourly years (gzip JSON). Best effort: errors degrade to a miss."""
import gzip
import io
import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = "weather-history"
BREAKER_S = 60
_NOT_FOUND = ("NoSuchKey", "NoSuchObject", "NoSuchBucket")


def cache_key(cell_lat: float, cell_lon: float, elev: float, year: int, coastal: bool = False) -> str:
    """No tenant or parcel in the key: any parcel with the same corrected cell, elevation and
    treatment shares it. Coastal parcels (corrected cell != grid cell) hold different data in the
    same cell, so they get their own suffix."""
    tag = "_c" if coastal else ""
    return f"weather-history/era5/v1/{cell_lat:.2f}_{cell_lon:.2f}_{elev:.0f}{tag}/{year}.json.gz"


def _cell_tag(doc: dict) -> str:
    try:
        return f"{float(doc['cell_lat']):.2f},{float(doc['cell_lon']):.2f}"
    except (KeyError, TypeError, ValueError):
        return ""


def _stored_meta(stat, name: str) -> Optional[str]:
    md = getattr(stat, "metadata", None) or {}
    for k, v in md.items():
        if str(k).lower() in (f"x-amz-meta-{name}", name):
            return str(v)
    return None


class HistoryCache:
    """Best-effort cache. Any MinIO error opens a circuit breaker: no calls for BREAKER_S."""

    def __init__(self, client=None, bucket: str = DEFAULT_BUCKET, clock=time.monotonic):
        self._client = client
        self.bucket = bucket
        self._bucket_ready = False
        self._clock = clock
        self._disabled_until = 0.0
        self.last_put_ok: Optional[bool] = None

    @property
    def enabled(self) -> bool:
        return self._client is not None and self._clock() >= self._disabled_until

    def _trip(self, what: str, exc: Exception) -> None:
        was_open = self._clock() < self._disabled_until
        self._disabled_until = self._clock() + BREAKER_S
        if not was_open:
            logger.warning("history cache %s failed (%s): cache disabled for %ds (circuit breaker)",
                           what, type(exc).__name__, BREAKER_S)

    def _ensure_bucket(self) -> None:
        if not self._bucket_ready:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)
            self._bucket_ready = True

    def get(self, key: str) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            resp = self._client.get_object(self.bucket, key)
            try:
                return json.loads(gzip.decompress(resp.read()))
            finally:
                resp.close()
                resp.release_conn()
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "code", None) not in _NOT_FOUND:
                self._trip("read", exc)
            return None

    def put(self, key: str, doc: dict, closed: bool) -> bool:
        """Store a year. The stored closed flag decides: closed is never overwritten (or downgraded);
        an open object may be replaced; a missing, unreadable or other-cell object is replaced.
        When the stored flag cannot be read the object is left alone. True = the object is in the cache."""
        if not self.enabled:
            self.last_put_ok = False
            return False
        try:
            self._ensure_bucket()
            tag = _cell_tag(doc)
            try:
                st = self._client.stat_object(self.bucket, key)
            except Exception as exc:  # noqa: BLE001
                if getattr(exc, "code", None) not in _NOT_FOUND:
                    raise
            else:
                if _stored_meta(st, "closed") == "1" and _stored_meta(st, "cell") == tag:
                    self.last_put_ok = True
                    return False
            stored = dict(doc)
            stored["closed"] = bool(closed)
            payload = gzip.compress(json.dumps(stored, separators=(",", ":")).encode("utf-8"))
            self._client.put_object(
                self.bucket, key, io.BytesIO(payload), len(payload), content_type="application/gzip",
                metadata={"closed": "1" if closed else "0", "cell": tag},
            )
            self.last_put_ok = True
            return True
        except Exception as exc:  # noqa: BLE001
            self.last_put_ok = False
            self._trip("write", exc)
            return False


_cache: Optional[HistoryCache] = None


def get_cache() -> HistoryCache:
    """Process-wide cache from the environment; disabled (one warning) when it is incomplete."""
    global _cache
    if _cache is not None:
        return _cache
    endpoint = os.getenv("MINIO_ENDPOINT", "").strip()
    access = os.getenv("MINIO_ACCESS_KEY", "").strip()
    secret = os.getenv("MINIO_SECRET_KEY", "").strip()
    bucket = os.getenv("HISTORY_BUCKET", "").strip() or DEFAULT_BUCKET
    if not (endpoint and access and secret):
        logger.warning("weather history cache disabled: MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY not all set")
        _cache = HistoryCache(None, bucket)
        return _cache
    import urllib3
    import minio

    secure = os.getenv("MINIO_SECURE", "").strip().lower() == "true"
    http_client = urllib3.PoolManager(
        timeout=urllib3.Timeout(connect=2, read=10),
        retries=urllib3.Retry(total=1, backoff_factor=0),
    )
    _cache = HistoryCache(minio.Minio(endpoint, access, secret, secure=secure, http_client=http_client), bucket)
    return _cache


def reset_cache() -> None:
    global _cache
    _cache = None
