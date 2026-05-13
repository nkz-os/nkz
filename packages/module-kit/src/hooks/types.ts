/** Authenticated user info exposed to modules */
export interface AuthInfo {
  user: { id: string; email: string; name: string } | null;
  tenantId: string | null;
  tenantName: string | null;
  roles: string[];
  isAuthenticated: boolean;
}

/** Plan tier names (mirrors services/common/tier_quotas.py) */
export type PlanTier = 'basic' | 'pro' | 'premium' | 'enterprise';

/** What `useAuth()` returns */
export interface UseAuthReturn extends AuthInfo {
  hasRole(role: string): boolean;
  hasPlan(plan: PlanTier): boolean;
}

/** What `useI18n()` returns */
export interface UseI18nReturn {
  t(key: string, vars?: Record<string, unknown>): string;
  lang: string;
  setLang(lang: string): void;
}

/** What `usePlatformEvents()` returns */
export interface UsePlatformEventsReturn {
  emit(event: string, payload: unknown): void;
  on(event: string, handler: (payload: unknown) => void): () => void;
}
