"""Guard: the AgriParcel write-enforcement hook must NOT exist (uniform writes)."""
import fiware_api_gateway as gw


def test_enforcement_hook_removed():
    assert not hasattr(gw, "enforce_parcel_write_authority"), (
        "enforce_parcel_write_authority must be removed — AgriParcel writes go via /ngsi-ld like every entity"
    )


def test_agriparcel_post_to_ngsild_not_blocked_by_before_request():
    assert not hasattr(gw, "_targets_agriparcel")
