// =============================================================================
// useViewerTheme — viewer profile toggle (dark vs light glass)
// =============================================================================
// Persists viewer theme preference in localStorage. Defaults to dark ('viewer').
// The profile is typed as TokenProfile but constrained to 'viewer' | 'viewer-light'.

import { useState, useCallback, useEffect } from 'react';
import type { TokenProfile } from '@nekazari/design-tokens';

const STORAGE_KEY = 'nkz:viewer:theme';

function getStoredTheme(): TokenProfile {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'viewer-light' || stored === 'viewer') return stored;
  } catch {
    // localStorage unavailable (incognito, SSR, etc.)
  }
  return 'viewer'; // dark default
}

export function useViewerTheme() {
  const [profile, setProfile] = useState<TokenProfile>(getStoredTheme);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, profile);
    } catch {
      // Silently ignore storage errors
    }
  }, [profile]);

  const toggle = useCallback(() => {
    setProfile(p => (p === 'viewer' ? 'viewer-light' : 'viewer'));
  }, []);

  return { profile, toggle };
}
