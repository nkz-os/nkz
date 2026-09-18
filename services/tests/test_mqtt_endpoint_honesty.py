"""A device must not be handed an endpoint that does not exist.

The broker is reachable from outside the cluster only if an installation
publishes it. Answering with an empty host and a plausible port reads as an
endpoint, is not one, and leaves the device failing on its own.

Imports only the rule, not the service around it: a pure decision over two
settings should not need a database driver to be tested.
"""

import importlib.util
import pathlib

import pytest

_SRC = (
    pathlib.Path(__file__).resolve().parents[1]
    / "sdm-integration" / "mqtt_endpoint.py"
)
_spec = importlib.util.spec_from_file_location("mqtt_endpoint", _SRC)
mqtt_endpoint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mqtt_endpoint)


def test_an_unconfigured_endpoint_says_so():
    ep = mqtt_endpoint.endpoint_for_devices("", 8883)
    assert ep["configured"] is False
    assert "host" not in ep, "an absent endpoint must not look like one"
    assert "MQTT_EXTERNAL_HOST" in ep["reason"], "name the setting that is missing"


def test_a_configured_endpoint_is_returned():
    assert mqtt_endpoint.endpoint_for_devices("mqtt.example.com", 8883) == {
        "configured": True,
        "host": "mqtt.example.com",
        "port": 8883,
        "protocol": "mqtts",
    }


def test_a_plain_port_is_not_announced_as_tls():
    ep = mqtt_endpoint.endpoint_for_devices("mqtt.example.com", 1883)
    assert ep["protocol"] == "mqtt", "a plain port must not claim TLS"


@pytest.mark.parametrize("host", ["", None])
def test_any_empty_host_is_refused(host):
    assert mqtt_endpoint.endpoint_for_devices(host, 8883)["configured"] is False


def test_the_rule_needs_nothing_installed():
    """It is imported here on its own; a stray dependency would break that."""
    src = _SRC.read_text()
    imports = [
        ln for ln in src.splitlines()
        if ln.startswith(("import ", "from ")) and "__future__" not in ln
    ]
    assert imports == [], f"the rule must stay dependency-free: {imports}"


def test_the_service_binds_the_rule_to_its_configuration():
    """Read from source: the caller must not reimplement the decision."""
    service = (_SRC.parent / "sdm_api.py").read_text()
    assert "from mqtt_endpoint import endpoint_for_devices" in service
    assert "return endpoint_for_devices(MQTT_HOST, MQTT_PORT)" in service


@pytest.mark.parametrize("raw,expected", [(None, 8883), ("", 8883), ("   ", 8883), ("1883", 1883)])
def test_parse_port_falls_back_to_default_when_blank(raw, expected):
    assert mqtt_endpoint.parse_port(raw) == expected


def test_parse_port_rejects_garbage():
    with pytest.raises(ValueError):
        mqtt_endpoint.parse_port("not-a-port")


def test_the_service_parses_the_port_defensively():
    """Blank must not crash the service at import; the footgun is gone for good."""
    service = (_SRC.parent / "sdm_api.py").read_text()
    assert "MQTT_PORT = parse_port(" in service
    assert "int(os.getenv('MQTT_EXTERNAL_PORT'" not in service
