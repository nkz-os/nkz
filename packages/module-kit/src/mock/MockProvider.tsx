import React, { type ReactNode, useMemo, useState } from 'react';
import { NKZContext, type NKZRuntime } from '../runtime/NKZContext';
import type { PlanTier } from '../hooks/types';
import type { MockFixtures } from './types';
import { DEFAULT_MOCK_FIXTURES } from './fixtures';

const PLAN_ORDER: Record<PlanTier, number> = { basic: 0, pro: 1, premium: 2, enterprise: 3 };

interface MockProviderProps {
  fixtures?: Partial<MockFixtures>;
  children: ReactNode;
}

/**
 * In-memory provider used by `nkz dev` and unit tests. The hooks behave
 * identically to production but read from local fixtures.
 *
 * @example
 *   <MockProvider fixtures={{ moduleId: 'soil-health', tenantPlan: 'enterprise' }}>
 *     <App />
 *   </MockProvider>
 */
export function MockProvider({ fixtures = {}, children }: MockProviderProps): React.ReactElement {
  const merged = { ...DEFAULT_MOCK_FIXTURES, ...fixtures };
  const auth = merged.auth ?? DEFAULT_MOCK_FIXTURES.auth;
  const tenantPlan = merged.tenantPlan ?? DEFAULT_MOCK_FIXTURES.tenantPlan;
  const i18n = merged.i18n ?? DEFAULT_MOCK_FIXTURES.i18n;

  const [lang, setLang] = useState<string>(i18n.lang ?? 'en');
  const [subscribers] = useState(() => new Map<string, Set<(p: unknown) => void>>());

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
    };
  }, [merged.moduleId, auth, tenantPlan, lang, i18n, subscribers]);

  return <NKZContext.Provider value={runtime}>{children}</NKZContext.Provider>;
}
