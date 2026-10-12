"""Tenant storage provisioning: one idempotent step, no cluster resources.

`provision_tenant_core` ensures the tenant's Orion-LD database and stores an
API key digest in PostgreSQL. Registration, activation, the admin endpoint and
the identity event all go through it; emails never hold the response.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestProvisionTenantCore:
    def test_success_returns_immutable_result(self, webhook_module):
        svc = webhook_module.webhook_service
        with patch.object(svc, "_ensure_orion_tenant_db", return_value=True) as mongo, \
             patch.object(svc, "generate_api_key", return_value="key-1") as key:
            result = svc.provision_tenant_core("acme", {"plan": "basic"})

        assert result.ok
        assert result.orion_db is True
        assert result.api_key == "key-1"
        assert result.errors == ()
        mongo.assert_called_once_with("acme")
        key.assert_called_once_with("acme")
        try:
            result.api_key = "other"
        except Exception:
            pass
        else:
            raise AssertionError("TenantProvisioning must be immutable")

    def test_database_failure_is_recorded_and_key_still_attempted(self, webhook_module):
        svc = webhook_module.webhook_service
        with patch.object(svc, "_ensure_orion_tenant_db", return_value=False), \
             patch.object(svc, "generate_api_key", return_value="key-1") as key:
            result = svc.provision_tenant_core("acme", {"plan": "basic"})

        assert not result.ok
        assert result.orion_db is False
        assert result.api_key == "key-1"
        assert "orion-ld database could not be ensured" in result.errors
        key.assert_called_once_with("acme")

    def test_api_key_exception_is_recorded_not_raised(self, webhook_module):
        svc = webhook_module.webhook_service
        with patch.object(svc, "_ensure_orion_tenant_db", return_value=True), \
             patch.object(svc, "generate_api_key", side_effect=RuntimeError("db down")):
            result = svc.provision_tenant_core("acme", {"plan": "basic"})

        assert not result.ok
        assert result.orion_db is True
        assert result.api_key is None
        assert "api key could not be stored" in result.errors


class TestGenerateApiKeyStoresOnlyInPostgres:
    def test_new_key_row_is_inserted(self, webhook_module):
        svc = webhook_module.webhook_service
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.side_effect = [None, {"column_name": "key_type"}]
        conn.cursor.return_value = cur
        with patch.object(webhook_module, "POSTGRES_URL", "postgresql://test"), \
             patch.object(webhook_module.psycopg2, "connect", return_value=conn):
            key = svc.generate_api_key("acme")

        assert key and len(key) == 64
        conn.commit.assert_called_once()
        inserted = [c for c in cur.execute.call_args_list if "INSERT INTO api_keys" in c[0][0]]
        assert len(inserted) == 1
        assert key not in inserted[0][0][1]  # only the digest is stored

    def test_without_database_url_returns_none(self, webhook_module):
        svc = webhook_module.webhook_service
        with patch.object(webhook_module, "POSTGRES_URL", ""):
            assert svc.generate_api_key("acme") is None


class TestSendEmailAsync:
    def test_returns_before_the_email_is_sent_and_logs_outcome(self, webhook_module):
        resp = MagicMock(status_code=200)
        with patch.object(webhook_module, "_email_post", return_value=resp) as post:
            thread = webhook_module._send_email_async(
                "/send/welcome", {"email": "a@b.test"}, purpose="welcome"
            )
            thread.join(timeout=5)
        assert not thread.is_alive()
        post.assert_called_once_with("/send/welcome", {"email": "a@b.test"}, timeout=10)

    def test_failure_never_propagates(self, webhook_module):
        with patch.object(webhook_module, "_email_post", side_effect=RuntimeError("smtp down")):
            thread = webhook_module._send_email_async("/send/welcome", {}, purpose="welcome")
            thread.join(timeout=5)
        assert not thread.is_alive()


class TestNoClusterProvisioningLeft:
    def test_legacy_entry_points_are_gone(self, webhook_module):
        svc = webhook_module.webhook_service
        for name in ("create_tenant_resources", "create_ros2_resources",
                     "_delete_tenant_namespace", "_ensure_namespace_exists"):
            assert not hasattr(svc, name), name
        for name in ("CREATE_TENANT_SCRIPT", "CREATE_ROS2_SCRIPT", "subprocess"):
            assert not hasattr(webhook_module, name), name
        rules = {rule.rule for rule in webhook_module.app.url_map.iter_rules()}
        assert "/api/tenant/services/ros2/activate" not in rules
