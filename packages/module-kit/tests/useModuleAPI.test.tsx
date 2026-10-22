import React, { ReactNode } from 'react';
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { NKZContext, type NKZRuntime } from '../src/runtime/NKZContext';
import { ModuleApiMockStore } from '../src/mock/orionStore';
import { useGet, usePost } from '../src/hooks/useModuleAPI';

function makeWrapper(runtime: NKZRuntime) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <NKZContext.Provider value={runtime}>{children}</NKZContext.Provider>
    </QueryClientProvider>
  );
}

const baseRuntime = (moduleApi: ModuleApiMockStore): NKZRuntime => ({
  moduleId: 'test',
  auth: {
    user: null,
    tenantId: null,
    tenantName: null,
    roles: [],
    isAuthenticated: false,
    hasRole: () => false,
    hasPlan: () => false,
  },
  i18n: { t: (k) => k, lang: 'en', setLang: () => {} },
  events: { emit: () => {}, on: () => () => {} },
  orion: {
    getEntity: async () => ({ id: '', type: '' }),
    listEntities: async () => [],
    createEntity: async () => {},
    updateEntity: async () => {},
    deleteEntity: async () => {},
  },
  moduleApi,
});

describe('useModuleAPI', () => {
  it('useGet calls a registered mock handler', async () => {
    const store = new ModuleApiMockStore();
    store.register('GET', '/forecast', async () => ({ temp: 22 }));
    const wrapper = makeWrapper(baseRuntime(store));
    const { result } = renderHook(() => useGet<{ temp: number }>('/forecast'), { wrapper });
    await waitFor(() => expect(result.current.data?.temp).toBe(22));
  });

  it('usePost mutates and calls the registered POST handler', async () => {
    const seen: unknown[] = [];
    const store = new ModuleApiMockStore();
    store.register('POST', '/orders', async (init) => {
      seen.push(JSON.parse(init?.body as string));
      return { ok: true };
    });
    const wrapper = makeWrapper(baseRuntime(store));
    const { result } = renderHook(() => usePost<{ ok: boolean }, { item: string }>('/orders'), { wrapper });
    const res = await result.current.mutateAsync({ item: 'banana' });
    expect(res.ok).toBe(true);
    expect(seen).toEqual([{ item: 'banana' }]);
  });
});
