import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const slots = { 'map-layer': [{ id: 'lidar-layer', component: 'L', priority: 1 }] } as any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let modules: any[];

vi.mock('../ModuleContext', () => ({ useModules: () => ({ modules }) }));
vi.mock('../ViewerContext', () => ({
  useViewer: () => ({ selectedEntityType: null, activeLayers: new Set() }),
}));
vi.mock('../KeycloakAuthContext', () => ({ useAuth: () => ({ tenantId: 'tenant-a' }) }));
vi.mock('@/modules/registry', () => ({ getLocalModuleSlots: () => ({}), getAllLocalModuleIds: () => [] }));
vi.mock('@/components/viewer/CoreEntityTree', () => ({ default: () => null }));
vi.mock('@/components/viewer/CoreContextPanel', () => ({ default: () => null }));
vi.mock('@/components/viewer/CoreLayerToggles', () => ({ default: () => null }));

import { SlotRegistryProvider, useSlotRegistry } from '../SlotRegistry';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <SlotRegistryProvider>{children}</SlotRegistryProvider>
);

beforeEach(() => {
  const store = new Map<string, string>();
  vi.stubGlobal('sessionStorage', {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => { store.set(k, v); },
  });
  modules = [{ id: 'lidar', viewerSlots: slots, viewerDefaultActive: false }];
});

describe('SlotRegistryProvider', () => {
  it('starts a module in its declared default state', () => {
    const { result } = renderHook(() => useSlotRegistry(), { wrapper });
    expect(result.current.isModuleActive('lidar')).toBe(false);
    expect(result.current.getWidgetsForSlot('map-layer')).toHaveLength(0);
  });

  it('keeps the user choice when the module list is refreshed', () => {
    const { result, rerender } = renderHook(() => useSlotRegistry(), { wrapper });
    act(() => result.current.toggleModule('lidar'));
    expect(result.current.isModuleActive('lidar')).toBe(true);

    modules = [{ id: 'lidar', viewerSlots: slots, viewerDefaultActive: false }]; // new array
    rerender();
    expect(result.current.isModuleActive('lidar')).toBe(true);
    expect(result.current.getWidgetsForSlot('map-layer').map((w) => w.id)).toEqual(['lidar-layer']);
  });

  it('restores the session choice on remount', () => {
    const first = renderHook(() => useSlotRegistry(), { wrapper });
    act(() => first.result.current.activateModule('lidar'));
    first.unmount();

    const { result } = renderHook(() => useSlotRegistry(), { wrapper });
    expect(result.current.isModuleActive('lidar')).toBe(true);
  });

  it('never deactivates core', () => {
    const { result } = renderHook(() => useSlotRegistry(), { wrapper });
    act(() => result.current.deactivateModule('core'));
    act(() => result.current.toggleModule('core'));
    expect(result.current.isModuleActive('core')).toBe(true);
  });
});
