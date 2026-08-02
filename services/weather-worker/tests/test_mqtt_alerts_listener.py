"""Tests for MqttWarningsListener (MQTT WIS 2.0 subscriber).

Mocks paho.mqtt.client.Client to verify TLS, auth, topic subscription,
message routing, and error resilience without connecting to a real broker.
"""

import json
import os
import socket
import sys
from unittest.mock import MagicMock, patch

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

import pytest

from weather_worker.mqtt_alerts import MqttWarningsListener


@pytest.fixture
def mock_cls():
    """Yield the patched paho Client *constructor* so we can inspect call_args."""
    with patch("weather_worker.mqtt_alerts.mqtt.Client") as cls:
        mock_client = MagicMock()
        cls.return_value = mock_client
        yield cls, mock_client


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_listener_default_client_id_uses_hostname(mock_cls):
    cls, client = mock_cls
    MqttWarningsListener(
        host="test.local", port=8883, topic="test/topic", api_key="skey",
        on_notification=lambda n: True,
    )
    kwargs = cls.call_args.kwargs
    assert kwargs.get("clean_session") is True
    cid = kwargs.get("client_id", "")
    assert socket.gethostname() in cid
    assert "nekazari-weather-worker" in cid


def test_listener_custom_client_id(mock_cls):
    cls, client = mock_cls
    MqttWarningsListener(
        host="test.local", port=1883, topic="t/x", api_key="sk",
        on_notification=lambda n: True, client_id="custom-id",
    )
    assert cls.call_args.kwargs["client_id"] == "custom-id"


def test_listener_sets_auth(mock_cls):
    cls, client = mock_cls
    MqttWarningsListener(
        host="h", port=8883, topic="t", api_key="my-key",
        on_notification=lambda n: True,
    )
    client.username_pw_set.assert_called_once_with("apikey", "my-key")


def test_listener_sets_tls(mock_cls):
    cls, client = mock_cls
    MqttWarningsListener(
        host="h", port=8883, topic="t", api_key="k",
        on_notification=lambda n: True,
    )
    client.tls_set.assert_called_once()
    import ssl
    assert client.tls_set.call_args.kwargs.get("cert_reqs") == ssl.CERT_REQUIRED


# ---------------------------------------------------------------------------
# on_connect
# ---------------------------------------------------------------------------


def test_on_connect_success_subscribes(mock_cls):
    cls, client = mock_cls
    lst = MqttWarningsListener(
        host="h", port=8883, topic="my/topic", api_key="k",
        on_notification=lambda n: True,
    )
    # rc=0 → success; use a simple object with .value attribute
    rc = MagicMock()
    rc.value = 0
    lst._on_connect(client, None, None, rc, None)
    client.subscribe.assert_called_once_with("my/topic", qos=0)


def test_on_connect_failure_does_not_subscribe(mock_cls):
    cls, client = mock_cls
    lst = MqttWarningsListener(
        host="h", port=8883, topic="t", api_key="k",
        on_notification=lambda n: True,
    )
    rc = MagicMock()
    rc.value = 134  # Bad User Name or Password
    lst._on_connect(client, None, None, rc, None)
    client.subscribe.assert_not_called()


# ---------------------------------------------------------------------------
# on_message
# ---------------------------------------------------------------------------


def _make_msg(payload_bytes):
    msg = MagicMock()
    msg.payload = payload_bytes
    msg.topic = "origin/a/wis2/data"
    return msg


def test_on_message_routes_to_callback(mock_cls):
    cls, client = mock_cls
    call_args = []
    lst = MqttWarningsListener(
        host="h", port=8883, topic="t", api_key="k",
        on_notification=lambda n: call_args.append(n),
    )
    msg = _make_msg(b'{"id":"abc","properties":{"alertId":"x","hubTime":"2026"}}')
    lst._on_message(client, None, msg)
    assert len(call_args) == 1
    assert call_args[0]["id"] == "abc"


def test_on_message_malformed_json_does_not_raise(mock_cls):
    cls, client = mock_cls
    call_args = []
    lst = MqttWarningsListener(
        host="h", port=8883, topic="t", api_key="k",
        on_notification=lambda n: call_args.append(n),
    )
    msg = _make_msg(b"not json")
    # Must NOT raise
    lst._on_message(client, None, msg)
    assert len(call_args) == 0  # callback never called


def test_on_message_exception_in_callback_does_not_raise(mock_cls):
    cls, client = mock_cls
    def raiser(n):
        raise RuntimeError("oops")

    lst = MqttWarningsListener(
        host="h", port=8883, topic="t", api_key="k",
        on_notification=raiser,
    )
    msg = _make_msg(b'{"id":"z"}')
    # Must NOT propagate exception
    lst._on_message(client, None, msg)


def test_on_message_empty_payload_does_not_raise(mock_cls):
    cls, client = mock_cls
    call_args = []
    lst = MqttWarningsListener(
        host="h", port=8883, topic="t", api_key="k",
        on_notification=lambda n: call_args.append(n),
    )
    msg = _make_msg(b"")
    lst._on_message(client, None, msg)
    assert len(call_args) == 0


# ---------------------------------------------------------------------------
# on_disconnect
# ---------------------------------------------------------------------------


def test_on_disconnect_logs_warning(mock_cls):
    cls, client = mock_cls
    lst = MqttWarningsListener(
        host="h", port=8883, topic="t", api_key="k",
        on_notification=lambda n: True,
    )
    rc = MagicMock()
    rc.value = 1
    flags = MagicMock()
    lst._on_disconnect(client, None, flags, rc, None)


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


def test_start_connects_and_loops(mock_cls):
    cls, client = mock_cls
    lst = MqttWarningsListener(
        host="broker.local", port=8883, topic="t/x", api_key="k",
        on_notification=lambda n: True,
    )
    client.reconnect_delay_set.assert_called_once()
    call_kwargs = client.reconnect_delay_set.call_args.kwargs
    assert call_kwargs.get("min_delay") == 5
    assert call_kwargs.get("max_delay") == 300

    lst.start()
    client.connect_async.assert_called_once_with("broker.local", 8883, keepalive=30)
    client.connect.assert_not_called()
    client.loop_start.assert_called_once()


def test_start_failure_does_not_raise(mock_cls):
    cls, client = mock_cls
    client.connect_async.side_effect = OSError("dns boom")
    lst = MqttWarningsListener(
        host="broker.local", port=8883, topic="t/x", api_key="k",
        on_notification=lambda n: True,
    )
    lst.start()  # must not raise — the caller thread (hourly loop) survives
    client.loop_start.assert_not_called()


def test_stop_disconnects(mock_cls):
    cls, client = mock_cls
    lst = MqttWarningsListener(
        host="h", port=8883, topic="t", api_key="k",
        on_notification=lambda n: True,
    )
    lst.stop()
    client.loop_stop.assert_called_once()
    client.disconnect.assert_called_once()
