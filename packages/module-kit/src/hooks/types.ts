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

/** Minimal NGSI-LD entity shape — modules may extend with their attribute set */
export interface NgsiLdEntity {
  id: string;
  type: string;
  [attr: string]: unknown;
}

/** Result shape returned by `useEntity` / `useEntities` (mirrors TanStack Query useQuery) */
export interface QueryResult<T> {
  data?: T;
  isLoading: boolean;
  isFetching: boolean;
  error: Error | null;
  refetch: () => Promise<unknown>;
}

/** Transport layer the runtime injects — same shape for real and mock */
export interface OrionTransport {
  getEntity(id: string, type?: string): Promise<NgsiLdEntity>;
  listEntities(type: string, opts?: { q?: string; limit?: number; offset?: number }): Promise<NgsiLdEntity[]>;
  createEntity(entity: NgsiLdEntity): Promise<void>;
  updateEntity(id: string, attrs: Record<string, unknown>): Promise<void>;
  deleteEntity(id: string): Promise<void>;
}

/** Transport for the module's own backend (basePath from defineModule({ api })) */
export interface ModuleAPITransport {
  basePath: string | null;
  fetch<T = unknown>(path: string, init?: RequestInit): Promise<T>;
}

/** Transport for file storage scoped to `tenants/<tenant>/modules/<module>/<path>` */
export interface FilesTransport {
  upload(file: Blob, path: string): Promise<{ url: string }>;
  getUrl(path: string, opts?: { expiresInSeconds?: number }): Promise<string>;
  list(prefix: string): Promise<string[]>;
}

/** A single point on a time series — ISO timestamp + numeric value */
export interface TimeseriesPoint {
  timestamp: string;
  value: number;
}

/** Query parameters for useTimeseries */
export interface TimeseriesQuery {
  entityId: string;
  attribute: string;
  from: Date | string;
  to: Date | string;
  /** Target number of buckets (server quantises to a standard interval). Default 1000. */
  resolution?: number;
}

/** Transport that the runtime injects — same shape for real and mock */
export interface TimeseriesTransport {
  query(opts: TimeseriesQuery): Promise<TimeseriesPoint[]>;
}
