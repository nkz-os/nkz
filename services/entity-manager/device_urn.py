"""The ids registration gives: `urn:ngsi-ld:<Device|ManufacturingMachine>:<tenant>:<externalId>`."""
import re
from typing import Optional

EXTERNAL_ID_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
DEVICE_TYPES = ('Device', 'ManufacturingMachine')


def external_id_of(tenant: str, urn: Optional[str]) -> Optional[str]:
    """The externalId of a registered entity of this tenant; None for any other id."""
    parts = (urn or '').split(':')
    if (len(parts) != 5 or parts[:2] != ['urn', 'ngsi-ld'] or parts[2] not in DEVICE_TYPES
            or parts[3] != tenant or not EXTERNAL_ID_RE.fullmatch(parts[4])):
        return None
    return parts[4]


def is_device_type(urn: str) -> bool:
    """True for a `Device` URN, False for a `ManufacturingMachine`."""
    return urn.startswith('urn:ngsi-ld:Device:')
