#!/usr/bin/env bash
# =============================================================================
# Keycloak Setup: User Profile attributes + tenant_id mapper (idempotent)
# =============================================================================
# 1. Registers all custom user attributes in the KC26 User Profile
#    (without this, Keycloak 26 silently discards attributes on PUT)
# 2. Creates a protocol mapper on the nekazari-frontend client so that
#    "tenant_id" appears in ID token, access token, and userinfo.
#
# Usage:
#   ./keycloak-setup-mappers.sh
#
# Environment (defaults work on the K8s cluster):
#   KEYCLOAK_URL        internal Keycloak URL  (default: http://keycloak-service:8080/auth)
#   KEYCLOAK_REALM      realm name             (default: nekazari)
#   KEYCLOAK_CLIENT_ID_TARGET  client to add mapper to (default: nekazari-frontend)
#   KEYCLOAK_ADMIN_USER admin username          (default: admin)
#   KEYCLOAK_ADMIN_PASSWORD admin password       (required)
# =============================================================================

set -euo pipefail

KC_URL="${KEYCLOAK_URL:-http://keycloak-service:8080/auth}"
KC_REALM="${KEYCLOAK_REALM:-nekazari}"
KC_CLIENT="${KEYCLOAK_CLIENT_ID_TARGET:-nekazari-frontend}"
KC_ADMIN="${KEYCLOAK_ADMIN_USER:-admin}"
KC_PASS="${KEYCLOAK_ADMIN_PASSWORD:?KEYCLOAK_ADMIN_PASSWORD is required}"

echo "==> Obtaining admin token..."
TOKEN=$(curl -sf -X POST "${KC_URL}/realms/master/protocol/openid-connect/token" \
  --data-urlencode "grant_type=password" \
  --data-urlencode "client_id=admin-cli" \
  --data-urlencode "username=${KC_ADMIN}" \
  --data-urlencode "password=${KC_PASS}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

if [ -z "$TOKEN" ]; then
  echo "ERROR: Failed to get admin token" >&2
  exit 1
fi
echo "==> Token obtained."

# ─── Step 1: Register custom attributes in User Profile (KC26 requirement) ───
# Without this, Keycloak 26 silently discards custom attributes on user PUT.

echo "==> Registering custom attributes in User Profile..."
PROFILE_URL="${KC_URL}/admin/realms/${KC_REALM}/users/profile"

CURRENT_PROFILE=$(curl -sf -H "Authorization: Bearer ${TOKEN}" "${PROFILE_URL}")

# Attributes to register: name, displayName
CUSTOM_ATTRS='[
  {"name": "tenant_id",        "displayName": "Tenant ID"},
  {"name": "tenant",           "displayName": "Tenant (legacy)"},
  {"name": "plan",             "displayName": "Plan"},
  {"name": "max_users",        "displayName": "Max Users"},
  {"name": "max_robots",       "displayName": "Max Robots"},
  {"name": "max_sensors",      "displayName": "Max Sensors"},
  {"name": "activation_code",  "displayName": "Activation Code"},
  {"name": "created_by",       "displayName": "Created By"},
  {"name": "is_owner",         "displayName": "Is Owner"}
]'

UPDATED_PROFILE=$(python3 -c "
import sys, json

profile = json.loads(sys.argv[1])
new_attrs = json.loads(sys.argv[2])
existing_names = {a['name'] for a in profile.get('attributes', [])}
added = 0

for attr in new_attrs:
    if attr['name'] not in existing_names:
        profile['attributes'].append({
            'name': attr['name'],
            'displayName': attr['displayName'],
            'permissions': {'view': ['admin'], 'edit': ['admin']},
            'validations': {'multivalued': {'max': '1'}},
        })
        added += 1
        print(f'  + {attr[\"name\"]}', file=sys.stderr)
    else:
        print(f'  = {attr[\"name\"]} (already registered)', file=sys.stderr)

if added == 0:
    print('__SKIP__')
else:
    print(json.dumps(profile))
" "$CURRENT_PROFILE" "$CUSTOM_ATTRS" 2>&1)

# Separate stderr (status lines) from stdout (JSON or __SKIP__)
PROFILE_JSON=$(echo "$UPDATED_PROFILE" | grep -v '^\s*[+=]' | grep -v '^$')
STATUS_LINES=$(echo "$UPDATED_PROFILE" | grep '^\s*[+=]' || true)

if [ -n "$STATUS_LINES" ]; then
  echo "$STATUS_LINES"
fi

if [ "$PROFILE_JSON" = "__SKIP__" ]; then
  echo "==> All attributes already registered in User Profile."
else
  HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" -X PUT \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    "${PROFILE_URL}" \
    -d "$PROFILE_JSON")

  if [ "$HTTP_CODE" = "200" ]; then
    echo "==> User Profile updated successfully."
  else
    echo "ERROR: Failed to update User Profile (HTTP ${HTTP_CODE})" >&2
    exit 1
  fi
fi

# ─── Step 2: Create tenant_id mapper on nekazari-frontend client ─────────────

echo "==> Looking up client '${KC_CLIENT}'..."
CLIENT_ID=$(curl -sf -H "Authorization: Bearer ${TOKEN}" \
  "${KC_URL}/admin/realms/${KC_REALM}/clients?clientId=${KC_CLIENT}" \
  | python3 -c "import sys,json; clients=json.load(sys.stdin); print(clients[0]['id'] if clients else '')")

if [ -z "$CLIENT_ID" ]; then
  echo "ERROR: Client '${KC_CLIENT}' not found in realm '${KC_REALM}'" >&2
  exit 1
fi
echo "==> Client UUID: ${CLIENT_ID}"

MAPPER_NAME="tenant_id"
echo "==> Checking if mapper '${MAPPER_NAME}' already exists..."
EXISTING=$(curl -sf -H "Authorization: Bearer ${TOKEN}" \
  "${KC_URL}/admin/realms/${KC_REALM}/clients/${CLIENT_ID}/protocol-mappers/models" \
  | python3 -c "
import sys, json
mappers = json.load(sys.stdin)
for m in mappers:
    if m.get('name') == '${MAPPER_NAME}':
        print(m['id'])
        break
" 2>/dev/null || true)

if [ -n "$EXISTING" ]; then
  echo "==> Mapper '${MAPPER_NAME}' already exists (id: ${EXISTING}). Skipping creation."
else
  echo "==> Creating mapper '${MAPPER_NAME}'..."
  HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" -X POST \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    "${KC_URL}/admin/realms/${KC_REALM}/clients/${CLIENT_ID}/protocol-mappers/models" \
    -d '{
      "name": "tenant_id",
      "protocol": "openid-connect",
      "protocolMapper": "oidc-usermodel-attribute-mapper",
      "config": {
        "user.attribute": "tenant_id",
        "claim.name": "tenant_id",
        "jsonType.label": "String",
        "id.token.claim": "true",
        "access.token.claim": "true",
        "userinfo.token.claim": "true",
        "multivalued": "false",
        "aggregate.attrs": "false"
      }
    }')

  if [ "$HTTP_CODE" = "201" ]; then
    echo "==> Mapper '${MAPPER_NAME}' created successfully."
  else
    echo "ERROR: Failed to create mapper (HTTP ${HTTP_CODE})" >&2
    exit 1
  fi
fi

echo "==> Keycloak setup complete."
