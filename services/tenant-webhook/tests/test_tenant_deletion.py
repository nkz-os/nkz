"""Tests for tenant deletion cleanup (command injection hardening)."""

from __future__ import annotations

from unittest.mock import patch


class TestHandleTenantDeleted:
    def test_rejects_invalid_tenant_id(self, webhook_module):
        with webhook_module.app.test_request_context("/"):
            resp, status = webhook_module.handle_tenant_deleted("../evil", {})
        assert status == 400

    def test_deletes_namespace_via_kubectl_argv(self, webhook_module):
        with webhook_module.app.test_request_context("/"):
            with patch.object(webhook_module, "subprocess") as mock_subprocess:
                mock_subprocess.run.return_value = type(
                    "R", (), {"returncode": 0, "stderr": ""}
                )()
                with patch.object(webhook_module, "os") as mock_os:
                    mock_os.path.exists.return_value = False
                    resp, status = webhook_module.handle_tenant_deleted("tenant-one", {})

        assert status == 200
        mock_subprocess.run.assert_called_once()
        cmd = mock_subprocess.run.call_args[0][0]
        assert cmd[0] == "kubectl"
        assert cmd[1] == "delete"
        assert cmd[2] == "namespace"
        assert cmd[3] == "nekazari-tenant-tenant-one"
        assert "tenant-one" not in cmd[0]
