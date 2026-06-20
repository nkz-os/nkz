"""services/common/orion_safe_query.py — Orion queries that never false-zero.

NEVER return 0 or [] on error. Return sentinel values so callers must handle errors explicitly.
This prevents the class of bug where an Orion query failure is interpreted as "no data exists"
(which has nearly caused mass deletions >= 3 times in this codebase).
"""

import os
import logging
from typing import List, Union
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# Default context URL — can be overridden via env var
CONTEXT_URL = os.environ.get(
    "CONTEXT_URL",
    "http://orion:1026/ngsi-ld-context.json"
)

# Sentinel for "query failed" — distinct from legitimate 0
QUERY_FAILED = -1


class OrionQueryError(Exception):
    """Raised when Orion returns an error that would produce a false zero."""
    pass


def safe_count_entities(
    orion_url: str,
    tenant_id: str,
    entity_type: str,
    *,
    raise_on_error: bool = False
) -> int:
    """
    Count entities of a given type in Orion, NEVER returning 0 on error.

    Returns:
        int >= 0: count if query succeeds (legitimate 0 = no entities found)
        QUERY_FAILED (-1): if query fails (caller MUST handle -1 explicitly)

    Raises:
        OrionQueryError: if raise_on_error=True and query fails
    """
    import requests

    headers = {
        "NGSILD-Tenant": tenant_id,
        "Accept": "application/json",
        "Link": f'<{CONTEXT_URL}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'
    }

    try:
        resp = requests.get(
            f"{orion_url}/ngsi-ld/v1/entities?type={entity_type}&options=count,keyValues",
            headers=headers,
            timeout=10
        )

        if resp.status_code == 200:
            count = resp.headers.get("X-Total-Count")
            if count is not None:
                try:
                    return int(count)
                except (ValueError, TypeError):
                    logger.warning(
                        "safe_count_entities: malformed X-Total-Count=%r for "
                        "tenant=%s type=%s — falling back to body length",
                        count, tenant_id, entity_type
                    )
            data = resp.json()
            return len(data) if isinstance(data, list) else 0

        elif resp.status_code == 404:
            logger.warning(
                "safe_count_entities: 404 for tenant=%s type=%s — returning 0 (legitimate)",
                tenant_id, entity_type
            )
            return 0

        else:
            msg = (
                f"safe_count_entities: tenant={tenant_id} type={entity_type} "
                f"status={resp.status_code} body={resp.text[:200]}"
            )
            if raise_on_error:
                raise OrionQueryError(msg)
            logger.error(msg)
            return QUERY_FAILED  # Sentinel: NOT 0

    except requests.RequestException as e:
        msg = f"safe_count_entities: connection error: {e}"
        if raise_on_error:
            raise OrionQueryError(msg) from e
        logger.error(msg)
        return QUERY_FAILED  # Sentinel: NOT 0


def safe_query_entities(
    orion_url: str,
    tenant_id: str,
    entity_type: str,
    *,
    limit: int = 100,
    offset: int = 0,
    raise_on_error: bool = False
) -> Union[List[dict], None]:
    """
    Query entities, NEVER returning empty list on error.

    Returns:
        list: entities if query succeeds (may be empty = legitimate no data)
        None: if query fails (caller MUST check for None)

    Raises:
        OrionQueryError: if raise_on_error=True and query fails
    """
    import requests

    headers = {
        "NGSILD-Tenant": tenant_id,
        "Accept": "application/json",
        "Link": f'<{CONTEXT_URL}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'
    }

    params = {"type": entity_type, "options": "keyValues", "limit": limit}
    if offset:
        params["offset"] = offset

    try:
        resp = requests.get(
            f"{orion_url}/ngsi-ld/v1/entities?{urlencode(params)}",
            headers=headers,
            timeout=10
        )

        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else []

        elif resp.status_code == 404:
            logger.warning(
                "safe_query_entities: 404 for tenant=%s type=%s — returning [] (legitimate)",
                tenant_id, entity_type
            )
            return []  # Legitimate empty

        else:
            msg = (
                f"safe_query_entities: tenant={tenant_id} type={entity_type} "
                f"status={resp.status_code}"
            )
            if raise_on_error:
                raise OrionQueryError(msg)
            logger.error(msg)
            return None  # Sentinel: NOT []

    except requests.RequestException as e:
        msg = f"safe_query_entities: connection error: {e}"
        if raise_on_error:
            raise OrionQueryError(msg) from e
        logger.error(msg)
        return None  # Sentinel: NOT []
