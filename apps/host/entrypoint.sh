#!/bin/sh
set -e

RUNTIME_DIR="/runtime"
mkdir -p "${RUNTIME_DIR}"

echo "🚀 Nekazari Frontend - Generando configuración de runtime..."

VITE_API_URL="${VITE_API_URL:-}"
VITE_KEYCLOAK_URL="${VITE_KEYCLOAK_URL:-}"
VITE_KEYCLOAK_REALM="${VITE_KEYCLOAK_REALM:-nekazari}"
VITE_KEYCLOAK_CLIENT_ID="${VITE_KEYCLOAK_CLIENT_ID:-nekazari-frontend}"
VITE_CESIUM_TOKEN="${VITE_CESIUM_TOKEN:-}"
VITE_ENABLE_NDVI="${VITE_ENABLE_NDVI:-true}"
VITE_ENABLE_WEATHER="${VITE_ENABLE_WEATHER:-true}"
VITE_ENABLE_RISK="${VITE_ENABLE_RISK:-true}"
VITE_MODULES_CDN_URL="${VITE_MODULES_CDN_URL:-/modules}"
VITE_ZULIP_URL="${VITE_ZULIP_URL:-}"
COMPANY_URL="${COMPANY_URL:-}"
COMPANY_NAME="${COMPANY_NAME:-}"
SUPPORT_EMAIL="${SUPPORT_EMAIL:-}"
SALES_EMAIL="${SALES_EMAIL:-}"
PARTNERS_JSON="${PARTNERS_JSON:-}"

# Deployment origins are configuration, never defaults baked into the image.
if [ -z "${VITE_API_URL}" ] || [ -z "${VITE_KEYCLOAK_URL}" ]; then
    echo "ERROR: VITE_API_URL (public API origin) and VITE_KEYCLOAK_URL (Keycloak base URL) must be set." >&2
    exit 1
fi

cat > "${RUNTIME_DIR}/__nkz_runtime__.js" <<JSEOF
window.__ENV__ = {
  VITE_API_URL: "${VITE_API_URL}",
  VITE_KEYCLOAK_URL: "${VITE_KEYCLOAK_URL}",
  VITE_KEYCLOAK_REALM: "${VITE_KEYCLOAK_REALM}",
  VITE_KEYCLOAK_CLIENT_ID: "${VITE_KEYCLOAK_CLIENT_ID}",
  VITE_CESIUM_TOKEN: "${VITE_CESIUM_TOKEN}",
  VITE_ENABLE_NDVI: ${VITE_ENABLE_NDVI},
  VITE_ENABLE_WEATHER: ${VITE_ENABLE_WEATHER},
  VITE_ENABLE_RISK: ${VITE_ENABLE_RISK},
  VITE_MODULES_CDN_URL: "${VITE_MODULES_CDN_URL}",
  VITE_ZULIP_URL: "${VITE_ZULIP_URL}",
  COMPANY_URL: "${COMPANY_URL}",
  COMPANY_NAME: "${COMPANY_NAME}",
  SUPPORT_EMAIL: "${SUPPORT_EMAIL}",
  SALES_EMAIL: "${SALES_EMAIL}",
  PARTNERS_JSON: ${PARTNERS_JSON:-"\"\""}
};
console.log('[Nekazari] Runtime config injected');
JSEOF

if [ -d "/usr/share/nginx/html/cesium" ]; then
    echo "1" > "${RUNTIME_DIR}/__cesium__"
else
    echo "0" > "${RUNTIME_DIR}/__cesium__"
fi

echo "✅ Runtime config escrito en ${RUNTIME_DIR} (root read-only intacto)"
echo "   - API URL: ${VITE_API_URL}"
echo "🌐 Iniciando Nginx..."
exec nginx -g 'daemon off;'
