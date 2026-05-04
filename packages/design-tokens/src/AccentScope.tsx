import React, { createContext, useContext, useMemo, useEffect, useRef } from 'react';

export interface Accent {
  base: string;
  soft: string;
  strong: string;
}

const AccentContext = createContext<Accent | null>(null);

let accentCounter = 0;

export function AccentScope({ accent, children }: { accent: Accent; children: React.ReactNode }) {
  const idRef = useRef(`nkz-accent-${++accentCounter}`);
  const value = useMemo(() => accent, [accent.base, accent.soft, accent.strong]);

  useEffect(() => {
    const root = document.getElementById(idRef.current);
    if (!root) return;

    root.style.setProperty('--nkz-color-accent-base', accent.base);
    root.style.setProperty('--nkz-color-accent-soft', accent.soft);
    root.style.setProperty('--nkz-color-accent-strong', accent.strong);

    return () => {
      root.style.removeProperty('--nkz-color-accent-base');
      root.style.removeProperty('--nkz-color-accent-soft');
      root.style.removeProperty('--nkz-color-accent-strong');
    };
  }, [accent.base, accent.soft, accent.strong]);

  return (
    <div id={idRef.current}>
      <AccentContext.Provider value={value}>{children}</AccentContext.Provider>
    </div>
  );
}

export function useAccentContext() {
  return useContext(AccentContext);
}
