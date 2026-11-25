"""The tenants background work runs for, in one place.

A suspended tenant (`deleted_at` set) keeps its data but nothing works on it
until it is restored. Every loop that walks the tenants uses this statement,
so none of them disagrees about who is active.
"""

ACTIVE_TENANT_IDS_SQL = (
    "SELECT DISTINCT tenant_id FROM tenants"
    " WHERE tenant_id IS NOT NULL AND deleted_at IS NULL"
)

# For statements over another table with a tenant_id column: `AND` + this.
TENANT_IS_ACTIVE_SQL = (
    "tenant_id IN (SELECT tenant_id FROM tenants WHERE deleted_at IS NULL)"
)
