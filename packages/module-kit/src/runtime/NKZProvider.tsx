import React, { type ReactNode, useMemo, useState } from 'react';
import { NKZContext, type NKZRuntime } from './NKZContext';
import type { AuthInfo, PlanTier } from '../hooks/types';

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
  children: ReactNode;
}

/**
 * Real-runtime provider injected by the host around each module's main +
 * slot widgets. Reads auth from `window.__nekazariAuthContext`, i18n from
 * `@nekazari/sdk`'s singleton i18n instance, and routes events through
 * `window.__NKZ__.events` if present (else a no-op).
 */
export function NKZProvider({ moduleId, tenantPlan, children }: NKZProviderProps): React.ReactElement {
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

  const runtime = useMemo<NKZRuntime>(() => {
    return {
      moduleId,
      auth: {
        ...authSnapshot,
        hasRole: (r) => authSnapshot.roles.includes(r),
        hasPlan: (p) => (tenantPlan ? PLAN_ORDER[tenantPlan] >= PLAN_ORDER[p] : false),
      },
      i18n: {
        // The host's @nekazari/sdk i18n is provided as a window global at runtime.
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
    };
  }, [moduleId, tenantPlan, lang, authSnapshot.tenantId, authSnapshot.user?.id]);

  return <NKZContext.Provider value={runtime}>{children}</NKZContext.Provider>;
}
