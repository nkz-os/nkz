#!/usr/bin/env bash
# publish-module.sh — break-glass module publish (CI is the primary path).
# Refuses to run from a dirty/unpushed tree. Calls the same /publish endpoint.
set -euo pipefail

MODULE_ID="${1:?usage: publish-module.sh <module-id> [dist-dir]}"
DIST="${2:-dist}"
EM="${ENTITY_MANAGER_URL:?set ENTITY_MANAGER_URL}"
SECRET="${INTERNAL_SERVICE_SECRET:?set INTERNAL_SERVICE_SECRET}"

# --- guardrails ---
[ -z "$(git status --porcelain)" ] || { echo "ERROR: git tree is dirty — commit first"; exit 1; }
branch=$(git rev-parse --abbrev-ref HEAD)
git fetch -q origin "$branch" 2>/dev/null || true
if ! git merge-base --is-ancestor HEAD "origin/$branch" 2>/dev/null; then
  echo "ERROR: HEAD is not pushed to origin/$branch — push first"; exit 1
fi
SHA=$(git rev-parse HEAD | cut -c1-40)

# --- refuse to overwrite an existing immutable version ---
if curl -fsS -o /dev/null "$EM/modules/$MODULE_ID/$SHA/mf-manifest.json" 2>/dev/null; then
  [ "${3:-}" = "--republish" ] || { echo "ERROR: version $SHA already published (immutable). Use --republish to force."; exit 1; }
fi

[ -f "$DIST/mf-manifest.json" ] || { echo "ERROR: $DIST/mf-manifest.json not found — build first"; exit 1; }

args=$(cd "$DIST" && find . -type f -printf '-F file=@%p;filename=%P ')
# shellcheck disable=SC2086
code=$(cd "$DIST" && curl -sS -o /tmp/pub.json -w '%{http_code}' \
       -H "X-Internal-Service-Secret: $SECRET" -F "version_hash=$SHA" $args \
       "$EM/api/internal/modules/$MODULE_ID/publish")
echo "publish HTTP $code"; cat /tmp/pub.json; echo
[ "$code" = "200" ] || { echo "publish failed"; exit 1; }
echo "OK — $MODULE_ID @ $SHA published + activated."
