import React, { createContext, useContext, useMemo } from 'react';
import type { TokenProfile, TokenProfileDefinition } from './tokens.config';
import { profiles } from './tokens.config';

interface ThemeContextValue {
  profile: TokenProfile;
  theme: TokenProfileDefinition;
  setProfile: (p: TokenProfile) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({
  profile,
  children,
  onChange,
}: {
  profile: TokenProfile;
  children: React.ReactNode;
  onChange?: (p: TokenProfile) => void;
}) {
  const theme = profiles[profile];

  const value = useMemo<ThemeContextValue>(
    () => ({
      profile,
      theme,
      setProfile: (p: TokenProfile) => onChange?.(p),
    }),
    [profile, onChange],
  );

  return (
    <ThemeContext.Provider value={value}>
      {/* display:contents — the wrapper carries data-theme without participating in layout.
          Custom properties inherit through the DOM tree regardless of `display`,
          so descendants receive the profile's values exactly as before. */}
      <div data-theme={profile} style={{ display: 'contents' }}>
        {children}
      </div>
    </ThemeContext.Provider>
  );
}

export function useThemeContext() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useThemeContext must be used within <ThemeProvider>');
  return ctx;
}
