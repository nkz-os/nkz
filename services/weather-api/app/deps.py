"""FastAPI dependencies for database access."""

from common.tenant_constants import SHARED_TENANT
import psycopg2
from psycopg2.extras import RealDictCursor

from app.config import settings


def get_db_connection(tenant_id: str = SHARED_TENANT):
    """Get a database connection with tenant context set."""
    conn = psycopg2.connect(settings.postgres_url)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.current_tenant', %s, true)", (tenant_id,)
            )
    except Exception:
        conn.rollback()
    return conn


def get_db_cursor(conn, cursor_factory=RealDictCursor):
    """Create a cursor from a connection."""
    return conn.cursor(cursor_factory=cursor_factory)
