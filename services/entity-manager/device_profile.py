"""The profile an entity is on, for every reader of this service.

`common.device_model.device_profile_code` is the lookup; this module applies it to entities read
from Orion and logs, once each, the Devices that still name their profile only with `profileCode`.
"""
import logging
import threading
from collections import OrderedDict
from typing import Mapping, Optional

from common.device_model import device_profile_code

logger = logging.getLogger(__name__)

# A read whose context lacks the term brings the type as an IRI.
_DEVICE_TYPES = ('Device', 'https://smartdatamodels.org/dataModel.Device/Device')

# Bound of the memory of what was logged; past it the oldest are forgotten and may log again.
_MAX_LOGGED = 10_000
_logged: 'OrderedDict[tuple, None]' = OrderedDict()
_logged_lock = threading.Lock()


def entity_profile_code(tenant: str, entity: Optional[Mapping]) -> Optional[str]:
    """The profile code an entity of `tenant` is on, or None.

    Only a Device has `refDeviceModel`: any other type is on its `profileCode`.
    """
    if not entity:
        return None
    if entity.get('type') not in _DEVICE_TYPES:
        return device_profile_code(tenant, {'profileCode': entity.get('profileCode')})[0]
    code, source = device_profile_code(tenant, entity)
    if source == 'legacy':
        _log_fallback_once(tenant, entity.get('id'), code)
    return code


def _log_fallback_once(tenant: str, entity_id, code: str) -> None:
    key = (tenant, str(entity_id))
    with _logged_lock:
        if key in _logged:
            return
        _logged[key] = None
        while len(_logged) > _MAX_LOGGED:
            _logged.popitem(last=False)
    logger.info("device_profile_code_fallback: tenant=%s entity_id=%s profile=%s",
                tenant, entity_id, code)
