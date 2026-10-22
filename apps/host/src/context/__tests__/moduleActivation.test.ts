import { describe, it, expect, vi, afterEach } from 'vitest';
import { resolveActiveModules, loadChoices, saveChoices } from '../moduleActivation';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const slots = { 'map-layer': [{ id: 'x', component: 'X', priority: 1 }] } as any;
const noLocals = new Set<string>();

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('resolveActiveModules', () => {
  it('always includes core', () => {
    expect(resolveActiveModules([], {}, noLocals).has('core')).toBe(true);
  });

  it('uses the declared default when there is no user choice', () => {
    const active = resolveActiveModules(
      [
        { id: 'lidar', viewerSlots: slots, viewerDefaultActive: false },
        { id: 'catastro-spain', viewerSlots: slots, viewerDefaultActive: true },
        { id: 'risk', viewerSlots: slots },
      ],
      {},
      noLocals,
    );
    expect(active.has('lidar')).toBe(false);
    expect(active.has('catastro-spain')).toBe(true);
    expect(active.has('risk')).toBe(true);
  });

  it('lets an explicit choice override the default both ways', () => {
    const active = resolveActiveModules(
      [
        { id: 'lidar', viewerSlots: slots, viewerDefaultActive: false },
        { id: 'risk', viewerSlots: slots },
      ],
      { lidar: true, risk: false },
      noLocals,
    );
    expect(active.has('lidar')).toBe(true);
    expect(active.has('risk')).toBe(false);
  });

  it('keeps modules without viewer slots inactive unless bundled locally', () => {
    const active = resolveActiveModules(
      [{ id: 'remote-no-slots' }, { id: 'local-mod', isLocal: true }],
      {},
      new Set(['bundled']),
    );
    expect(active.has('remote-no-slots')).toBe(false);
    expect(active.has('local-mod')).toBe(true);
  });

  it('keeps module state across module list refresh', () => {
    const choices = { lidar: true };
    const first = resolveActiveModules([{ id: 'lidar', viewerSlots: slots, viewerDefaultActive: false }], choices, noLocals);
    const second = resolveActiveModules([{ id: 'lidar', viewerSlots: slots, viewerDefaultActive: false }], choices, noLocals);
    expect([...second]).toEqual([...first]);
  });
});

describe('choices storage', () => {
  it('round-trips choices per tenant', () => {
    const store = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => { store.set(k, v); },
    });
    saveChoices('tenant-a', { lidar: true });
    expect(loadChoices('tenant-a')).toEqual({ lidar: true });
    expect(loadChoices('tenant-b')).toEqual({});
  });

  it('returns no choices when storage throws or holds garbage', () => {
    vi.stubGlobal('sessionStorage', {
      getItem: () => { throw new Error('blocked'); },
      setItem: () => { throw new Error('blocked'); },
    });
    expect(loadChoices('t')).toEqual({});
    expect(() => saveChoices('t', { a: true })).not.toThrow();

    vi.stubGlobal('sessionStorage', { getItem: () => '[1,2]', setItem: () => {} });
    expect(loadChoices('t')).toEqual({});
  });
});
