#!/usr/bin/env python3
"""Send the support welcome email to users created before it existed.

One-off backfill, safe to re-run: users already recorded in
public.support_welcome_emails are skipped, and a user is recorded only after
email-service accepted the send. Users created after --created-before get the
email at tenant creation and are left out.

Run inside the tenant-webhook pod (same env: KEYCLOAK_*, POSTGRES_URL,
EMAIL_SERVICE_URL, INTERNAL_SERVICE_SECRET):

    python3 send_support_welcome.py --created-before 2026-10-28T12:00:00Z --dry-run
    python3 send_support_welcome.py --created-before ... --only-tenant <tenant>
    python3 send_support_welcome.py --created-before ...
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

import psycopg2
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.internal_auth import internal_service_headers  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("support-welcome-backfill")

PAGE_SIZE = 100


@dataclass(frozen=True)
class Recipient:
    user_id: str
    email: str
    tenant_id: str


def _log(event: str, **fields) -> None:
    logger.info(json.dumps({"event": event, "ts": datetime.utcnow().isoformat() + "Z", **fields}))


def _tenant_of(user: dict) -> str:
    attrs = user.get("attributes") or {}
    value = attrs.get("tenant_id") or attrs.get("tenant") or []
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def eligible(users: Iterable[dict], created_before_ms: int, only_tenant: str | None) -> list[Recipient]:
    """Enabled users with an email and a tenant, created before the cutoff."""
    out = []
    for u in users:
        email = (u.get("email") or "").strip().lower()
        tenant = _tenant_of(u)
        if not (u.get("enabled") and u.get("id") and email and tenant):
            continue
        if int(u.get("createdTimestamp") or 0) >= created_before_ms:
            continue
        if only_tenant and tenant != only_tenant:
            continue
        out.append(Recipient(u["id"], email, tenant))
    return out


def _keycloak_base() -> str:
    url = os.getenv("KEYCLOAK_URL", "http://keycloak-service:8080")
    if "keycloak-service" in url and "/auth" not in url:
        url = f"{url.rstrip('/')}/auth"
    return url.rstrip("/")


def _admin_token(base: str) -> str:
    """Same credentials as tenant-webhook: admin user if set, else service account."""
    user, password = os.getenv("KEYCLOAK_ADMIN_USER", ""), os.getenv("KEYCLOAK_ADMIN_PASSWORD", "")
    if user and password:
        url = f"{base}/realms/master/protocol/openid-connect/token"
        data = {"grant_type": "password", "username": user, "password": password, "client_id": "admin-cli"}
    else:
        realm = os.getenv("KEYCLOAK_REALM", "nekazari")
        url = f"{base}/realms/{realm}/protocol/openid-connect/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": os.getenv("KEYCLOAK_CLIENT_ID", "nekazari-api-gateway"),
            "client_secret": os.getenv("KEYCLOAK_CLIENT_SECRET", ""),
        }
    resp = requests.post(url, data=data, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_users(base: str, token: str) -> list[dict]:
    realm = os.getenv("KEYCLOAK_REALM", "nekazari")
    users, first = [], 0
    while True:
        resp = requests.get(
            f"{base}/admin/realms/{realm}/users",
            params={"first": first, "max": PAGE_SIZE, "briefRepresentation": "false"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        page = resp.json()
        users.extend(page)
        if len(page) < PAGE_SIZE:
            return users
        first += PAGE_SIZE


def send_via_email_service(email: str) -> bool:
    url = f"{os.getenv('EMAIL_SERVICE_URL', 'http://email-service:5000')}/email/support-welcome"
    resp = requests.post(
        url, json={"email": email}, headers=internal_service_headers("support welcome backfill"), timeout=30
    )
    if resp.status_code != 200:
        _log("send_failed", status=resp.status_code)
    return resp.status_code == 200


def run(
    recipients: list[Recipient],
    conn,
    send: Callable[[str], bool],
    *,
    dry_run: bool,
    sleep_s: float,
) -> dict:
    """Send to every recipient not yet recorded. Stops at the first failure."""
    with conn.cursor() as cur:
        cur.execute("SELECT user_id FROM support_welcome_emails")
        done = {row[0] for row in cur.fetchall()}

    pending = [r for r in recipients if r.user_id not in done]
    stats = {"eligible": len(recipients), "already_sent": len(recipients) - len(pending), "sent": 0}
    for r in pending:
        if dry_run:
            _log("would_send", user_id=r.user_id, tenant_id=r.tenant_id)
            continue
        if not send(r.email):
            _log("aborted", user_id=r.user_id, tenant_id=r.tenant_id, **stats)
            stats["failed_user_id"] = r.user_id
            return stats
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO support_welcome_emails (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (r.user_id,),
            )
        conn.commit()
        stats["sent"] += 1
        _log("sent", user_id=r.user_id, tenant_id=r.tenant_id)
        time.sleep(sleep_s)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--created-before", required=True, help="ISO-8601 cutoff, e.g. 2026-10-28T12:00:00Z")
    parser.add_argument("--only-tenant", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep", type=float, default=2.0, help="seconds between sends")
    args = parser.parse_args(argv)

    cutoff = datetime.fromisoformat(args.created_before.replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        parser.error("--created-before needs a timezone (e.g. trailing Z)")

    postgres_url = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")
    if not postgres_url:
        _log("config_error", missing="POSTGRES_URL")
        return 2

    base = _keycloak_base()
    users = fetch_users(base, _admin_token(base))
    recipients = eligible(users, int(cutoff.timestamp() * 1000), args.only_tenant)
    _log("start", users=len(users), eligible=len(recipients), dry_run=args.dry_run,
         only_tenant=args.only_tenant, created_before=args.created_before)

    conn = psycopg2.connect(postgres_url)
    try:
        stats = run(recipients, conn, send_via_email_service, dry_run=args.dry_run, sleep_s=args.sleep)
    finally:
        conn.close()
    _log("done", **stats)
    return 1 if "failed_user_id" in stats else 0


if __name__ == "__main__":
    sys.exit(main())
