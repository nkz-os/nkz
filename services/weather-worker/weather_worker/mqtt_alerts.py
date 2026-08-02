"""MqttWarningsListener — MQTT WIS 2.0 subscriber for MeteoAlarm.

Connects to the MeteoAlarm MQTT broker (mqtt.meteoalarm.org:8883 TLS),
subscribes to the WMO WIS 2.0 warnings topic, and routes parsed
notifications to a callback (typically MeteoAlertsEngine.handle_notification).

Uses paho-mqtt v2.x (CallbackAPIVersion.VERSION2).
"""

import json
import logging
import socket
import ssl
from typing import Callable, Optional

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MqttWarningsListener:
    """Persistent MQTT client that pushes WIS2 notifications into an engine.

    **Connection:** TLS to ``host:port``, auth via ``apikey``/``api_key``,
    clean session (QoS 0 — no broker-side buffering while down). Reconnect
    is handled by paho (reconnect_delay 5–300 s, exponential back-off).

    **Message routing:** each incoming WIS2 notification is JSON-parsed and
    passed to ``on_notification(dict)``. Exceptions inside the callback are
    caught and logged — they will never escape into the paho thread.
    """

    def __init__(
        self,
        host: str,
        port: int,
        topic: str,
        api_key: str,
        on_notification: Callable[[dict], bool],
        *,
        client_id: Optional[str] = None,
        keepalive: int = 30,
    ):
        self._host = host
        self._port = port
        self._topic = topic
        self._on_notification = on_notification

        if not client_id:
            client_id = f"nekazari-weather-worker-{socket.gethostname()}"
        self._client_id = client_id
        self._keepalive = keepalive

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
        )
        self._client.username_pw_set("apikey", api_key)
        self._client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self._client.reconnect_delay_set(min_delay=5, max_delay=300)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Connect to the broker and start the paho network loop thread.

        Uses ``connect_async`` so an unreachable broker at startup does not
        raise here (and kill the caller thread) — paho retries from its own
        loop thread using the configured reconnect back-off.
        """
        logger.info(
            "MqttWarningsListener: connecting %s:%d topic=%s client=%s",
            self._host,
            self._port,
            self._topic,
            self._client_id,
        )
        try:
            self._client.connect_async(self._host, self._port, keepalive=self._keepalive)
            self._client.loop_start()
        except Exception:
            logger.exception(
                "MqttWarningsListener: failed to start MQTT client — listener disabled"
            )

    def stop(self):
        """Disconnect and stop the paho loop thread."""
        logger.info("MqttWarningsListener: stopping client %s", self._client_id)
        self._client.loop_stop()
        self._client.disconnect()

    # ------------------------------------------------------------------
    # paho callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        rc = reason_code.value if hasattr(reason_code, "value") else reason_code
        if rc == 0:
            logger.info(
                "MqttWarningsListener: connected to %s:%d, subscribing %s",
                self._host,
                self._port,
                self._topic,
            )
            client.subscribe(self._topic, qos=0)
        else:
            logger.warning(
                "MqttWarningsListener: connect failed rc=%s — will retry", rc
            )

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload
            if not payload:
                return
            notification = json.loads(payload.decode("utf-8"))
            self._on_notification(notification)
        except json.JSONDecodeError:
            logger.warning(
                "MqttWarningsListener: non-JSON message on %s (%d bytes)",
                msg.topic,
                len(msg.payload),
            )
        except Exception:
            logger.exception(
                "MqttWarningsListener: unhandled error in notification callback"
            )

    def _on_disconnect(self, client, userdata, reason_code, properties=None):
        rc = reason_code.value if hasattr(reason_code, "value") else reason_code
        logger.warning("MqttWarningsListener: disconnected (rc=%s)", rc)
