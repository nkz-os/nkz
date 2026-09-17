"""A device must not be handed an endpoint that does not exist.

The broker is only reachable from outside the cluster if an installation
chooses to publish it. Until then, provisioning used to answer with an empty
host and port 8883 — which reads as an endpoint, is not one, and leaves the
device failing on its own with nothing to point at.
"""

import importlib.util
import pathlib
import sys

_SERVICE_DIR = pathlib.Path(__file__).resolve().parents[1] / "sdm-integration"
_SRC = _SERVICE_DIR / "sdm_api.py"

# The service runs from its own directory and imports siblings by bare name.
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))


def _load(monkeypatch, host, port="8883"):
    """Import the module with only the settings this behaviour depends on."""
    for k, v in (
        ("MONGODB_URL", "mongodb://localhost:27017/test"),
        ("ORION_URL", "http://orion-test:1026"),
        ("MQTT_EXTERNAL_HOST", host),
        ("MQTT_EXTERNAL_PORT", port),
    ):
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("sdm_api_probe", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_an_unconfigured_endpoint_says_so(monkeypatch):
    mod = _load(monkeypatch, host="")
    ep = mod.mqtt_endpoint_for_devices()
    assert ep["configured"] is False
    assert "host" not in ep, "an absent endpoint must not look like one"
    assert "MQTT_EXTERNAL_HOST" in ep["reason"], "say which setting is missing"


def test_a_configured_endpoint_is_returned(monkeypatch):
    mod = _load(monkeypatch, host="mqtt.example.com")
    ep = mod.mqtt_endpoint_for_devices()
    assert ep == {
        "configured": True,
        "host": "mqtt.example.com",
        "port": 8883,
        "protocol": "mqtts",
    }


def test_a_plain_port_is_not_announced_as_tls(monkeypatch):
    mod = _load(monkeypatch, host="mqtt.example.com", port="1883")
    assert mod.mqtt_endpoint_for_devices()["protocol"] == "mqtt"


def test_no_default_names_a_deployment(monkeypatch):
    """A default host would be someone else's broker."""
    monkeypatch.delenv("MQTT_EXTERNAL_HOST", raising=False)
    monkeypatch.setenv("MONGODB_URL", "mongodb://localhost:27017/test")
    monkeypatch.setenv("ORION_URL", "http://orion-test:1026")
    spec = importlib.util.spec_from_file_location("sdm_api_probe2", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.MQTT_HOST == ""
