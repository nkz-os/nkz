"""The profile an entity is on, for every reader of this service.

`common.device_model.entity_profile` is the lookup; this module applies it to entities read
from Orion and logs, once each, the Devices that still name their profile only with `profileCode`,
the ones whose two codes disagree and the ones whose `refDeviceModel` is not usable.
"""
import logging
import threading
from collections import OrderedDict
from typing import Mapping, Optional

from common.device_model import entity_profile, is_device, profile_codes

logger = logging.getLogger(__name__)

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
    code, source = entity_profile(tenant, entity)
    if not is_device(entity):
        return code
    entity_id = entity.get('id')
    codes = profile_codes(tenant, entity)
    if codes.model_error and first_time('model_ignored', tenant, entity_id):
        logger.warning("refDeviceModel ignored: tenant=%s entity_id=%s reason=%s",
                       tenant, entity_id, codes.model_error)
    if None not in (codes.model, codes.legacy) and codes.model != codes.legacy:
        if first_time('mismatch', tenant, entity_id):
            logger.warning("device_profile_code_mismatch: tenant=%s entity_id=%s "
                           "refDeviceModel=%s profileCode=%s",
                           tenant, entity_id, codes.model, codes.legacy)
    elif source == 'legacy' and first_time('fallback', tenant, entity_id):
        logger.info("device_profile_code_fallback: tenant=%s entity_id=%s profile=%s",
                    tenant, entity_id, code)
    return code


def first_time(event: str, tenant: str, entity_id) -> bool:
    """True the first time `event` happens to this entity, while it is remembered."""
    key = (event, tenant, str(entity_id))
    with _logged_lock:
        if key in _logged:
            return False
        _logged[key] = None
        while len(_logged) > _MAX_LOGGED:
            _logged.popitem(last=False)
    return True
