// =============================================================================
// Cookie consent — preferences storage & banner orchestration (LSSI / RGPD UX)
// =============================================================================

import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  useEffect,
} from 'react';

/** Bump when cookie policy text / categories change materially (re-prompt users). */
export const COOKIE_POLICY_NOTICE_VERSION = 1;

const STORAGE_KEY = 'nkz_cookie_consent_v2';
const LEGACY_CONSENT_KEY = 'cookieConsent';

export interface CookiePreferences {
  /** Always-on technical cookies; exposed for UI completeness */
  necessary: true;
  /** Optional measurement / first-party analytics (loaded only if true). */
  analytics: boolean;
}

interface StoredConsent {
  policyNoticeVersion: number;
  analytics: boolean;
  updatedAt: string;
}

export interface CookieConsentContextValue {
  /** User completed first-layer choice for current policy version */
  hasAnswered: boolean;
  preferences: CookiePreferences;
  analyticsEnabled: boolean;
  /** Open banner / configuration panel (revocation & updates). */
  openPreferences: () => void;
  closePreferences: () => void;
  preferencesOpen: boolean;
  acceptAll: () => void;
  rejectOptional: () => void;
  saveCustom: (analytics: boolean) => void;
}

function parseStored(raw: string | null): StoredConsent | null {
  if (!raw) return null;
  try {
    const o = JSON.parse(raw) as Partial<StoredConsent>;
    if (
      typeof o.policyNoticeVersion !== 'number' ||
      typeof o.analytics !== 'boolean' ||
      typeof o.updatedAt !== 'string'
    ) {
      return null;
    }
    return o as StoredConsent;
  } catch {
    return null;
  }
}

function migrateLegacy(): StoredConsent | null {
  try {
    const legacy = localStorage.getItem(LEGACY_CONSENT_KEY);
    if (legacy !== 'accepted' && legacy !== 'rejected') return null;
    // Conservative: legacy "accept" did not explicitly opt in to analytics category.
    const migrated: StoredConsent = {
      policyNoticeVersion: COOKIE_POLICY_NOTICE_VERSION,
      analytics: false,
      updatedAt: new Date().toISOString(),
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(migrated));
    return migrated;
  } catch {
    return null;
  }
}

const CookieConsentContext = createContext<CookieConsentContextValue | null>(null);

export const CookieConsentProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const [stored, setStored] = useState<StoredConsent | null>(() => {
    if (typeof window === 'undefined') return null;
    const fromNew = parseStored(localStorage.getItem(STORAGE_KEY));
    if (fromNew) return fromNew;
    return migrateLegacy();
  });

  const [preferencesOpen, setPreferencesOpen] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const fromNew = parseStored(localStorage.getItem(STORAGE_KEY));
    if (fromNew) {
      setStored(fromNew);
      return;
    }
    const migrated = migrateLegacy();
    if (migrated) setStored(migrated);
  }, []);

  const persist = useCallback((analytics: boolean) => {
    const next: StoredConsent = {
      policyNoticeVersion: COOKIE_POLICY_NOTICE_VERSION,
      analytics,
      updatedAt: new Date().toISOString(),
    };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* quota / private mode */
    }
    setStored(next);
  }, []);

  const hasAnswered = Boolean(
    stored && stored.policyNoticeVersion === COOKIE_POLICY_NOTICE_VERSION
  );

  const preferences: CookiePreferences = useMemo(
    () => ({
      necessary: true,
      analytics: stored?.analytics ?? false,
    }),
    [stored]
  );

  const openPreferences = useCallback(() => setPreferencesOpen(true), []);
  const closePreferences = useCallback(() => setPreferencesOpen(false), []);

  const acceptAll = useCallback(() => {
    persist(true);
    setPreferencesOpen(false);
  }, [persist]);

  const rejectOptional = useCallback(() => {
    persist(false);
    setPreferencesOpen(false);
  }, [persist]);

  const saveCustom = useCallback(
    (analytics: boolean) => {
      persist(analytics);
      setPreferencesOpen(false);
    },
    [persist]
  );

  const value = useMemo<CookieConsentContextValue>(
    () => ({
      hasAnswered,
      preferences,
      analyticsEnabled: preferences.analytics,
      openPreferences,
      closePreferences,
      preferencesOpen,
      acceptAll,
      rejectOptional,
      saveCustom,
    }),
    [
      hasAnswered,
      preferences,
      preferencesOpen,
      openPreferences,
      closePreferences,
      acceptAll,
      rejectOptional,
      saveCustom,
    ]
  );

  return (
    <CookieConsentContext.Provider value={value}>{children}</CookieConsentContext.Provider>
  );
};

export function useCookieConsent(): CookieConsentContextValue {
  const ctx = useContext(CookieConsentContext);
  if (!ctx) {
    throw new Error('useCookieConsent must be used within CookieConsentProvider');
  }
  return ctx;
}

/** Safe hook for optional surfaces (e.g. landings) if provider order changes during tests */
export function useCookieConsentOptional(): CookieConsentContextValue | null {
  return useContext(CookieConsentContext);
}
