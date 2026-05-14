import React, { type ReactNode, useMemo, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { NKZContext, type NKZRuntime } from '../runtime/NKZContext';
import type { PlanTier, NgsiLdEntity } from '../hooks/types';
import type { MockFixtures } from './types';
import { DEFAULT_MOCK_FIXTURES } from './fixtures';
import { OrionMockStore, ModuleApiMockStore, FilesMockStore } from './orionStore';

const PLAN_ORDER: Record<PlanTier, number> = { basic: 0, pro: 1, premium: 2, enterprise: 3 };

interface MockProviderProps {
  fixtures?: Partial<MockFixtures> & { entities?: NgsiLdEntity[] };
  children: ReactNode;
}

/**
 * In-memory provider used by `nkz dev` and unit tests. The hooks behave
 * identically to production but read from local fixtures.
 *
 * Wraps children in a `<QueryClientProvider>` so data hooks work out of the box.
 *
 * @example
 *   <MockProvider fixtures={{ moduleId: 'soil-health', entities: [...] }}>
 *     <App />
 *   </MockProvider>
 */
export function MockProvider({ fixtures = {}, children }: MockProviderProps): React.ReactElement {
  const merged = { ...DEFAULT_MOCK_FIXTURES, ...fixtures };
  const auth = merged.auth ?? DEFAULT_MOCK_FIXTURES.auth;
  const tenantPlan = merged.tenantPlan ?? DEFAULT_MOCK_FIXTURES.tenantPlan;
  const i18n = merged.i18n ?? DEFAULT_MOCK_FIXTURES.i18n;
  const seedEntities = (fixtures as { entities?: NgsiLdEntity[] }).entities ?? [];

  const [lang, setLang] = useState<string>(i18n.lang ?? 'en');
  const [subscribers] = useState(() => new Map<string, Set<(p: unknown) => void>>());
  const [orionStore] = useState(() => {
    const s = new OrionMockStore();
    s.seed(seedEntities);
    return s;
  });
  const [moduleApiStore] = useState(() => new ModuleApiMockStore());
  const [filesStore] = useState(() => new FilesMockStore());
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } }));

  const runtime = useMemo<NKZRuntime>(() => {
    return {
      moduleId: merged.moduleId,
      auth: {
        ...auth,
        hasRole: (r) => auth.roles.includes(r),
        hasPlan: (p) => PLAN_ORDER[tenantPlan] >= PLAN_ORDER[p],
      },
      i18n: {
        t: (key) => i18n.translations?.[lang]?.[key] ?? key,
        lang,
        setLang,
      },
      events: {
        emit: (ev, payload) => {
          const handlers = subscribers.get(ev);
          handlers?.forEach((h) => h(payload));
        },
        on: (ev, handler) => {
          const set = subscribers.get(ev) ?? new Set();
          set.add(handler);
          subscribers.set(ev, set);
          return () => {
            set.delete(handler);
          };
        },
      },
      orion: orionStore,
      moduleApi: moduleApiStore,
      files: filesStore,
    };
  }, [merged.moduleId, auth, tenantPlan, lang, i18n, subscribers, orionStore, moduleApiStore, filesStore]);

  return (
    <QueryClientProvider client={client}>
      <NKZContext.Provider value={runtime}>{children}</NKZContext.Provider>
    </QueryClientProvider>
  );
}
