"""A PAT of a suspended tenant must validate as invalid."""
import os
from unittest.mock import MagicMock, patch

import pytest

HASH = "a" * 64


@pytest.fixture
def client(webhook_module):
    return webhook_module.app.test_client()


def _conn_returning(row):
    cur = MagicMock()
    cur.fetchone.return_value = row
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_query_excludes_suspended_tenants(client, webhook_module):
    conn, cur = _conn_returning(None)
    with patch.dict(os.environ, {"INTERNAL_PAT_VALIDATE_SECRET": "s"}), patch.object(
        webhook_module, "POSTGRES_URL", "postgresql://x"
    ), patch.object(webhook_module.webhook_service, "get_db_connection", return_value=conn):
        resp = client.post(
            "/internal/validate-pat",
            json={"token_hash": HASH},
            headers={"X-Internal-Secret": "s"},
        )
    assert resp.status_code == 200
    assert resp.get_json() == {"valid": False}
    sql = cur.execute.call_args.args[0]
    assert "deleted_at IS NULL" in sql
    assert "LEFT JOIN tenants" in sql
