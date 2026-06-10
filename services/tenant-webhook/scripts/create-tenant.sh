#!/bin/bash
# =============================================================================
# Tenant Bootstrap Script - Automated Tenant Creation
# =============================================================================
# This script creates a complete tenant environment with proper network policies
# Usage: ./create-tenant.sh <tenant-id> [namespace]

set -euo pipefail

# Configuration
TENANT_ID="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# Deployment domain — must be set in the environment before running this script
PRODUCTION_DOMAIN="${PRODUCTION_DOMAIN:-}"
if [[ -z "${PRODUCTION_DOMAIN}" ]]; then
    echo "ERROR: PRODUCTION_DOMAIN environment variable is required." >&2
    echo "  export PRODUCTION_DOMAIN=nkz.example.com" >&2
    exit 1
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Normalize tenant_id: ensure it doesn't already have 'nekazari-' prefix
# This prevents issues like 'nekazari-nekazari-tenant-test-1'
if [[ "${TENANT_ID}" == nekazari-* ]]; then
    log_warning "Tenant ID '${TENANT_ID}' already has 'nekazari-' prefix, removing it"
    TENANT_ID="${TENANT_ID#nekazari-}"
fi
# Ensure tenant_id doesn't already have 'nekazari-tenant-' prefix
if [[ "${TENANT_ID}" == nekazari-tenant-* ]]; then
    log_warning "Tenant ID '${TENANT_ID}' already has 'nekazari-tenant-' prefix, removing it"
    TENANT_ID="${TENANT_ID#nekazari-tenant-}"
fi
# Construct namespace with proper prefix
NAMESPACE="${2:-nekazari-tenant-${TENANT_ID}}"

# Validation
if [[ -z "${TENANT_ID}" ]]; then
    log_error "Tenant ID is required"
    echo "Usage: $0 <tenant-id> [namespace]"
    echo "Example: $0 tenant-test-1"
    echo "Note: tenant-id should NOT include 'nekazari-' prefix (it will be added automatically)"
    exit 1
fi

if [[ ! "${TENANT_ID}" =~ ^[a-z0-9-]+$ ]]; then
    log_error "Tenant ID must contain only lowercase letters, numbers, and hyphens"
    exit 1
fi

# Log normalized tenant_id and namespace for debugging
log_info "Tenant ID (normalized): ${TENANT_ID}"
log_info "Namespace: ${NAMESPACE}"
log_info "Creating tenant: ${TENANT_ID} in namespace: ${NAMESPACE}"

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    log_error "kubectl is not installed or not in PATH"
    exit 1
fi

# Check if we can connect to cluster
if ! kubectl cluster-info &> /dev/null; then
    log_error "Cannot connect to Kubernetes cluster"
    exit 1
fi

# Create namespace
log_info "Creating namespace: ${NAMESPACE}"
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

# Add tenant label to namespace
kubectl label namespace "${NAMESPACE}" tenant-id="${TENANT_ID}" --overwrite

# Create tenant-specific network policies
log_info "Creating network policies for tenant: ${TENANT_ID}"

# Default deny policy for tenant namespace
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
  namespace: ${NAMESPACE}
  labels:
    tenant-id: ${TENANT_ID}
    policy-type: security
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  - Egress
EOF

# Essential services access for tenant
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: essential-services-access
  namespace: ${NAMESPACE}
  labels:
    tenant-id: ${TENANT_ID}
    policy-type: infrastructure
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  - Egress
  
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: ingress-nginx
    ports:
    - protocol: TCP
      port: 80
    - protocol: TCP
      port: 443
  
  egress:
  # DNS resolution
  - to: []
    ports:
    - protocol: UDP
      port: 53
    - protocol: TCP
      port: 53
  # PostgreSQL access (needed for database provisioning)
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: nekazari
    ports:
    - protocol: TCP
      port: 5432
  # Allow all egress to nekazari namespace (for essential services)
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: nekazari
EOF

# Per-tenant Postgres DB + user used to be provisioned here via a
# `tenant-db-provision-<id>` Job and a `<id>-secrets` Secret carrying
# `database-url`/`database-password`. Nothing in the platform reads them:
# n8n and odoo create their own per-tenant DBs (`n8n_<id>` and
# `nkz_odoo_<id>`) at module-activation time, not at tenant-creation
# time, and neither uses these credentials. The Job also failed on every
# creation because the tenant namespace cannot egress to postgres
# (NetworkPolicy mismatch). Removed 2026-05-28 to drop ~3 min from every
# tenant creation and to delete a dead component.

# Create tenant-specific service account
log_info "Creating service account for tenant: ${TENANT_ID}"
kubectl create serviceaccount "${TENANT_ID}-sa" \
    --namespace="${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -

# Create tenant-specific role binding (basic permissions)
log_info "Creating role binding for tenant: ${TENANT_ID}"
cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  namespace: ${NAMESPACE}
  name: ${TENANT_ID}-role
