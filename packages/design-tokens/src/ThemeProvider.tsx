import React, { createContext, useContext, useEffect, useMemo } from 'react';
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

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', profile);
  }, [profile]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useThemeContext() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useThemeContext must be used within <ThemeProvider>');
  return ctx;
}
