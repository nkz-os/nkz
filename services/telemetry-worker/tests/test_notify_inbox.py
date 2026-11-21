"""Receivers store notified entities in the inbox before answering 204."""

import os
import sys
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_TEST_DIR, _SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from telemetry_worker import notification_handler as nh

TENANT = "acme"


class FakeInbox:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def enqueue(self, tenant_id, entities, notified_at):
        if self.fail:
            raise RuntimeError("db down")
        self.calls.append((tenant_id, list(entities), notified_at))
        return len(entities)


def _entity(n):
    return {"id": f"urn:ngsi-ld:Device:acme:{n}", "type": "Device"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("NOTIFY_REQUIRE_INTERNAL_SECRET", raising=False)
    monkeypatch.setattr(nh, "_inbox", FakeInbox())
    app = FastAPI()
    app.include_router(nh.router)
    return TestClient(app)


def test_notify_stores_entities_and_answers_204(client):
    body = {"data": [_entity("a"), _entity("b")], "notifiedAt": "2026-11-21T10:00:00Z"}
    r = client.post("/notify", json=body, headers={"NGSILD-Tenant": TENANT})
    assert r.status_code == 204
    assert r.content == b""
    tenant, entities, notified_at = nh._inbox.calls[0]
    assert tenant == TENANT
    assert entities == body["data"]
    assert notified_at == datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)


def test_fiware_service_header_is_the_tenant(client):
    r = client.post("/notify", json={"data": [_entity("a")]}, headers={"Fiware-Service": TENANT})
    assert r.status_code == 204
    assert nh._inbox.calls[0][0] == TENANT
    assert nh._inbox.calls[0][2] is None


def test_unparseable_notified_at_is_none(client):
    client.post(
        "/notify", json={"data": [_entity("a")], "notifiedAt": "garbage"},
        headers={"NGSILD-Tenant": TENANT},
    )
    assert nh._inbox.calls[0][2] is None


def test_naive_notified_at_is_utc(client):
    client.post(
        "/notify", json={"data": [_entity("a")], "notifiedAt": "2026-11-21T10:00:00"},
        headers={"NGSILD-Tenant": TENANT},
    )
    assert nh._inbox.calls[0][2] == datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)


def test_inbox_failure_is_503(client, monkeypatch):
    monkeypatch.setattr(nh, "_inbox", FakeInbox(fail=True))
    r = client.post("/notify", json={"data": [_entity("a")]}, headers={"NGSILD-Tenant": TENANT})
    assert r.status_code == 503


def test_inbox_not_wired_is_503_and_nothing_processed(client, monkeypatch):
    monkeypatch.setattr(nh, "_inbox", None)
    r = client.post("/notify", json={"data": [_entity("a")]}, headers={"NGSILD-Tenant": TENANT})
    assert r.status_code == 503


def test_malformed_json_is_400(client):
    r = client.post(
        "/notify", content=b"{not json",
        headers={"NGSILD-Tenant": TENANT, "content-type": "application/json"},
    )
    assert r.status_code == 400
    assert nh._inbox.calls == []


def test_non_object_body_is_400(client):
    r = client.post("/notify", json=[1, 2], headers={"NGSILD-Tenant": TENANT})
    assert r.status_code == 400


def test_unauthenticated_is_401(client, monkeypatch):
    monkeypatch.setenv("NOTIFY_REQUIRE_INTERNAL_SECRET", "true")
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "s3cret")
    r = client.post("/notify", json={"data": [_entity("a")]}, headers={"NGSILD-Tenant": TENANT})
    assert r.status_code == 401
    assert nh._inbox.calls == []


@pytest.mark.parametrize("body", [{}, {"data": []}, {"data": None}])
def test_no_entities_is_204_nothing_enqueued(client, body):
    r = client.post("/notify", json=body, headers={"NGSILD-Tenant": TENANT})
    assert r.status_code == 204
    assert nh._inbox.calls == []


def test_missing_tenant_is_400(client):
    r = client.post("/notify", json={"data": [_entity("a")]})
    assert r.status_code == 400
    assert nh._inbox.calls == []


def test_entity_without_id_does_not_block_the_others(client):
    # The inbox drops the unstorable entity itself; the receiver passes the list through.
    data = [{"type": "Device"}, _entity("ok")]
    r = client.post("/notify", json={"data": data}, headers={"NGSILD-Tenant": TENANT})
    assert r.status_code == 204
    assert nh._inbox.calls[0][1] == data


def test_v2_notify_same_path(client):
    r = client.post(
        "/v2/notify", json={"data": [_entity("a")], "notifiedAt": "2026-11-21T10:00:00Z"},
        headers={"Fiware-Service": TENANT},
    )
    assert r.status_code == 204
    assert nh._inbox.calls[0][0] == TENANT
    assert len(nh._inbox.calls[0][1]) == 1


def test_v2_notify_failure_is_503(client, monkeypatch):
    monkeypatch.setattr(nh, "_inbox", FakeInbox(fail=True))
    r = client.post("/v2/notify", json={"data": [_entity("a")]}, headers={"Fiware-Service": TENANT})
    assert r.status_code == 503
