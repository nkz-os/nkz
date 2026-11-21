"""Send dead inbox rows back to the inbox.

    python -m telemetry_worker.inbox_redrive [--since ISO] [--entity URN] [--dry-run]

Moves rows of `notification_inbox_dead` back to `notification_inbox` with
attempts reset to 0. `--since` keeps rows that died at or after that instant,
`--entity` one entity id; `--dry-run` only counts them. The database comes from
POSTGRES_URL (or DATABASE_URL).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

import asyncpg

from telemetry_worker.inbox import NotificationInbox


def _parse_since(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 instant: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m telemetry_worker.inbox_redrive", description=__doc__.split("\n\n")[0])
    parser.add_argument("--since", type=_parse_since, help="only rows that died at or after this ISO instant")
    parser.add_argument("--entity", help="only rows of this entity id")
    parser.add_argument("--dry-run", action="store_true", help="report the count, move nothing")
    parser.add_argument("--queue", default="telemetry", help="inbox queue (default: telemetry)")
    return parser


async def count_dead(pool, queue: str, since: Optional[datetime], entity_id: Optional[str]) -> int:
    return int(await pool.fetchval(
        "SELECT count(*) FROM notification_inbox_dead WHERE queue = $1 "
        "AND ($2::timestamptz IS NULL OR died_at >= $2) "
        "AND ($3::text IS NULL OR entity_id = $3)",
        queue, since, entity_id,
    ))


async def run_redrive(
    pool, queue: str, since: Optional[datetime], entity_id: Optional[str], dry_run: bool
) -> int:
    """Rows that would move (dry run) or moved."""
    if dry_run:
        return await count_dead(pool, queue, since, entity_id)
    return await NotificationInbox(pool, queue).redrive(since, entity_id)


async def _amain(args: argparse.Namespace) -> int:
    dsn = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        print("POSTGRES_URL (or DATABASE_URL) is not set", file=sys.stderr)
        return 2
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2, command_timeout=60)
    try:
        count = await run_redrive(pool, args.queue, args.since, args.entity, args.dry_run)
    finally:
        await pool.close()
    verb = "would redrive" if args.dry_run else "redriven"
    print(f"{verb} {count} dead row(s) in queue {args.queue}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return asyncio.run(_amain(_build_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
