"""Tests for the tenant-deleted identity event."""

from __future__ import annotations


class TestHandleTenantDeleted:
    def test_rejects_invalid_tenant_id(self, webhook_module):
        with webhook_module.app.test_request_context("/"):
            resp, status = webhook_module.handle_tenant_deleted("../evil", {})
        assert status == 400

    def test_acknowledges_without_touching_the_cluster(self, webhook_module):
        """Tenants own no Kubernetes resources: the event is acknowledged and
        no shell or cluster call is made (the module no longer imports
        subprocess at all)."""
        assert not hasattr(webhook_module, "subprocess")
        with webhook_module.app.test_request_context("/"):
            resp, status = webhook_module.handle_tenant_deleted("tenant-one", {})
        assert status == 200
        assert resp.get_json()["message"] == "Tenant deletion processed"
