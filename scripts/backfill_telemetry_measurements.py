#!/usr/bin/env python3
"""
Chunked backfill and validation for telemetry_measurements (PR-0).

Key guarantees:
- Runs in bounded time windows (no monolithic transaction).
- Commits every batch and supports resumable progress checkpoints.
- Optional throttling between batches to reduce WAL pressure.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


NUMERIC_REGEX = r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$"
NON_FINITE = (
    "nan",
    "+nan",
    "-nan",
    "inf",
    "+inf",
    "-inf",
    "infinity",
    "+infinity",
    "-infinity",
)


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def default_start() -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class BatchResult:
    candidates: int
    inserted: int


class DbClient:
    def __init__(self) -> None:
        self.kind = ""
        self.conn = None
        self._connect()

    def _connect(self) -> None:
        dsn = os.getenv("DATABASE_URL")
        if dsn:
            try:
                import psycopg  # type: ignore

                self.kind = "psycopg3"
                self.conn = psycopg.connect(dsn)
                return
            except Exception:
                pass
            try:
                import psycopg2  # type: ignore

                self.kind = "psycopg2"
                self.conn = psycopg2.connect(dsn)
                return
            except Exception as exc:
                raise RuntimeError(
                    f"Could not connect using DATABASE_URL: {exc}"
                ) from exc

        # Fallback to PG* environment variables
        try:
            import psycopg  # type: ignore

            self.kind = "psycopg3"
            self.conn = psycopg.connect(
                host=os.getenv("PGHOST", "localhost"),
                port=int(os.getenv("PGPORT", "5432")),
                dbname=os.getenv("PGDATABASE", "postgres"),
                user=os.getenv("PGUSER", "postgres"),
                password=os.getenv("PGPASSWORD", ""),
            )
            return
        except Exception:
            pass
        try:
            import psycopg2  # type: ignore

            self.kind = "psycopg2"
            self.conn = psycopg2.connect(
                host=os.getenv("PGHOST", "localhost"),
                port=int(os.getenv("PGPORT", "5432")),
                dbname=os.getenv("PGDATABASE", "postgres"),
                user=os.getenv("PGUSER", "postgres"),
                password=os.getenv("PGPASSWORD", ""),
            )
            return
        except Exception as exc:
            raise RuntimeError(f"Could not connect using PG* variables: {exc}") from exc

    def execute_one(self, query: str, params: tuple = ()) -> tuple:
        cur = self.conn.cursor()
        try:
            cur.execute(query, params)
            row = cur.fetchone()
            return row if row is not None else tuple()
        finally:
            cur.close()

    def execute(self, query: str, params: tuple = ()) -> None:
        cur = self.conn.cursor()
        try:
            cur.execute(query, params)
        finally:
            cur.close()

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        self.conn.close()


def ensure_state_row(db: DbClient, job_name: str, start_ts: datetime) -> None:
    db.execute(
        """
        INSERT INTO telemetry_measurements_backfill_state (job_name, last_observed_at, metadata)
        VALUES (%s, %s, '{}'::jsonb)
        ON CONFLICT (job_name) DO NOTHING
        """,
        (job_name, start_ts),
    )
    db.commit()


def read_resume_ts(db: DbClient, job_name: str) -> Optional[datetime]:
    row = db.execute_one(
        "SELECT last_observed_at FROM telemetry_measurements_backfill_state WHERE job_name = %s",
        (job_name,),
    )
    if not row or row[0] is None:
        return None
    return row[0].astimezone(timezone.utc)


def write_resume_ts(
    db: DbClient, job_name: str, ts: datetime, candidates: int, inserted: int
) -> None:
    db.execute(
        """
        UPDATE telemetry_measurements_backfill_state
        SET last_observed_at = %s,
            updated_at = CURRENT_TIMESTAMP,
            metadata = jsonb_build_object(
                'last_candidates', %s,
                'last_inserted', %s
            )
        WHERE job_name = %s
        """,
        (ts, candidates, inserted, job_name),
    )


def run_batch(
    db: DbClient,
    start_ts: datetime,
    end_ts: datetime,
    tenant_id: Optional[str],
) -> BatchResult:
    row = db.execute_one(
        """
        WITH source_rows AS (
            SELECT
                e.tenant_id,
                e.observed_at,
                e.id AS source_event_id,
                e.entity_id,
                e.entity_type,
                e.device_id,
                e.sensor_id,
                e.task_id,
                kv.key AS attribute_name,
                trim(kv.value) AS value_txt
            FROM telemetry_events e
            CROSS JOIN LATERAL jsonb_each_text(e.payload->'measurements') AS kv(key, value)
            WHERE e.observed_at >= %s
              AND e.observed_at < %s
              AND (%s IS NULL OR e.tenant_id = %s)
        ),
        filtered AS (
            SELECT
                tenant_id,
                observed_at,
                source_event_id,
                entity_id,
                entity_type,
                device_id,
                sensor_id,
                task_id,
                attribute_name,
                value_txt::double precision AS value
            FROM source_rows
            WHERE value_txt ~ %s
              AND lower(value_txt) NOT IN %s
        ),
        ins AS (
            INSERT INTO telemetry_measurements (
                tenant_id, observed_at, source_event_id, entity_id, entity_type,
                device_id, sensor_id, task_id, attribute_name, value, source
            )
            SELECT
                tenant_id, observed_at, source_event_id, entity_id, entity_type,
                device_id, sensor_id, task_id, attribute_name, value, 'telemetry_events'
            FROM filtered
            ON CONFLICT (tenant_id, observed_at, source_event_id, attribute_name) DO NOTHING
            RETURNING 1
        )
        SELECT
            (SELECT COUNT(*) FROM filtered) AS candidates,
            (SELECT COUNT(*) FROM ins) AS inserted
        """,
        (start_ts, end_ts, tenant_id, tenant_id, NUMERIC_REGEX, NON_FINITE),
    )
    candidates = int(row[0] if row and row[0] is not None else 0)
    inserted = int(row[1] if row and row[1] is not None else 0)
    return BatchResult(candidates=candidates, inserted=inserted)


def run_backfill(args: argparse.Namespace) -> int:
    start_ts = parse_iso(args.start_time) if args.start_time else default_start()
    end_ts = parse_iso(args.end_time) if args.end_time else now_utc()
    if start_ts >= end_ts:
        print("ERROR: start_time must be before end_time", file=sys.stderr)
        return 2

    batch_window = timedelta(hours=args.batch_hours)
    if batch_window.total_seconds() <= 0:
        print("ERROR: batch_hours must be > 0", file=sys.stderr)
        return 2

    db = DbClient()
    try:
        ensure_state_row(db, args.job_name, start_ts)
        resume_ts = read_resume_ts(db, args.job_name)
        cursor_ts = max(start_ts, resume_ts) if resume_ts else start_ts
        print(
            f"Backfill starting job={args.job_name} tenant={args.tenant_id or 'ALL'} "
            f"from={cursor_ts.isoformat()} to={end_ts.isoformat()} batch_hours={args.batch_hours}"
        )

        total_candidates = 0
        total_inserted = 0
        batches = 0

        while cursor_ts < end_ts:
            if args.max_batches is not None and batches >= args.max_batches:
                print(f"Reached max_batches={args.max_batches}; stopping early.")
                break

            next_ts = min(cursor_ts + batch_window, end_ts)
            try:
                result = run_batch(db, cursor_ts, next_ts, args.tenant_id)
                write_resume_ts(
                    db, args.job_name, next_ts, result.candidates, result.inserted
                )
                db.commit()
            except Exception as exc:
                db.rollback()
                print(
                    f"Batch failed [{cursor_ts.isoformat()} -> {next_ts.isoformat()}]: {exc}",
                    file=sys.stderr,
                )
                return 1

            batches += 1
            total_candidates += result.candidates
            total_inserted += result.inserted
            print(
                f"batch={batches} window=[{cursor_ts.isoformat()} -> {next_ts.isoformat()}] "
                f"candidates={result.candidates} inserted={result.inserted}"
            )

            cursor_ts = next_ts
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)

        print(
            f"Backfill completed batches={batches} "
            f"total_candidates={total_candidates} total_inserted={total_inserted}"
        )
        return 0
    finally:
        db.close()


def run_validate(args: argparse.Namespace) -> int:
    start_ts = (
        parse_iso(args.start_time) if args.start_time else now_utc() - timedelta(days=7)
    )
    end_ts = parse_iso(args.end_time) if args.end_time else now_utc()
    if start_ts >= end_ts:
        print("ERROR: start_time must be before end_time", file=sys.stderr)
        return 2

    db = DbClient()
    try:
        source = db.execute_one(
            """
            SELECT COUNT(*)
            FROM telemetry_events e
            CROSS JOIN LATERAL jsonb_each_text(e.payload->'measurements') AS kv(key, value)
            WHERE e.observed_at >= %s
              AND e.observed_at < %s
              AND (%s IS NULL OR e.tenant_id = %s)
              AND trim(kv.value) ~ %s
              AND lower(trim(kv.value)) NOT IN %s
            """,
            (
                start_ts,
                end_ts,
                args.tenant_id,
                args.tenant_id,
                NUMERIC_REGEX,
                NON_FINITE,
            ),
        )
        target = db.execute_one(
            """
            SELECT COUNT(*)
            FROM telemetry_measurements tm
            WHERE tm.observed_at >= %s
              AND tm.observed_at < %s
              AND (%s IS NULL OR tm.tenant_id = %s)
            """,
            (start_ts, end_ts, args.tenant_id, args.tenant_id),
        )
        source_count = int(source[0] if source else 0)
        target_count = int(target[0] if target else 0)
        diff = source_count - target_count
        ratio = (target_count / source_count) if source_count > 0 else 1.0
        print(
            f"validate tenant={args.tenant_id or 'ALL'} "
            f"window=[{start_ts.isoformat()} -> {end_ts.isoformat()}] "
            f"source={source_count} target={target_count} diff={diff} ratio={ratio:.6f}"
        )
        if source_count == 0:
            return 0
        return 0 if ratio >= args.min_ratio else 1
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Backfill telemetry_measurements in bounded batches."
    )
    p.add_argument("--mode", choices=("backfill", "validate"), default="backfill")
    p.add_argument(
        "--tenant-id", default=None, help="Optional tenant scope (default: all tenants)"
    )
    p.add_argument(
        "--start-time",
        default=None,
        help="ISO timestamp (default: 1970-01-01 for backfill, now-7d for validate)",
    )
    p.add_argument("--end-time", default=None, help="ISO timestamp (default: now)")
    p.add_argument("--job-name", default="telemetry_measurements_pr0")
    p.add_argument("--batch-hours", type=int, default=24)
    p.add_argument("--sleep-seconds", type=float, default=0.3)
    p.add_argument("--max-batches", type=int, default=None)
    p.add_argument(
        "--min-ratio",
        type=float,
        default=0.999,
        help="Validation pass threshold target/source",
    )
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.mode == "validate":
        return run_validate(args)
    return run_backfill(args)


if __name__ == "__main__":
    raise SystemExit(main())
