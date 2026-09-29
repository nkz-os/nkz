import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

// --- mocks -------------------------------------------------------------------

type AuthState = {
  isAuthenticated: boolean;
  sessionReady: boolean;
  tenantId: string;
  getToken: () => string | undefined;
};
let auth: AuthState;
vi.mock('@/context/KeycloakAuthContext', () => ({
  useAuth: () => auth,
}));

// Per-tenant /api/modules/me responses; a test can hold one back with a deferred.
const modulesMeCalls: string[] = [];
let modulesMe: (tenant: string) => Promise<unknown[]>;
vi.mock('@nekazari/sdk', () => ({
  NekazariClient: class {
    private tenant: () => string;
    constructor(opts: { getTenantId: () => string }) {
      this.tenant = opts.getTenantId;
    }
    async get(path: string) {
      if (path === '/api/modules/me') {
        modulesMeCalls.push(this.tenant());
        return modulesMe(this.tenant());
      }
      return {};
    }
  },
}));

vi.mock('@module-federation/runtime', () => ({
  registerRemotes: vi.fn(),
  loadRemote: vi.fn(async () => ({
    default: { slots: { 'layer-toggle': [{ id: 'lidar-toggle', component: 'T', priority: 1 }] } },
  })),
}));

vi.mock('@nekazari/module-kit', () => ({
  toNKZRegistration: (def: { slots?: unknown }) => ({ viewerSlots: def.slots }),
}));

vi.mock('@/config/environment', () => ({
  getConfig: () => ({ api: { baseUrl: 'http://api.test' } }),
}));

vi.mock('@/modules/registry', () => ({ LOCAL_MODULE_REGISTRY: {} }));

import { ModuleProvider, useModules } from '../ModuleContext';

const remote = (id: string) => ({
  id,
  name: id,
  displayName: id,
  routePath: `/${id}`,
  remoteEntry: `/modules/${id}/mf-manifest.json`,
});

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ModuleProvider>{children}</ModuleProvider>
);

beforeEach(() => {
  modulesMeCalls.length = 0;
  modulesMe = async () => [remote('lidar')];
  auth = {
    isAuthenticated: true,
    sessionReady: true,
    tenantId: 'tenant-a',
    getToken: () => 'tok',
  };
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, headers: new Headers() })));
});

// --- tests -------------------------------------------------------------------

describe('ModuleProvider', () => {
  it('does not refetch on unrelated auth re-renders and keeps preloaded slots', async () => {
    const { result, rerender } = renderHook(() => useModules(), { wrapper });
    await waitFor(() => expect(result.current.modules[0]?.viewerSlots).toBeDefined());

    auth = { ...auth, getToken: () => 'tok' }; // new identity, as AuthProvider does
    rerender();
    await act(async () => {});

    expect(modulesMeCalls).toEqual(['tenant-a']);
    expect(result.current.modules[0].viewerSlots).toBeDefined();
  });

  it('refetches once the session cookie is ready', async () => {
    auth = { ...auth, sessionReady: false };
    const { rerender } = renderHook(() => useModules(), { wrapper });
    await waitFor(() => expect(modulesMeCalls.length).toBe(1));

    auth = { ...auth, sessionReady: true };
    rerender();
    await waitFor(() => expect(modulesMeCalls.length).toBe(2));
  });

  it('ignores a stale load that resolves after a newer one', async () => {
    let releaseStale: (v: unknown[]) => void = () => {};
    modulesMe = (tenant) =>
      tenant === 'master'
        ? new Promise((resolve) => { releaseStale = resolve; })
        : Promise.resolve([remote('lidar')]);
    auth = { ...auth, tenantId: 'master' };
    const { result, rerender } = renderHook(() => useModules(), { wrapper });
    await waitFor(() => expect(modulesMeCalls).toEqual(['master']));

    auth = { ...auth, tenantId: 'tenant-a' };
    rerender();
    await waitFor(() => expect(result.current.modules.map((m) => m.id)).toEqual(['lidar']));

    await act(async () => { releaseStale([remote('stale')]); });
    expect(result.current.modules.map((m) => m.id)).toEqual(['lidar']);
  });
});