rules:
- apiGroups: [""]
  resources: ["pods", "services", "configmaps", "secrets"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["apps"]
  resources: ["deployments", "replicasets"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ${TENANT_ID}-rolebinding
  namespace: ${NAMESPACE}
subjects:
- kind: ServiceAccount
  name: ${TENANT_ID}-sa
  namespace: ${NAMESPACE}
roleRef:
  kind: Role
  name: ${TENANT_ID}-role
  apiGroup: rbac.authorization.k8s.io
EOF

# Create tenant-specific ingress template
log_info "Creating ingress template for tenant: ${TENANT_ID}"
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${TENANT_ID}-ingress
  namespace: ${NAMESPACE}
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "false"
    nginx.ingress.kubernetes.io/rewrite-target: /
spec:
  ingressClassName: nginx
  rules:
  - host: ${TENANT_ID}.${PRODUCTION_DOMAIN}
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: ${TENANT_ID}-frontend-service
            port:
              number: 80
EOF

# (DB provisioning Job removed — see comment above the secrets block.)

# Create tenant-specific deployment template
log_info "Creating deployment template for tenant: ${TENANT_ID}"
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${TENANT_ID}-frontend
  namespace: ${NAMESPACE}
  labels:
    app: ${TENANT_ID}-frontend
    tenant-id: ${TENANT_ID}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${TENANT_ID}-frontend
      tenant-id: ${TENANT_ID}
  template:
    metadata:
      labels:
        app: ${TENANT_ID}-frontend
        tenant-id: ${TENANT_ID}
    spec:
      serviceAccountName: ${TENANT_ID}-sa
      containers:
      - name: frontend
        image: nginx:alpine
        ports:
        - containerPort: 80
        env:
        - name: TENANT_ID
          value: ${TENANT_ID}
        volumeMounts:
        - name: tenant-config
          mountPath: /etc/nginx/conf.d
      volumes:
      - name: tenant-config
        configMap:
          name: ${TENANT_ID}-config
---
apiVersion: v1
kind: Service
metadata:
  name: ${TENANT_ID}-frontend-service
  namespace: ${NAMESPACE}
  labels:
    tenant-id: ${TENANT_ID}
spec:
  selector:
    app: ${TENANT_ID}-frontend
    tenant-id: ${TENANT_ID}
  ports:
  - port: 80
    targetPort: 80
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${TENANT_ID}-config
  namespace: ${NAMESPACE}
  labels:
    tenant-id: ${TENANT_ID}
data:
  nginx.conf: |
    server {
        listen 80;
        server_name ${TENANT_ID}.${PRODUCTION_DOMAIN};
        
        location / {
            return 200 'Tenant ${TENANT_ID} - Coming Soon';
            add_header Content-Type text/plain;
        }
    }
EOF

# Verify tenant creation
log_info "Verifying tenant creation..."
kubectl get namespace "${NAMESPACE}" -o wide
kubectl get pods -n "${NAMESPACE}" -l tenant-id="${TENANT_ID}"
kubectl get services -n "${NAMESPACE}" -l tenant-id="${TENANT_ID}"
kubectl get networkpolicies -n "${NAMESPACE}"

log_success "Tenant ${TENANT_ID} created successfully!"
log_info "Namespace: ${NAMESPACE}"
log_info "Access URL: https://${TENANT_ID}.${PRODUCTION_DOMAIN}"
log_info "Service Account: ${TENANT_ID}-sa"
log_info "Secrets: ${TENANT_ID}-secrets"

# Create cleanup script
log_info "Creating cleanup script for tenant: ${TENANT_ID}"
cat > "${PROJECT_ROOT}/scripts/cleanup-tenant-${TENANT_ID}.sh" <<EOF
#!/bin/bash
# Cleanup script for tenant: ${TENANT_ID}
# Usage: ./cleanup-tenant-${TENANT_ID}.sh

set -euo pipefail

echo "Cleaning up tenant: ${TENANT_ID}"
kubectl delete namespace "${NAMESPACE}" --ignore-not-found=true
rm -f "${PROJECT_ROOT}/scripts/cleanup-tenant-${TENANT_ID}.sh"
echo "Tenant ${TENANT_ID} cleanup completed"
EOF

chmod +x "${PROJECT_ROOT}/scripts/cleanup-tenant-${TENANT_ID}.sh"

log_success "Tenant bootstrap completed!"
log_info "To clean up this tenant, run: ./scripts/cleanup-tenant-${TENANT_ID}.sh"

# Optional: crear suscripciones de QuantumLeap de forma automática (idempotente)
ENABLE_QL_SUBSCRIPTIONS="${ENABLE_QL_SUBSCRIPTIONS:-true}"
if [ "${ENABLE_QL_SUBSCRIPTIONS}" = "true" ]; then
    log_info "Creando suscripciones de QuantumLeap para el tenant ${TENANT_ID} (idempotente)"
    if [ -x "${PROJECT_ROOT}/scripts/setup-quantumleap-subscriptions.sh" ]; then
        if "${PROJECT_ROOT}/scripts/setup-quantumleap-subscriptions.sh" "${TENANT_ID}"; then
            log_success "Suscripciones de QuantumLeap creadas/verificadas para ${TENANT_ID}"
        else
            log_warning "No se pudieron crear/verificar suscripciones de QuantumLeap para ${TENANT_ID}. Puedes reintentar manualmente: scripts/setup-quantumleap-subscriptions.sh ${TENANT_ID}"
        fi
    else
        log_warning "scripts/setup-quantumleap-subscriptions.sh no encontrado o no es ejecutable"
    fi
else
    log_info "ENABLE_QL_SUBSCRIPTIONS=false: Omitiendo creación automática de suscripciones de QuantumLeap"
fi
