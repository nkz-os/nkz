"""MinIO cache of blended hourly years (gzip JSON). Best effort: errors degrade to a miss."""
import gzip
import io
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = "weather-history"
_NOT_FOUND = ("NoSuchKey", "NoSuchObject", "NoSuchBucket")


def cache_key(cell_lat: float, cell_lon: float, elev: float, year: int) -> str:
    """No tenant or parcel in the key: any parcel in the same cell and elevation shares it."""
    return f"weather-history/era5/v1/{cell_lat:.2f}_{cell_lon:.2f}_{elev:.0f}/{year}.json.gz"


class HistoryCache:
    def __init__(self, client=None, bucket: str = DEFAULT_BUCKET):
        self._client = client
        self.bucket = bucket
        self._bucket_ready = False

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def _ensure_bucket(self) -> None:
        if not self._bucket_ready:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)
            self._bucket_ready = True

    def _exists(self, key: str) -> bool:
        try:
            self._client.stat_object(self.bucket, key)
            return True
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "code", None) in _NOT_FOUND:
                return False
            raise

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
                logger.warning("history cache read failed: %s", exc)
            return None

    def put(self, key: str, doc: dict, closed: bool) -> bool:
        """Store a year. A closed year is written once and never overwritten."""
        if not self.enabled:
            return False
        try:
            self._ensure_bucket()
            if closed and self._exists(key):
                return False
            payload = gzip.compress(json.dumps(doc, separators=(",", ":")).encode("utf-8"))
            self._client.put_object(
                self.bucket, key, io.BytesIO(payload), len(payload), content_type="application/gzip"
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("history cache write failed: %s", exc)
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
    from minio import Minio

    secure = os.getenv("MINIO_SECURE", "").strip().lower() == "true"
    _cache = HistoryCache(Minio(endpoint, access, secret, secure=secure), bucket)
    return _cache


def reset_cache() -> None:
    global _cache
    _cache = None
