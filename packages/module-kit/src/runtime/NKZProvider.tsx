import React, { type ReactNode, useMemo, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { NKZContext, type NKZRuntime } from './NKZContext';
import type {
  AuthInfo,
  PlanTier,
  OrionTransport,
  ModuleAPITransport,
  NgsiLdEntity,
} from '../hooks/types';

declare global {
  interface Window {
    __nekazariAuthContext?: AuthInfo;
  }
}

const PLAN_ORDER: Record<PlanTier, number> = { basic: 0, pro: 1, premium: 2, enterprise: 3 };

interface NKZProviderProps {
  /** Module id — used for event namespacing */
  moduleId: string;
  /** Plan currently held by the tenant (set by host; unknown in dev) */
  tenantPlan?: PlanTier;
  /** Module's own backend base path, e.g. '/api/soil-health' */
  apiBasePath?: string;
  /** Optional override of the TanStack QueryClient (defaults to an internal singleton-per-mount) */
  queryClient?: QueryClient;
  children: ReactNode;
}

const DEFAULT_QUERY_OPTIONS = {
  staleTime: 30_000,
  retry: 1,
  refetchOnWindowFocus: false,
};

function makeDefaultClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: DEFAULT_QUERY_OPTIONS } });
}

async function httpJson<T>(input: string, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    credentials: 'include',
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`[module-kit] ${init?.method ?? 'GET'} ${input} → ${res.status} ${res.statusText}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const realOrion: OrionTransport = {
  async getEntity(id, type) {
    const q = type ? `?type=${encodeURIComponent(type)}` : '';
    return httpJson<NgsiLdEntity>(`/api/ngsi-ld/v1/entities/${encodeURIComponent(id)}${q}`);
  },
  async listEntities(type, opts) {
    const sp = new URLSearchParams({ type });
    if (opts?.q) sp.set('q', opts.q);
    if (opts?.limit) sp.set('limit', String(opts.limit));
    if (opts?.offset) sp.set('offset', String(opts.offset));
    return httpJson<NgsiLdEntity[]>(`/api/ngsi-ld/v1/entities?${sp.toString()}`);
  },
  async createEntity(entity) {
    await httpJson<void>('/api/ngsi-ld/v1/entities', {
      method: 'POST',
      headers: { 'Content-Type': 'application/ld+json' },
      body: JSON.stringify(entity),
    });
  },
  async updateEntity(id, attrs) {
    await httpJson<void>(`/api/ngsi-ld/v1/entities/${encodeURIComponent(id)}/attrs`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/ld+json' },
      body: JSON.stringify(attrs),
    });
  },
  async deleteEntity(id) {
    await httpJson<void>(`/api/ngsi-ld/v1/entities/${encodeURIComponent(id)}`, { method: 'DELETE' });
  },
};

function makeRealModuleApi(basePath: string | null): ModuleAPITransport {
  return {
    basePath,
    fetch<T = unknown>(path: string, init?: RequestInit): Promise<T> {
      if (!basePath) {
        throw new Error('[module-kit] useModuleAPI called but the module has no api.basePath in defineModule({...})');
      }
      const url = `${basePath}${path.startsWith('/') ? path : `/${path}`}`;
      return httpJson<T>(url, init);
    },
  };
}

/**
 * Real-runtime provider injected by the host around each module's main +
 * slot widgets. Reads auth from `window.__nekazariAuthContext`, i18n from
 * `@nekazari/sdk`'s singleton i18n instance, and routes events through
 * `window.__NKZ__.events` if present (else a no-op).
 *
 * Wraps children in a `<QueryClientProvider>` for the data hooks.
 */
export function NKZProvider({
  moduleId,
  tenantPlan,
  apiBasePath,
  queryClient,
  children,
}: NKZProviderProps): React.ReactElement {
  const authSnapshot: AuthInfo = window.__nekazariAuthContext ?? {
    user: null,
    tenantId: null,
    tenantName: null,
    roles: [],
    isAuthenticated: false,
  };

  const [lang, setLang] = useState<string>(() => {
    return (document.documentElement.lang || navigator.language?.slice(0, 2) || 'en').toLowerCase();
  });

  const [client] = useState<QueryClient>(() => queryClient ?? makeDefaultClient());

  const runtime = useMemo<NKZRuntime>(() => {
    return {
      moduleId,
      auth: {
        ...authSnapshot,
        hasRole: (r) => authSnapshot.roles.includes(r),
        hasPlan: (p) => (tenantPlan ? PLAN_ORDER[tenantPlan] >= PLAN_ORDER[p] : false),
      },
      i18n: {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        t: (key, vars) => ((window as any).__NKZ_SDK__?.i18n?.t?.(key, vars) ?? key) as string,
        lang,
        setLang: (l) => {
          setLang(l);
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (window as any).__NKZ_SDK__?.i18n?.changeLanguage?.(l);
        },
      },
      events: {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        emit: (ev, payload) => (window as any).__NKZ__?.events?.emit?.(ev, payload),
        on: (ev, handler) => {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const off = (window as any).__NKZ__?.events?.on?.(ev, handler);
          return typeof off === 'function' ? off : () => {};
        },
      },
      orion: realOrion,
      moduleApi: makeRealModuleApi(apiBasePath ?? null),
    };
  }, [moduleId, tenantPlan, lang, apiBasePath, authSnapshot.tenantId, authSnapshot.user?.id]);

  return (
    <QueryClientProvider client={client}>
      <NKZContext.Provider value={runtime}>{children}</NKZContext.Provider>
    </QueryClientProvider>
  );
}
