"""Smoke test: SUT must import cleanly under the test harness mocks."""


def test_module_imports(webhook_module):
    """The conftest's heavy-mock setup is sufficient for module load."""
    assert hasattr(webhook_module, "TENANT_ID_RE")
    assert hasattr(webhook_module, "_is_valid_tenant_id")
    assert hasattr(webhook_module, "_verify_shared_secret")
    assert hasattr(webhook_module, "_internal_error")
    assert hasattr(webhook_module, "_delete_keycloak_user_best_effort")
