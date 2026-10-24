"""FIWARE IoT Agent (JSON) provisioning helper shared by services.

One service group per tenant: its apikey is the first segment of every MQTT
topic of the tenant (/<apikey>/<device_id>/attrs). Callers pass the IoT Agent
URL and the already-built FIWARE headers, so this module holds no
configuration and no auth dependency.
"""

import logging
import os
import secrets
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class IotAgentError(Exception):
    """The IoT Agent answered with a non-2xx status."""

    def __init__(self, status_code: int, body: str = ""):
        self.status_code = status_code
        self.body = (body or "")[:200]
        super().__init__(f"IoT Agent error: {status_code} - {self.body}")


def _generate_tenant_apikey() -> str:
    """Generate a random API key for a tenant service group."""
    return f"nkz_iot_{secrets.token_hex(16)}"


def get_or_create_service_group(tenant: str, iota_url: str, headers: dict,
                                cbroker: Optional[str] = None) -> Optional[str]:
    """Return the tenant's service-group apikey, creating the group if absent.

    `cbroker` defaults to the ORION_URL environment variable. Returns None on
    failure (never raises).
    """
    cbroker = cbroker or os.getenv("ORION_URL", "")
    try:
        resp = requests.get(f"{iota_url}/iot/services", headers=headers, timeout=5)
        if resp.status_code == 200:
            services = resp.json().get("services", [])
            if services:
                existing_apikey = services[0].get("apikey")
                if not existing_apikey:
                    logger.error(f"Service group for tenant '{tenant}' has no apikey")
                    return None
                logger.debug(f"Service group exists for tenant '{tenant}', apikey=<redacted:{len(existing_apikey)}ch>")
                return existing_apikey

        tenant_apikey = _generate_tenant_apikey()
        service_group = {
            "services": [{
                "resource": "/iot/json",
                "apikey": tenant_apikey,
                "cbroker": cbroker,
                "entity_type": "Device",
                "attributes": [],
                "lazy": [],
                "commands": [],
                "static_attributes": [],
            }]
        }
        resp = requests.post(f"{iota_url}/iot/services", json=service_group,
                             headers=headers, timeout=5)

        if resp.status_code in [200, 201]:
            logger.info(f"Created service group for tenant '{tenant}' with apikey=<redacted:{len(tenant_apikey)}ch>")
            return tenant_apikey
        elif resp.status_code == 409:
            # Race condition: another request created it first - retrieve it
            resp2 = requests.get(f"{iota_url}/iot/services", headers=headers, timeout=5)
            if resp2.status_code == 200:
                services = resp2.json().get("services", [])
                if services:
                    return services[0].get("apikey")
            logger.warning(f"Service group 409 but cannot retrieve for tenant '{tenant}'")
            return None
        else:
            logger.error(f"Failed to create service group for tenant '{tenant}': "
                         f"{resp.status_code} - {resp.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"Error in get_or_create_service_group for tenant '{tenant}': {e}")
        return None


def build_iota_attributes(measurements: list) -> list:
    """IoT Agent attribute mappings from profile `mapping.measurements[]`.

    `expression` is passed through verbatim: it is JEXL over the incoming keys
    (e.g. "r3 * 0.1"); the legacy "${@...}" forms are silently ignored by the
    agent and must not be produced here.
    """
    attributes = []
    for m in measurements:
        attr = {
            "object_id": m.get("incomingKey") or m["type"],
            "name": m["sdmAttribute"],
            "type": m.get("valueType") or ("Number" if m.get("unit") else "Text"),
        }
        if m.get("expression"):
            attr["expression"] = m["expression"]
        attributes.append(attr)
    return attributes


def provision_device(iota_url: str, headers: dict, *, apikey: str, device_id: str,
                     entity_name: str, entity_type: str, attributes: list,
                     static_attributes: Optional[list] = None) -> None:
    """Register a device in the IoT Agent. Raises IotAgentError on non-2xx (409 included)."""
    body = {
        "devices": [{
            "device_id": device_id,
            "entity_name": entity_name,
            "entity_type": entity_type,
            "protocol": "IoTA-JSON",
            "transport": "MQTT",
            "apikey": apikey,
            "attributes": attributes,
            "lazy": [],
            "commands": [],
            "static_attributes": list(static_attributes or []),
        }]
    }
    resp = requests.post(f"{iota_url}/iot/devices", json=body, headers=headers, timeout=10)
    if not 200 <= resp.status_code < 300:
        raise IotAgentError(resp.status_code, resp.text)


def delete_device(iota_url: str, headers: dict, device_id: str) -> bool:
    """Delete a device. True when it is gone (204 or 404)."""
    resp = requests.delete(f"{iota_url}/iot/devices/{device_id}", headers=headers, timeout=10)
    return resp.status_code in (204, 404)


def update_device_attributes(iota_url: str, headers: dict, device_id: str,
                             attributes: list) -> None:
    """Replace a device's attribute mappings. Raises IotAgentError on non-2xx."""
    resp = requests.put(f"{iota_url}/iot/devices/{device_id}",
                        json={"attributes": attributes}, headers=headers, timeout=10)
    if not 200 <= resp.status_code < 300:
        raise IotAgentError(resp.status_code, resp.text)
