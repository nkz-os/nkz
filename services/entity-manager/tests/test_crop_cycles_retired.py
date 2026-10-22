"""Crop cycles left the core: no read, notify or sweep route in entity-manager."""
from tests.test_module_publish import client  # noqa: F401  (harness + fixture)


def test_crop_cycle_routes_are_gone(client):  # noqa: F811
    h = {"X-Internal-Service-Secret": "test-secret"}
    assert client.get("/api/internal/parcels/p1/crop-cycles?tenant_id=t", headers=h).status_code == 404
    assert client.post("/api/internal/notify/crop-cycles", headers=h, json={}).status_code == 404
    assert client.post("/api/internal/crop-cycles/reconcile-all", headers=h).status_code == 404
