import React, { ReactNode } from 'react';
import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { MockProvider } from '../src/mock/MockProvider';
import { NKZProvider } from '../src/runtime/NKZProvider';
import { useAuth } from '../src/hooks/useAuth';
import { useI18n } from '../src/hooks/useI18n';
import { usePlatformEvents } from '../src/hooks/usePlatformEvents';
import type { AuthInfo } from '../src/hooks/types';

describe('mock↔real parity', () => {
  // Setup window.__nekazariAuthContext for the real provider
  (window as unknown as { __nekazariAuthContext: AuthInfo }).__nekazariAuthContext = {
    user: { id: 'r', email: 'r@x.com', name: 'R' },
    tenantId: 'rt',
    tenantName: 'RT',
    roles: ['Farmer'],
    isAuthenticated: true,
  };

  const mockW = ({ children }: { children: ReactNode }) => <MockProvider>{children}</MockProvider>;
  const realW = ({ children }: { children: ReactNode }) => (
    <NKZProvider moduleId="m">{children}</NKZProvider>
  );

  it('useAuth has the same return keys in MockProvider and NKZProvider', () => {
    const mock = renderHook(() => useAuth(), { wrapper: mockW }).result.current;
    const real = renderHook(() => useAuth(), { wrapper: realW }).result.current;
    expect(Object.keys(mock).sort()).toEqual(Object.keys(real).sort());
    expect(typeof mock.hasRole).toBe(typeof real.hasRole);
    expect(typeof mock.hasPlan).toBe(typeof real.hasPlan);
    expect(typeof mock.isAuthenticated).toBe(typeof real.isAuthenticated);
  });

  it('useI18n has the same return keys in both providers', () => {
    const mock = renderHook(() => useI18n(), { wrapper: mockW }).result.current;
    const real = renderHook(() => useI18n(), { wrapper: realW }).result.current;
    expect(Object.keys(mock).sort()).toEqual(Object.keys(real).sort());
    expect(typeof mock.t).toBe(typeof real.t);
    expect(typeof mock.setLang).toBe(typeof real.setLang);
  });

  it('usePlatformEvents has the same return keys in both providers', () => {
    const mock = renderHook(() => usePlatformEvents(), { wrapper: mockW }).result.current;
    const real = renderHook(() => usePlatformEvents(), { wrapper: realW }).result.current;
    expect(Object.keys(mock).sort()).toEqual(Object.keys(real).sort());
    expect(typeof mock.emit).toBe(typeof real.emit);
    expect(typeof mock.on).toBe(typeof real.on);
  });
});
