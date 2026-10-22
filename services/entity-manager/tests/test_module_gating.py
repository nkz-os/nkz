import pytest
from entity_manager_gating import can_tenant_install_module


@pytest.mark.parametrize("tenant_level,required_level,expected", [
    (0, 0, True),    # basic can install basic
    (1, 0, True),    # pro can install basic (THE ORIGINAL BUG)
    (1, 1, True),    # pro can install pro
    (1, 2, False),   # pro CANNOT install premium
    (2, 2, True),    # premium can install premium
    (2, 3, False),   # premium CANNOT install enterprise
    (3, 3, True),    # enterprise can install enterprise
    (3, 0, True),    # enterprise can install basic
    (None, 0, True), # NULL tenant = 0 basic
    (0, None, True), # NULL required = 0 basic
])
def test_can_tenant_install_module(tenant_level, required_level, expected):
    assert can_tenant_install_module(tenant_level, required_level) is expected
