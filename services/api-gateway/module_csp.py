"""CSP-of-data — validate that a module's gateway requests stay within the
data types it declared in its manifest (`data.entities`, `data.timeseries`).

Loaded by `fiware_api_gateway.py` and called from a `before_request` hook.

Policy:
  * If the request has no `X-Module-Id` header → skip (host or unscoped call).
  * If the manifest cannot be fetched (network error / 404) → fail OPEN with
    a warning log. Legacy modules that haven't published a manifest yet must
    keep working.
  * If `manifest.data.entities` is undeclared (no `data` key, or `entities`
    absent) → fail OPEN. The module simply hasn't opted into enforcement.
  * If `manifest.data.entities` contains the literal '*' → fail OPEN (wildcard).
  * Otherwise: the requested entity type must be in the list.

Manifests live on MinIO at `modules/<module-id>/manifest.json` in the
`nekazari-frontend` bucket (served publicly by the frontend ingress).
"""

import os
import json
import time
import logging
from typing import Optional, Set, Tuple
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

MANIFEST_BASE_URL = os.getenv(
    "MODULE_MANIFEST_BASE_URL",
    "http://frontend-static-service:80/modules",
)
MANIFEST_TTL_SECONDS = int(os.getenv("MODULE_MANIFEST_TTL_SECONDS", "60"))

# In-process cache: module_id -> (fetched_at, parsed_dict_or_None)
# None means we tried and the manifest is unavailable; we still cache the
# negative for TTL seconds to avoid hammering MinIO.
_cache: dict = {}


def _now() -> float:
    return time.time()


def _fetch_manifest_remote(module_id: str) -> Optional[dict]:
    url = f"{MANIFEST_BASE_URL}/{module_id}/manifest.json"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        ValueError,
        TimeoutError,
    ) as exc:
        logger.warning("manifest fetch failed for module=%s: %s", module_id, exc)
        return None


def get_manifest(module_id: str) -> Optional[dict]:
    """Return the cached manifest dict, refetching after TTL. None if unavailable."""
    entry = _cache.get(module_id)
    now = _now()
    if entry and (now - entry[0]) < MANIFEST_TTL_SECONDS:
        return entry[1]
    manifest = _fetch_manifest_remote(module_id)
    _cache[module_id] = (now, manifest)
    return manifest


def _allowlist(manifest: Optional[dict], key: str) -> Optional[Set[str]]:
    """Return None if no allowlist (fail-open), set() of allowed values otherwise.

    A literal '*' inside the list is treated as wildcard: returns None.
    """
    if not manifest:
        return None
    data = manifest.get("data")
    if not isinstance(data, dict):
        return None
    values = data.get(key)
    if not isinstance(values, list):
        return None
    if "*" in values:
        return None
    return {str(v) for v in values}


def is_entity_type_allowed(module_id: str, entity_type: str) -> Tuple[bool, str]:
    """Return (allowed, reason). Fail-open on missing manifest / wildcard / no declaration."""
    manifest = get_manifest(module_id)
    allowed = _allowlist(manifest, "entities")
    if allowed is None:
        return True, "fail-open"
    if entity_type in allowed:
        return True, "in allowlist"
    return (
        False,
        f"entity type {entity_type!r} not in module {module_id!r} data.entities",
    )


def is_timeseries_allowed(module_id: str, hypertable: str) -> Tuple[bool, str]:
    manifest = get_manifest(module_id)
    allowed = _allowlist(manifest, "timeseries")
    if allowed is None:
        return True, "fail-open"
    if hypertable in allowed:
        return True, "in allowlist"
    return (
        False,
        f"timeseries {hypertable!r} not in module {module_id!r} data.timeseries",
    )


def reset_cache_for_tests() -> None:
    """Clear the in-process cache. Test-only helper."""
    _cache.clear()
