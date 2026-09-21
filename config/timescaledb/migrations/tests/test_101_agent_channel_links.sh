#!/usr/bin/env bash
# Verifies migration 101 is idempotent and that the partial unique index
# enforces one active channel link per (channel, channel_user_id).
set -euo pipefail

CID=$(docker run -d -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test postgres:15)
trap 'docker rm -f "$CID" >/dev/null 2>&1 || true' EXIT

READY_ATTEMPTS=30
for ((i = 1; i <= READY_ATTEMPTS; i++)); do
  if docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1; then
    break
  fi
  if [ "$i" -eq "$READY_ATTEMPTS" ]; then
    echo "FAIL: postgres container did not become ready after ${READY_ATTEMPTS}s" >&2
    exit 1
  fi
  sleep 1
done

MIG="$(dirname "$0")/../101_agent_channel_links.sql"
run() { docker exec -i "$CID" psql -U postgres -d test -v ON_ERROR_STOP=1 "$@"; }

echo "-- apply twice (idempotency)"
run < "$MIG"
run < "$MIG"

echo "-- one active link per channel account"
run -c "INSERT INTO agent_channel_links (channel, channel_user_id, tenant_id, user_id)
        VALUES ('telegram','1','tenant_a','user_a');"
SECOND_INSERT_ERR=$(run -c "INSERT INTO agent_channel_links (channel, channel_user_id, tenant_id, user_id)
           VALUES ('telegram','1','tenant_b','user_b');" 2>&1 >/dev/null) && {
  echo "FAIL: second active link was accepted"; exit 1
}
if ! grep -q "agent_channel_links_active_uq" <<<"$SECOND_INSERT_ERR"; then
  echo "FAIL: second insert was rejected, but not by agent_channel_links_active_uq:" >&2
  echo "$SECOND_INSERT_ERR" >&2
  exit 1
fi
ACTIVE_COUNT=$(docker exec "$CID" psql -U postgres -d test -tA -c \
  "SELECT count(*) FROM agent_channel_links WHERE channel='telegram' AND channel_user_id='1' AND status='active';")
if [ "$ACTIVE_COUNT" != "1" ]; then
  echo "FAIL: expected exactly 1 active row for the channel account, got $ACTIVE_COUNT" >&2
  exit 1
fi

echo "-- revoking frees the slot"
run -c "UPDATE agent_channel_links SET status='revoked', revoked_at=now()
        WHERE channel='telegram' AND channel_user_id='1';"
run -c "INSERT INTO agent_channel_links (channel, channel_user_id, tenant_id, user_id)
        VALUES ('telegram','1','tenant_b','user_b');"

echo "PASS"
