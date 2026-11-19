"""Read access to the observable-property catalogue (`public.observation_properties`)."""
from typing import Optional

from psycopg2.extras import RealDictCursor

import dataloggers
from db_helper import get_db_connection_with_tenant


@dataloggers.in_step('db', 'Database error')
def get_property(tenant_id: str, name: str) -> Optional[dict]:
    """The non-deprecated property `name` visible to the tenant: its own row first, then the global one."""
    with get_db_connection_with_tenant(tenant_id) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM observation_properties
            WHERE name = %s AND (tenant_id IS NULL OR tenant_id = %s)
              AND deprecated_at IS NULL
            ORDER BY tenant_id NULLS LAST
            LIMIT 1
        """, (name, tenant_id))
        row = cur.fetchone()
        cur.close()
    return row
