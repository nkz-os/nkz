"""/send/push forwards an optional, allow-listed Android channelId to Expo."""

import os
import sys

import pytest

_SVC_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app as push_app  # noqa: E402

TOKENS = ["ExponentPushToken[a]", "ExponentPushToken[b]"]


class _Cur:
    def execute(self, *_a, **_k):
        pass

    def fetchall(self):
        return [{"expo_token": t} for t in TOKENS]

    def close(self):
        pass


class _Db:
    def cursor(self):
        return _Cur()


class _Resp:
    ok = True

    def json(self):
        return {"data": []}


@pytest.fixture
def client(monkeypatch):
    sent = []
    monkeypatch.setattr(push_app, "INTERNAL_SECRET", "s3cret")
    monkeypatch.setattr(push_app, "get_db", lambda: _Db())

    def _post(url, json=None, timeout=None):
        sent.append(json)
        return _Resp()

    monkeypatch.setattr(push_app.requests, "post", _post)
    c = push_app.app.test_client()
    c.sent = sent
    return c


def _send(client, **extra):
    body = {"tenant_id": "acmefarm", "title": "t", "body": "b", "data": {"x": 1}}
    body.update(extra)
    return client.post("/send/push", json=body, headers={"X-Internal-Secret": "s3cret"})


@pytest.mark.parametrize("channel", ["vibrate", "silent", "crop-alerts", "risk-alerts", "system"])
def test_valid_channel_forwarded_in_every_message(client, channel):
    assert _send(client, channelId=channel).status_code == 200
    msgs = client.sent[0]
    assert len(msgs) == len(TOKENS)
    assert all(m["channelId"] == channel for m in msgs)


@pytest.mark.parametrize("channel", ["bogus", "", None, 5, ["vibrate"]])
def test_unknown_channel_omitted_and_200(client, channel):
    assert _send(client, channelId=channel).status_code == 200
    assert all("channelId" not in m for m in client.sent[0])


def test_missing_channel_unchanged(client):
    assert _send(client).status_code == 200
    assert client.sent[0] == [
        {"to": t, "title": "t", "body": "b", "data": {"x": 1}, "sound": "default", "priority": "high"}
        for t in TOKENS
    ]


def test_silent_channel_omits_sound_others_keep_it(client):
    assert _send(client, channelId="silent").status_code == 200
    assert all("sound" not in m for m in client.sent[0])
    assert _send(client, channelId="vibrate").status_code == 200
    assert all(m["sound"] == "default" for m in client.sent[1])
