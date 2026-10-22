"""Where is a module installed? — the platform's answer, over the internal API.

Modules must not read ``tenant_installed_modules`` themselves: that takes a
database credential and ties them to the core schema. entity-manager answers
``GET /api/internal/modules/{module_id}/tenants`` with one state per tenant.

An error is never an empty answer: every failure raises
``InstalledTenantsUnavailable``, so a caller cannot mistake an outage for
"installed nowhere" and delete subscriptions on the strength of it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote

import httpx

ENTITY_MANAGER_URL_ENV = "ENTITY_MANAGER_URL"
DEFAULT_ENTITY_MANAGER_URL = "http://entity-manager-service:5000"
STATES = ("enabled", "disabled", "not_installed")


class InstalledTenantsUnavailable(Exception):
    """The platform could not say where the module is installed. Never means "nowhere"."""


@dataclass(frozen=True)
class TenantInstallations:
    module_id: str
    enabled: tuple[str, ...]
    disabled: tuple[str, ...]
    not_installed: tuple[str, ...]


def _parse(module_id: str, payload: object) -> TenantInstallations:
    if not isinstance(payload, dict):
        raise InstalledTenantsUnavailable(f"{module_id}: response is not an object")
    if payload.get("module_id") != module_id:
        raise InstalledTenantsUnavailable(
            f"{module_id}: response is for {payload.get('module_id')!r}"
        )
    tenants = payload.get("tenants")
    if not isinstance(tenants, dict):
        raise InstalledTenantsUnavailable(f"{module_id}: response has no 'tenants' map")
    by_state: dict[str, list[str]] = {s: [] for s in STATES}
    for tenant, state in tenants.items():
        if not isinstance(tenant, str) or not tenant or state not in by_state:
            raise InstalledTenantsUnavailable(
                f"{module_id}: unexpected entry {tenant!r}: {state!r}"
            )
        by_state[state].append(tenant)
    return TenantInstallations(module_id, *(tuple(sorted(by_state[s])) for s in STATES))


async def installed_tenants(
    module_id: str,
    *,
    base_url: str | None = None,
    internal_secret: str | None = None,
    timeout: float = 10.0,
) -> TenantInstallations:
    """Installation state of ``module_id`` in every tenant. Raises, never returns empty on error."""
    base = (
        base_url or os.getenv(ENTITY_MANAGER_URL_ENV) or DEFAULT_ENTITY_MANAGER_URL
    ).rstrip("/")
    secret = (
        internal_secret
        if internal_secret is not None
        else os.getenv("INTERNAL_SERVICE_SECRET", "")
    )
    if not secret:
        raise InstalledTenantsUnavailable(f"{module_id}: INTERNAL_SERVICE_SECRET is not set")
    url = f"{base}/api/internal/modules/{quote(module_id, safe='')}/tenants"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers={"X-Internal-Service-Secret": secret})
    except httpx.HTTPError as e:
        raise InstalledTenantsUnavailable(f"{module_id}: {type(e).__name__}: {e}") from e
    if resp.status_code != 200:
        raise InstalledTenantsUnavailable(
            f"{module_id}: entity-manager answered {resp.status_code}"
        )
    try:
        payload = resp.json()
    except ValueError as e:
        raise InstalledTenantsUnavailable(f"{module_id}: response is not JSON") from e
    return _parse(module_id, payload)
