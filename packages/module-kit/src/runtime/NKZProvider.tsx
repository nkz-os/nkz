import React, { type ReactNode, useMemo, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { NKZContext, type NKZRuntime } from './NKZContext';
import type {
  AuthInfo,
  PlanTier,
  OrionTransport,
  ModuleAPITransport,
  FilesTransport,
  TimeseriesTransport,
  TimeseriesPoint,
  NgsiLdEntity,
} from '../hooks/types';
import {
  ORION_LD_PREFIX,
  orionEntityAttrsPath,
  orionEntityPath,
  orionEntitiesPath,
} from './orionPaths';

declare global {
  interface Window {
    __nekazariAuthContext?: AuthInfo;
  }
}

const PLAN_ORDER: Record<PlanTier, number> = { basic: 0, pro: 1, premium: 2, enterprise: 3 };

interface NKZProviderProps {
  /** Module id — used for event namespacing AND injected as X-Module-Id on every gateway-bound request */
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

function makeHttp(moduleId: string) {
  return async function httpJson<T>(input: string, init?: RequestInit): Promise<T> {
    const res = await fetch(input, {
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        'X-Module-Id': moduleId,
        ...(init?.headers ?? {}),
      },
      ...init,
    });
    if (!res.ok) {
      throw new Error(`[module-kit] ${init?.method ?? 'GET'} ${input} → ${res.status} ${res.statusText}`);
    }
    if (res.status === 204) return undefined as T;
    return res.json() as Promise<T>;
  };
}

function makeRealOrion(moduleId: string): OrionTransport {
  const http = makeHttp(moduleId);
  return {
    async getEntity(id, type) {
      return http<NgsiLdEntity>(orionEntityPath(id, type));
    },
    async listEntities(type, opts) {
      const sp = new URLSearchParams({ type });
      if (opts?.q) sp.set('q', opts.q);
      if (opts?.limit) sp.set('limit', String(opts.limit));
      if (opts?.offset) sp.set('offset', String(opts.offset));
      return http<NgsiLdEntity[]>(orionEntitiesPath(sp));
    },
    async createEntity(entity) {
      await http<void>(`${ORION_LD_PREFIX}/v1/entities`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/ld+json' },
        body: JSON.stringify(entity),
      });
    },
    async updateEntity(id, attrs) {
      await http<void>(orionEntityAttrsPath(id), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/ld+json' },
        body: JSON.stringify(attrs),
      });
    },
    async deleteEntity(id) {
      await http<void>(orionEntityPath(id), { method: 'DELETE' });
    },
  };
}

function makeRealModuleApi(moduleId: string, basePath: string | null): ModuleAPITransport {
  const http = makeHttp(moduleId);
  return {
    basePath,
    fetch<T = unknown>(path: string, init?: RequestInit): Promise<T> {
      if (!basePath) {
        throw new Error('[module-kit] useModuleAPI called but the module has no api.basePath in defineModule({...})');
      }
      const url = `${basePath}${path.startsWith('/') ? path : `/${path}`}`;
      return http<T>(url, init);
    },
  };
}

function makeRealTimeseries(moduleId: string): TimeseriesTransport {
  const http = makeHttp(moduleId);
  return {
    async query({ entityId, attribute, from, to, resolution }) {
      const sp = new URLSearchParams({
        attribute,
        start_time: typeof from === 'string' ? from : from.toISOString(),
        end_time: typeof to === 'string' ? to : to.toISOString(),
        format: 'json',
      });
      if (resolution !== undefined) sp.set('resolution', String(resolution));
      const resp = await http<{ data?: Array<Record<string, unknown>> }>(
        `/api/timeseries/entities/${encodeURIComponent(entityId)}/data?${sp.toString()}`,
      );
      const rows = resp?.data ?? [];
      const points: TimeseriesPoint[] = [];
      for (const row of rows) {
        const ts = row.timestamp;
        const v = row[attribute];
        if (typeof ts === 'string' && typeof v === 'number') {
          points.push({ timestamp: ts, value: v });
        }
      }
      return points;
    },
  };
}

function makeRealFiles(moduleId: string): FilesTransport {
  const http = makeHttp(moduleId);
  return {
    async upload(file, path) {
      const { url } = await http<{ url: string }>('/api/storage/presigned-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path,
          operation: 'PUT',
          contentType: file.type || 'application/octet-stream',
        }),
      });
      const putRes = await fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
        body: file,
      });
      if (!putRes.ok) {
        throw new Error(`[module-kit] upload to MinIO failed: ${putRes.status} ${putRes.statusText}`);
      }
      return { url: url.split('?')[0] };
    },
    async getUrl(path, opts) {
      const { url } = await http<{ url: string }>('/api/storage/presigned-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path,
          operation: 'GET',
          expiresInSeconds: opts?.expiresInSeconds,
        }),
      });
      return url;
    },
    async list(prefix) {
      const { items } = await http<{ items: string[] }>(
        `/api/storage/list?prefix=${encodeURIComponent(prefix)}`,
      );
      return items;
    },
  };
}

/**
 * Real-runtime provider injected by the host around each module's main +
 * slot widgets. Reads auth from `window.__nekazariAuthContext`, i18n from
 * `@nekazari/sdk`'s singleton i18n instance, and routes events through
 * `window.__NKZ__.events` if present (else a no-op).
 *
 * Every gateway-bound request adds an `X-Module-Id: <moduleId>` header so
 * the api-gateway can scope file storage and (in a later phase) enforce
 * CSP-of-data against the module's manifest.
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
      orion: makeRealOrion(moduleId),
      moduleApi: makeRealModuleApi(moduleId, apiBasePath ?? null),
      files: makeRealFiles(moduleId),
      timeseries: makeRealTimeseries(moduleId),
    };
  }, [moduleId, tenantPlan, lang, apiBasePath, authSnapshot.tenantId, authSnapshot.user?.id]);

  return (
    <QueryClientProvider client={client}>
      <NKZContext.Provider value={runtime}>{children}</NKZContext.Provider>
    </QueryClientProvider>
  );
}
