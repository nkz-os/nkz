import { describe, it, expect } from 'vitest';
import { carryOverViewerSlots } from '../moduleSlotMerge';
import type { ModuleDefinition } from '../ModuleContext';

const mod = (id: string, extra: Partial<ModuleDefinition> = {}): ModuleDefinition => ({
  id,
  name: id,
  displayName: id,
  version: '1.0.0',
  routePath: `/${id}`,
  label: id,
  remoteEntry: `/modules/${id}/mf-manifest.json`,
  ...extra,
});
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const slots = { 'layer-toggle': [{ id: 'x-toggle', component: 'X', priority: 1 }] } as any;

describe('carryOverViewerSlots', () => {
  it('keeps viewerSlots across reloads', () => {
    const prev = [mod('lidar', { viewerSlots: slots })];
    const next = [mod('lidar')];
    expect(carryOverViewerSlots(prev, next)[0].viewerSlots).toBe(slots);
  });

  it('prefers fresh viewerSlots when next already has them', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const fresh = { 'map-layer': [] } as any;
    const out = carryOverViewerSlots([mod('a', { viewerSlots: slots })], [mod('a', { viewerSlots: fresh })]);
    expect(out[0].viewerSlots).toBe(fresh);
  });

  it('drops modules no longer returned', () => {
    const out = carryOverViewerSlots([mod('a', { viewerSlots: slots })], [mod('b')]);
    expect(out.map(m => m.id)).toEqual(['b']);
    expect(out[0].viewerSlots).toBeUndefined();
  });

  it('does not carry slots when the remote entry changed', () => {
    const prev = [mod('a', { viewerSlots: slots })];
    const next = [mod('a', { remoteEntry: '/modules/a-v2/mf-manifest.json' })];
    expect(carryOverViewerSlots(prev, next)[0].viewerSlots).toBeUndefined();
  });
});
