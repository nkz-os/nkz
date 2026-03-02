#!/bin/sh
# =============================================================================
# Nekazari Frontend - Runtime Config Entrypoint
# =============================================================================
# Este script se ejecuta al arrancar el contenedor.
# Lee las variables de entorno e inyecta window.__ENV__ y las dependencias
# directamente en index.html para que el frontend React sea portable.
# =============================================================================

set -e

# Directorio donde se sirven los archivos estáticos
NGINX_HTML_DIR="/usr/share/nginx/html"
INDEX_HTML="${NGINX_HTML_DIR}/index.html"

echo "🚀 Nekazari Frontend - Generando configuración de runtime..."

# =============================================================================
# Variables de entorno con valores por defecto
# =============================================================================
VITE_API_URL="${VITE_API_URL:-https://nkz.robotika.cloud}"
VITE_KEYCLOAK_URL="${VITE_KEYCLOAK_URL:-https://auth.robotika.cloud}"
VITE_KEYCLOAK_REALM="${VITE_KEYCLOAK_REALM:-nekazari}"
VITE_KEYCLOAK_CLIENT_ID="${VITE_KEYCLOAK_CLIENT_ID:-nekazari-frontend}"
VITE_CESIUM_TOKEN="${VITE_CESIUM_TOKEN:-}"
VITE_ENABLE_NDVI="${VITE_ENABLE_NDVI:-true}"
VITE_ENABLE_WEATHER="${VITE_ENABLE_WEATHER:-true}"
VITE_ENABLE_RISK="${VITE_ENABLE_RISK:-true}"
VITE_MODULES_CDN_URL="${VITE_MODULES_CDN_URL:-/modules}"

# =============================================================================
# Bloques de inyección
# =============================================================================
# Inyectar Cesium solo si el directorio existe en el contenedor (evitar 404)
CESIUM_SCRIPT=""
if [ -d "${NGINX_HTML_DIR}/cesium" ]; then
    CESIUM_SCRIPT="<script src=\"/cesium/Cesium.js\"></script>"
fi

ENV_SCRIPT="<script id=\"runtime-config\">
window.__ENV__ = {
  VITE_API_URL: \"${VITE_API_URL}\",
  VITE_KEYCLOAK_URL: \"${VITE_KEYCLOAK_URL}\",
  VITE_KEYCLOAK_REALM: \"${VITE_KEYCLOAK_REALM}\",
  VITE_KEYCLOAK_CLIENT_ID: \"${VITE_KEYCLOAK_CLIENT_ID}\",
  VITE_CESIUM_TOKEN: \"${VITE_CESIUM_TOKEN}\",
  VITE_ENABLE_NDVI: ${VITE_ENABLE_NDVI},
  VITE_ENABLE_WEATHER: ${VITE_ENABLE_WEATHER},
  VITE_ENABLE_RISK: ${VITE_ENABLE_RISK},
  VITE_MODULES_CDN_URL: \"${VITE_MODULES_CDN_URL}\"
};
console.log('[Nekazari] Runtime config injected');
</script>"

# =============================================================================
# Inyectar en index.html antes de </head>
# =============================================================================
if [ -f "${INDEX_HTML}" ]; then
    # Limpieza de inyecciones previas para asegurar idempotencia
    sed -i '/<script id="runtime-config">/,/<\/script>/d' "${INDEX_HTML}"
    sed -i '/<script src="\/cesium\/Cesium.js"><\/script>/d' "${INDEX_HTML}"
    
    # Inserción
    sed -i "s|<\/head>|${CESIUM_SCRIPT}${ENV_SCRIPT}<\/head>|" "${INDEX_HTML}"
    echo "✅ Configuración y dependencias inyectadas en index.html"
else
    echo "⚠️  Archivo index.html no encontrado"
fi

echo "   - API URL: ${VITE_API_URL}"
echo "   - Keycloak URL: ${VITE_KEYCLOAK_URL}"

# =============================================================================
# Iniciar Nginx
# =============================================================================
echo "🌐 Iniciando Nginx..."
exec nginx -g 'daemon off;'
