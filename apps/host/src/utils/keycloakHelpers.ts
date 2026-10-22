// =============================================================================
// Keycloak Auth Helper Functions
// Pure utility functions extracted from KeycloakAuthContext and DashboardImproved
// =============================================================================

import type { KeycloakUser } from '@/context/KeycloakAuthContext';

export interface ExpirationInfo {
  days_remaining: number | null;
  expires_at: string | null;
  plan: string | null;
}

export type ExpirationAlertLevel = 'critical' | 'warning' | 'info';

export interface ExpirationAlert {
  level: ExpirationAlertLevel;
  i18nKey: string;
  i18nValues?: Record<string, unknown>;
  color: string;
}

export interface ExpirationAlertContext {
  roles?: string[];
  tenantId?: string | null;
}

const PLATFORM_TENANT = 'platform';

function isPlatformAdminContext(context?: ExpirationAlertContext): boolean {
  if (!context) return false;
  if (context.tenantId && context.tenantId.toLowerCase() === PLATFORM_TENANT) return true;
  if (context.roles && context.roles.includes('PlatformAdmin')) return true;
  return false;
}

/**
 * Extract user data from a JWT token and optional Keycloak profile.
 * Decodes the token payload, extracts roles from realm_access, resource_access,
 * and root-level roles claims, and resolves tenant from multiple claim formats.
 */
export function extractUserFromToken(token: string, keycloakProfile?: any): KeycloakUser & { tenant: string } {
  const decoded = JSON.parse(atob(token.split('.')[1]));

  const realmRoles = decoded.realm_access?.roles || [];
  const resourceRoles = Object.values(decoded.resource_access || {}).flatMap((r: any) => r.roles || []);
  const rootRoles = decoded.roles || decoded['roles'] || [];
  const roles = [...new Set([...realmRoles, ...resourceRoles, ...rootRoles])];

  // Extract tenant - try multiple claim names and fallback to groups
  let tokenTenant = decoded['tenant-id'] || decoded['tenant_id'] || decoded.tenantId || decoded.tenant || '';

  // Fallback: Extract from groups (same logic as backend)
  if (!tokenTenant && decoded.groups && Array.isArray(decoded.groups) && decoded.groups.length > 0) {
    const firstGroup = decoded.groups[0];
    tokenTenant = firstGroup.startsWith('/') ? firstGroup.substring(1) : firstGroup;
  }

  // If tenant is array (from Keycloak group mapper), take first element
  tokenTenant = Array.isArray(tokenTenant) ? (tokenTenant[0] || '') : tokenTenant;

  const firstName = keycloakProfile?.firstName || decoded.given_name || '';
  const lastName = keycloakProfile?.lastName || decoded.family_name || '';

  return {
    id: decoded.sub || '',
    username: keycloakProfile?.username || decoded.preferred_username || '',
    email: keycloakProfile?.email || decoded.email || '',
    firstName,
    lastName,
    name: `${firstName} ${lastName}`.trim(),
    tenant: tokenTenant,
    roles,
  };
}

/**
 * Determine expiration notification urgency from expiration info.
 * Returns null if no alert should be shown (>30 days remaining, missing info,
 * or the caller is a platform-internal user/tenant).
 *
 * The returned alert contains an i18n key + values so callers MUST translate
 * via react-i18next. No user-facing copy lives in this helper.
 */
export function getExpirationAlert(
  expirationInfo: ExpirationInfo | null,
  context?: ExpirationAlertContext,
): ExpirationAlert | null {
  if (isPlatformAdminContext(context)) {
    return null;
  }

  if (!expirationInfo || expirationInfo.days_remaining === null || expirationInfo.days_remaining === undefined) {
    return null;
  }

  const days = expirationInfo.days_remaining;

  if (days <= 0) {
    return {
      level: 'critical',
      i18nKey: 'dashboard.alert_expired',
      color: 'bg-red-50 border-red-200 text-red-800',
    };
  } else if (days <= 1) {
    return {
      level: 'critical',
      i18nKey: 'dashboard.alert_expires_tomorrow',
      color: 'bg-red-50 border-red-200 text-red-800',
    };
  } else if (days <= 7) {
    return {
      level: 'warning',
      i18nKey: 'dashboard.alert_expires_urgent',
      i18nValues: { days },
      color: 'bg-orange-50 border-orange-200 text-orange-800',
    };
  } else if (days <= 15) {
    return {
      level: 'info',
      i18nKey: 'dashboard.alert_expires_info',
      i18nValues: { days },
      color: 'bg-yellow-50 border-yellow-200 text-yellow-800',
    };
  } else if (days <= 30) {
    return {
      level: 'info',
      i18nKey: 'dashboard.alert_expires_soft',
      i18nValues: { days },
      color: 'bg-blue-50 border-blue-200 text-blue-800',
    };
  }

  return null;
}

/**
 * Format an error for logging. Handles Error objects, plain objects, and primitives.
 */
export function formatAuthError(e: unknown): string {
  try {
    if (e instanceof Error) return `${e.name}: ${e.message}`;
    if (typeof e === 'object') return JSON.stringify(e);
    return String(e);
  } catch {
    return 'Uncapturable error';
  }
}
