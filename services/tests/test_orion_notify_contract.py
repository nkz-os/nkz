"""Contract: every Orion-LD notification endpoint must answer 204 with no body.

Orion-LD validates a notification response by looking for the literal string
``Content-Length:`` with a **case-sensitive** ``strstr`` and only skips that check
when the status is exactly 204::

    char* contentLenP = strstr(headers, "Content-Length:");
    if (contentLenP == NULL) { if (httpStatus != 204) -> notificationFailure(...) }

uvicorn/h11 emits response headers lower-cased (``content-length:``), which is legal
per RFC 7230 but invisible to that ``strstr``. So any FastAPI endpoint that answers a
notification with 200 + body is counted as a failure, and Orion auto-deactivates the
subscription after 3 consecutive failures.

That is what silently froze the weather timeseries on 2026-08-31: the
``WeatherObserved -> telemetry-worker`` subscriptions of both live tenants were paused
while the worker was in fact processing every notification correctly.

Flask/gunicorn capitalises the header, which is why entity-manager's notify endpoints
were never affected — the bug only bites the ASGI services.

This test exercises the real endpoints through TestClient rather than grepping for the
decorator, so a handler that returns a body despite declaring 204 still fails.
"""

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_SERVICES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for _sub in ("telemetry-worker", "risk-worker", "common"):
    _p = os.path.join(_SERVICES_DIR, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


# An Orion-LD notification body with no entities: every handler short-circuits on
# this, so no database or broker is touched.
EMPTY_NOTIFICATION = {
    "id": "urn:ngsi-ld:Notification:contract-test",
    "type": "Notification",
    "subscriptionId": "urn:ngsi-ld:subscription:contract-test",
    "notifiedAt": "2026-09-01T00:00:00Z",
    "data": [],
}

NOTIFY_HEADERS = {
    "NGSILD-Tenant": "contract-test",
    "Fiware-Service": "contract-test",
    "Content-Type": "application/json",
}


def _client_for(module_name, monkeypatch):
    """Mount a service's notification router on a bare app and return a TestClient."""
    if module_name == "telemetry-worker":
        from telemetry_worker import notification_handler as handler

        # The handler defers work to a BackgroundTask; TestClient runs those inline
        # after the response, so stub it out to keep this a pure HTTP contract test.
        async def _noop(*_args, **_kwargs):
            return None

        monkeypatch.setattr(handler, "process_notification_task", _noop)
    elif module_name == "risk-worker":
        import notification_handler as handler
    else:  # pragma: no cover - guarded by the parametrisation below
        raise AssertionError(f"unknown service {module_name}")

    app = FastAPI()
    app.include_router(handler.router)
    return TestClient(app)


@pytest.mark.parametrize(
    "service,path",
    [
        ("telemetry-worker", "/notify"),
        ("telemetry-worker", "/v2/notify"),
        ("risk-worker", "/notify"),
    ],
)
def test_notification_endpoint_answers_204_without_body(service, path, monkeypatch):
    client = _client_for(service, monkeypatch)

    response = client.post(path, json=EMPTY_NOTIFICATION, headers=NOTIFY_HEADERS)

    assert response.status_code == 204, (
        f"{service} {path} answered {response.status_code}; Orion-LD counts anything "
        "other than 204 as a notification failure unless the response carries a "
        "capitalised 'Content-Length:' header, which uvicorn never emits. "
        "Three consecutive failures deactivate the subscription."
    )
    assert response.content == b"", (
        f"{service} {path} returned a body with 204: {response.content!r}. "
        "A body forces a content-length header and defeats the purpose."
    )


def test_malformed_notification_is_rejected_not_acknowledged(monkeypatch):
    """A body Orion cannot have produced must fail loudly, never be ACKed as 204.

    The original handler did ``return {"error": ...}, 400`` — in FastAPI that
    serialises the *tuple* and answers **200**, so a malformed notification was
    acknowledged as success and dropped silently.
    """
    client = _client_for("telemetry-worker", monkeypatch)

    response = client.post(
        "/notify",
        content=b"{ this is not json",
        headers=NOTIFY_HEADERS,
    )

    assert response.status_code == 400, (
        f"malformed notification answered {response.status_code}; it must be a 4xx so "
        "the failure is visible instead of being silently acknowledged."
    )
