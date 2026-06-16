"""
Tests for enforce_parcel_write_authority() before_request hook.

The hook blocks external AgriParcel writes to Orion-LD; entity-manager is the
sole authorized writer.  GET requests and non-AgriParcel writes are always
allowed through.

We use test_request_context() throughout because the full test client fires
pre-existing auth hooks first, which would return 401 before our hook runs —
making the status code ambiguous.  Direct invocation of the hook function is
the clean, unambiguous approach for unit-testing it.
"""

import os

import pytest


# ---------------------------------------------------------------------------
# Test 1 — external AgriParcel POST is blocked (hook returns 403 response)
# ---------------------------------------------------------------------------
def test_external_agriparcel_post_blocked():
    """Hook must return a 403 response for an unauthenticated AgriParcel POST."""
    import fiware_api_gateway as gw

    with gw.app.test_request_context(
        "/ngsi-ld/v1/entities",
        method="POST",
        json={"type": "AgriParcel", "id": "urn:x"},
    ):
        result = gw.enforce_parcel_write_authority()

    # The hook returns a (response, status) tuple when blocking.
    assert result is not None, "Hook should block the request"
    response, status = result
    assert status == 403


# ---------------------------------------------------------------------------
# Test 2 — entity-manager (correct secret) is allowed through
# ---------------------------------------------------------------------------
def test_agriparcel_post_from_entity_manager_allowed(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "s3cret")
    import fiware_api_gateway as gw

    with gw.app.test_request_context(
        "/ngsi-ld/v1/entities",
        method="POST",
        json={"type": "AgriParcel"},
        headers={"X-Internal-Service-Secret": "s3cret"},
    ):
        assert gw.enforce_parcel_write_authority() is None


# ---------------------------------------------------------------------------
# Test 3 — GET on AgriParcel is always allowed
# ---------------------------------------------------------------------------
def test_get_agriparcel_not_blocked():
    import fiware_api_gateway as gw

    with gw.app.test_request_context(
        "/ngsi-ld/v1/entities?type=AgriParcel", method="GET"
    ):
        assert gw.enforce_parcel_write_authority() is None


# ---------------------------------------------------------------------------
# Test 4 — write on a different entity type passes through
# ---------------------------------------------------------------------------
def test_other_type_write_not_blocked():
    import fiware_api_gateway as gw

    with gw.app.test_request_context(
        "/ngsi-ld/v1/entities",
        method="POST",
        json={"type": "WeatherObserved"},
    ):
        assert gw.enforce_parcel_write_authority() is None


# ---------------------------------------------------------------------------
# Test 5 — PATCH by entity URN is also blocked
# ---------------------------------------------------------------------------
def test_external_agriparcel_patch_by_urn_blocked():
    import fiware_api_gateway as gw

    with gw.app.test_request_context(
        "/ngsi-ld/v1/entities/urn:ngsi-ld:AgriParcel:abc123",
        method="PATCH",
        json={"name": {"type": "Property", "value": "x"}},
    ):
        result = gw.enforce_parcel_write_authority()

    assert result is not None
    _, status = result
    assert status == 403


# ---------------------------------------------------------------------------
# Test 6 — batch create with AgriParcel in the list is blocked
# ---------------------------------------------------------------------------
def test_external_agriparcel_batch_create_blocked():
    import fiware_api_gateway as gw

    with gw.app.test_request_context(
        "/ngsi-ld/v1/entityOperations/create",
        method="POST",
        json=[
            {"type": "WeatherObserved", "id": "urn:a"},
            {"type": "AgriParcel", "id": "urn:b"},
        ],
    ):
        result = gw.enforce_parcel_write_authority()

    assert result is not None
    _, status = result
    assert status == 403


# ---------------------------------------------------------------------------
# Test 7 — wrong secret is still blocked
# ---------------------------------------------------------------------------
def test_wrong_secret_is_blocked(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "s3cret")
    import fiware_api_gateway as gw

    with gw.app.test_request_context(
        "/ngsi-ld/v1/entities",
        method="POST",
        json={"type": "AgriParcel"},
        headers={"X-Internal-Service-Secret": "wrong"},
    ):
        result = gw.enforce_parcel_write_authority()

    assert result is not None
    _, status = result
    assert status == 403
