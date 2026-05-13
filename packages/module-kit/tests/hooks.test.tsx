import React, { ReactNode } from 'react';
import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { NKZContext, type NKZRuntime } from '../src/runtime/NKZContext';
import { useAuth } from '../src/hooks/useAuth';
import { useI18n } from '../src/hooks/useI18n';
import { usePlatformEvents } from '../src/hooks/usePlatformEvents';

const fixture: NKZRuntime = {
  moduleId: 'test',
  auth: {
    user: { id: 'u1', email: 'a@b.com', name: 'Alice' },
    tenantId: 't1',
    tenantName: 'Acme',
    roles: ['Farmer', 'TenantAdmin'],
    isAuthenticated: true,
    hasRole: (r) => ['Farmer', 'TenantAdmin'].includes(r),
    hasPlan: (p) => p === 'basic' || p === 'pro',
  },
  i18n: { t: (k) => k, lang: 'en', setLang: () => {} },
  events: { emit: () => {}, on: () => () => {} },
};

const wrapper = ({ children }: { children: ReactNode }) => (
  <NKZContext.Provider value={fixture}>{children}</NKZContext.Provider>
);

describe('useAuth', () => {
  it('returns the runtime auth slice', () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.user?.email).toBe('a@b.com');
    expect(result.current.tenantId).toBe('t1');
    expect(result.current.isAuthenticated).toBe(true);
  });

  it('exposes hasRole', () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.hasRole('Farmer')).toBe(true);
    expect(result.current.hasRole('Admin')).toBe(false);
  });

  it('exposes hasPlan', () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.hasPlan('basic')).toBe(true);
    expect(result.current.hasPlan('enterprise')).toBe(false);
  });

  it('throws a helpful error when no provider is present', () => {
    expect(() => renderHook(() => useAuth())).toThrow(/No NKZProvider/);
  });
});

describe('useI18n', () => {
  it('returns the runtime i18n slice', () => {
    const { result } = renderHook(() => useI18n(), { wrapper });
    expect(result.current.lang).toBe('en');
    expect(result.current.t('hello')).toBe('hello');
  });

  it('interpolates variables via the runtime t()', () => {
    const customFixture: NKZRuntime = {
      ...fixture,
      i18n: {
        t: (k, vars) => (vars?.name ? `${k} ${String(vars.name)}` : k),
        lang: 'en',
        setLang: () => {},
      },
    };
    const customWrapper = ({ children }: { children: ReactNode }) => (
      <NKZContext.Provider value={customFixture}>{children}</NKZContext.Provider>
    );
    const { result } = renderHook(() => useI18n(), { wrapper: customWrapper });
    expect(result.current.t('greet', { name: 'Alice' })).toBe('greet Alice');
  });
});

describe('usePlatformEvents (namespaced)', () => {
  it('rejects emit with a colon in the event name', () => {
    const { result } = renderHook(() => usePlatformEvents(), { wrapper });
    expect(() => result.current.emit('auth:logout', null)).toThrow(/colon/);
  });

  it('routes emit through the runtime with namespaced name', () => {
    const seen: Array<{ ev: string; payload: unknown }> = [];
    const customFixture: NKZRuntime = {
      ...fixture,
      moduleId: 'soil-health',
      events: {
        emit: (ev, payload) => seen.push({ ev, payload }),
        on: () => () => {},
      },
    };
    const customWrapper = ({ children }: { children: ReactNode }) => (
      <NKZContext.Provider value={customFixture}>{children}</NKZContext.Provider>
    );
    const { result } = renderHook(() => usePlatformEvents(), { wrapper: customWrapper });
    result.current.emit('analysis-complete', { id: 'x' });
    expect(seen).toEqual([{ ev: 'module:soil-health:analysis-complete', payload: { id: 'x' } }]);
  });

  it('forwards on() to the runtime unchanged', () => {
    const subs: Array<{ ev: string }> = [];
    const customFixture: NKZRuntime = {
      ...fixture,
      events: {
        emit: () => {},
        on: (ev) => {
          subs.push({ ev });
          return () => {};
        },
      },
    };
    const customWrapper = ({ children }: { children: ReactNode }) => (
      <NKZContext.Provider value={customFixture}>{children}</NKZContext.Provider>
    );
    const { result } = renderHook(() => usePlatformEvents(), { wrapper: customWrapper });
    result.current.on('parcel:selected', () => {});
    expect(subs[0].ev).toBe('parcel:selected');
  });
});
