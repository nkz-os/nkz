"""
OrionClient — typed NGSI-LD client with automatic FIWARE header injection.

Rules enforced at library level (impossible to forget):
- Every request sends NGSILD-Tenant AND Fiware-Service headers
- Content-Type: application/ld+json with @context in body
- Or Content-Type: application/json with Link header
"""

import os
from typing import Any
from urllib.parse import urljoin

import httpx

CONTEXT_URL = os.getenv(
    "CONTEXT_URL",
    "http://api-gateway-service:5000/ngsi-ld-context.json",
)


class OrionClient:
    """NGSI-LD client scoped to a single tenant."""

    def __init__(
        self,
        tenant_id: str,
        base_url: str | None = None,
        context_url: str | None = None,
    ):
        self.tenant_id = tenant_id
        self.base_url = base_url or os.getenv(
            "ORION_LD_URL", "http://orion-ld-service:1026"
        )
        self.context_url = context_url or CONTEXT_URL
        self._client = httpx.AsyncClient(timeout=30.0)

    def _headers(self, content_type: str = "application/ld+json") -> dict[str, str]:
        headers = {
            "NGSILD-Tenant": self.tenant_id,
            "Fiware-Service": self.tenant_id,
            "Fiware-ServicePath": "/",
        }
        if content_type == "application/ld+json":
            headers["Content-Type"] = "application/ld+json"
        elif content_type == "application/json":
            headers["Content-Type"] = "application/json"
            headers["Link"] = (
                f'<{self.context_url}>; rel="http://www.w3.org/ns/json-ld#context";'
                ' type="application/ld+json"'
            )
        return headers

    def _url(self, path: str) -> str:
        return urljoin(f"{self.base_url}/", path.lstrip("/"))

    async def query_entities(
        self,
        type: str | None = None,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
        attrs: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if type:
            params["type"] = type
        if q:
            params["q"] = q
        if attrs:
            params["attrs"] = attrs
        resp = await self._client.get(
            self._url("/ngsi-ld/v1/entities"),
            params=params,
            headers=self._headers("application/json"),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_entity(self, entity_id: str) -> dict[str, Any]:
        resp = await self._client.get(
            self._url(f"/ngsi-ld/v1/entities/{entity_id}"),
            headers=self._headers("application/json"),
        )
        resp.raise_for_status()
        return resp.json()

    async def create_entity(self, entity: dict[str, Any]) -> dict[str, Any]:
        if "@context" not in entity:
            entity = {"@context": [self.context_url], **entity}
        resp = await self._client.post(
            self._url("/ngsi-ld/v1/entities"),
            json=entity,
            headers=self._headers("application/ld+json"),
        )
        resp.raise_for_status()
        location = resp.headers.get("Location", "")
        return {"id": location.split("/")[-1] if location else "", "status": "created"}

    async def update_entity_attrs(self, entity_id: str, attrs: dict[str, Any]) -> None:
        resp = await self._client.patch(
            self._url(f"/ngsi-ld/v1/entities/{entity_id}/attrs"),
            json=attrs,
            headers=self._headers("application/ld+json"),
        )
        resp.raise_for_status()

    async def delete_entity(self, entity_id: str) -> None:
        resp = await self._client.delete(
            self._url(f"/ngsi-ld/v1/entities/{entity_id}"),
            headers=self._headers(),
        )
        resp.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
