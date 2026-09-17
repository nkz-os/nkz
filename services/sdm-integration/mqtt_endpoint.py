"""How a device is told to reach the MQTT broker.

Its own module, with no imports: the broker endpoint is a question of
configuration, and answering it should not require the database driver and the
web framework the rest of the service needs. That also lets the rule be tested
for what it is -- a pure decision over two settings.
"""

from __future__ import annotations

TLS_PORT = 8883


def endpoint_for_devices(host: str, port: int) -> dict:
    """The endpoint a device should use, or why there is none.

    The broker is reachable from outside the cluster only if the installation
    publishes it, and nothing here can assume that. Answering with an empty
    host and a plausible port looks like an endpoint and is not one: the device
    fails on its own, with nothing to point at.
    """
    if not host:
        return {
            "configured": False,
            "reason": (
                "No external MQTT endpoint is configured for this installation. "
                "Set MQTT_EXTERNAL_HOST once the broker is reachable from "
                "outside the cluster; until then devices cannot connect."
            ),
        }
    return {
        "configured": True,
        "host": host,
        "port": port,
        "protocol": "mqtts" if port == TLS_PORT else "mqtt",
    }
