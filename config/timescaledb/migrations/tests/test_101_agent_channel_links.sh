#!/usr/bin/env bash
# Verifies migration 101 is idempotent and that the partial unique index
# enforces one active channel link per (channel, channel_user_id).
set -euo pipefail

CID=$(docker run -d -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test postgres:15)
trap 'docker rm -f "$CID" >/dev/null 2>&1 || true' EXIT
until docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done

MIG="$(dirname "$0")/../101_agent_channel_links.sql"
run() { docker exec -i "$CID" psql -U postgres -d test -v ON_ERROR_STOP=1 "$@"; }

echo "-- apply twice (idempotency)"
run < "$MIG"
run < "$MIG"

echo "-- one active link per channel account"
run -c "INSERT INTO agent_channel_links (channel, channel_user_id, tenant_id, user_id)
        VALUES ('telegram','1','tenant_a','user_a');"
if run -c "INSERT INTO agent_channel_links (channel, channel_user_id, tenant_id, user_id)
           VALUES ('telegram','1','tenant_b','user_b');" 2>/dev/null; then
  echo "FAIL: second active link was accepted"; exit 1
fi

echo "-- revoking frees the slot"
run -c "UPDATE agent_channel_links SET status='revoked', revoked_at=now()
        WHERE channel='telegram' AND channel_user_id='1';"
run -c "INSERT INTO agent_channel_links (channel, channel_user_id, tenant_id, user_id)
        VALUES ('telegram','1','tenant_b','user_b');"

echo "PASS"
