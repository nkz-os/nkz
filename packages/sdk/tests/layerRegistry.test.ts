import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  LayerRegistry,
  registerViewerLayers,
  type ViewerLayerStorageAdapter,
} from '../src/viewer/layerRegistry';

function fakeStorage(): ViewerLayerStorageAdapter {
  const map = new Map<string, string>();
  return {
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => {
      map.set(key, value);
    },
  };
}

beforeEach(() => {
  LayerRegistry.reset();
});

describe('registerViewerLayers / LayerRegistry', () => {
  it('registers layers with declared defaults', () => {
    registerViewerLayers('weather-map', [
      { id: 'wm-precip', titleKey: 'weather.precip', group: 'weather', supportsOpacity: true, defaultVisible: false },
    ]);

    const entry = LayerRegistry.getLayer('wm-precip');
    expect(entry).toEqual({
      id: 'wm-precip',
      moduleId: 'weather-map',
      titleKey: 'weather.precip',
      group: 'weather',
      supportsOpacity: true,
      defaultVisible: false,
      visible: false,
      opacity: 100,
      status: 'idle',
    });
  });

  it('defaults defaultVisible to true and supportsOpacity to false when omitted', () => {
    registerViewerLayers('soil', [{ id: 'soil-texture', titleKey: 'soil.texture' }]);
    const entry = LayerRegistry.getLayer('soil-texture');
    expect(entry?.visible).toBe(true);
    expect(entry?.supportsOpacity).toBe(false);
  });

  it('is idempotent per moduleId: re-registering replaces the previous set', () => {
    registerViewerLayers('crop-health', [
      { id: 'ch-stress', titleKey: 'ch.stress' },
      { id: 'ch-water', titleKey: 'ch.water' },
    ]);
    expect(LayerRegistry.getAllLayers()).toHaveLength(2);

    registerViewerLayers('crop-health', [
      { id: 'ch-stress', titleKey: 'ch.stress' },
      { id: 'ch-disease', titleKey: 'ch.disease' },
    ]);

    const all = LayerRegistry.getAllLayers();
    expect(all).toHaveLength(2);
    expect(LayerRegistry.getLayer('ch-water')).toBeUndefined();
    expect(LayerRegistry.getLayer('ch-stress')).toBeDefined();
    expect(LayerRegistry.getLayer('ch-disease')).toBeDefined();
  });

  it('does not leak another module layers when replacing', () => {
    registerViewerLayers('weather-map', [{ id: 'wm-precip', titleKey: 'weather.precip' }]);
    registerViewerLayers('soil', [{ id: 'soil-texture', titleKey: 'soil.texture' }]);

    registerViewerLayers('weather-map', [{ id: 'wm-frost', titleKey: 'weather.frost' }]);

    expect(LayerRegistry.getLayer('soil-texture')).toBeDefined();
    expect(LayerRegistry.getLayer('wm-precip')).toBeUndefined();
    expect(LayerRegistry.getLayer('wm-frost')).toBeDefined();
  });

  it('preserves runtime visible/opacity across re-registration via the storage round-trip', () => {
    const storage = fakeStorage();
    LayerRegistry.setStorageAdapter(storage);

    registerViewerLayers('weather-map', [
      { id: 'wm-precip', titleKey: 'weather.precip', supportsOpacity: true, defaultVisible: true },
    ]);
    LayerRegistry.setVisible('wm-precip', false);
    LayerRegistry.setOpacity('wm-precip', 40);

    // Simulate a remount: module calls registerViewerLayers again with the same decl.
    registerViewerLayers('weather-map', [
      { id: 'wm-precip', titleKey: 'weather.precip', supportsOpacity: true, defaultVisible: true },
    ]);

    const entry = LayerRegistry.getLayer('wm-precip');
    expect(entry?.visible).toBe(false);
    expect(entry?.opacity).toBe(40);
  });

  it('falls back to declared defaults when persisted storage holds malformed JSON', () => {
    const storage = fakeStorage();
    storage.setItem('nkz.viewerLayer.soil-texture', '{not valid json');
    LayerRegistry.setStorageAdapter(storage);

    expect(() =>
      registerViewerLayers('soil', [{ id: 'soil-texture', titleKey: 'soil.texture', defaultVisible: true }]),
    ).not.toThrow();
    expect(LayerRegistry.getLayer('soil-texture')?.visible).toBe(true);
    expect(LayerRegistry.getLayer('soil-texture')?.opacity).toBe(100);
  });

  it('falls back to declared defaults when persisted storage holds a partial/wrong-shaped value', () => {
    const storage = fakeStorage();
    storage.setItem('nkz.viewerLayer.soil-texture', JSON.stringify({ visible: false })); // missing opacity
    LayerRegistry.setStorageAdapter(storage);

    registerViewerLayers('soil', [{ id: 'soil-texture', titleKey: 'soil.texture', defaultVisible: true }]);
    expect(LayerRegistry.getLayer('soil-texture')?.visible).toBe(true);
    expect(LayerRegistry.getLayer('soil-texture')?.opacity).toBe(100);
  });

  it('setVisible/setOpacity/setStatus update state and notify subscribers', () => {
    registerViewerLayers('soil', [{ id: 'soil-texture', titleKey: 'soil.texture', supportsOpacity: true }]);
    const listener = vi.fn();
    const unsubscribe = LayerRegistry.subscribe(listener);

    LayerRegistry.setVisible('soil-texture', false);
    LayerRegistry.setOpacity('soil-texture', 55);
    LayerRegistry.setStatus('soil-texture', 'ready');

    expect(listener).toHaveBeenCalledTimes(3);
    const entry = LayerRegistry.getLayer('soil-texture');
    expect(entry?.visible).toBe(false);
    expect(entry?.opacity).toBe(55);
    expect(entry?.status).toBe('ready');

    unsubscribe();
    LayerRegistry.setStatus('soil-texture', 'error');
    expect(listener).toHaveBeenCalledTimes(3); // no more calls after unsubscribe
  });

  it('does not notify when setting the same value (no-op)', () => {
    registerViewerLayers('soil', [{ id: 'soil-texture', titleKey: 'soil.texture', defaultVisible: true }]);
    const listener = vi.fn();
    LayerRegistry.subscribe(listener);

    LayerRegistry.setVisible('soil-texture', true); // already true
    expect(listener).not.toHaveBeenCalled();
  });

  it('clamps opacity to [0, 100]', () => {
    registerViewerLayers('soil', [{ id: 'soil-texture', titleKey: 'soil.texture', supportsOpacity: true }]);
    LayerRegistry.setOpacity('soil-texture', 250);
    expect(LayerRegistry.getLayer('soil-texture')?.opacity).toBe(100);
    LayerRegistry.setOpacity('soil-texture', -30);
    expect(LayerRegistry.getLayer('soil-texture')?.opacity).toBe(0);
  });

  it('getAllLayers returns a stable reference until the next change', () => {
    registerViewerLayers('soil', [{ id: 'soil-texture', titleKey: 'soil.texture' }]);
    const snap1 = LayerRegistry.getAllLayers();
    const snap2 = LayerRegistry.getAllLayers();
    expect(snap1).toBe(snap2);

    LayerRegistry.setVisible('soil-texture', false);
    const snap3 = LayerRegistry.getAllLayers();
    expect(snap3).not.toBe(snap2);
  });

  describe('unknown ids', () => {
    it('getLayer returns undefined for an unregistered id', () => {
      expect(LayerRegistry.getLayer('nope')).toBeUndefined();
    });

    it('setVisible/setOpacity/setStatus on an unknown id are silent no-ops (never throw)', () => {
      expect(() => LayerRegistry.setVisible('nope', true)).not.toThrow();
      expect(() => LayerRegistry.setOpacity('nope', 50)).not.toThrow();
      expect(() => LayerRegistry.setStatus('nope', 'error')).not.toThrow();
      expect(LayerRegistry.getLayer('nope')).toBeUndefined();
    });

    it('consumeUnknownWarning is true only the first time per id, until reset', () => {
      expect(LayerRegistry.consumeUnknownWarning('ghost')).toBe(true);
      expect(LayerRegistry.consumeUnknownWarning('ghost')).toBe(false);
      expect(LayerRegistry.consumeUnknownWarning('ghost')).toBe(false);

      LayerRegistry.reset();
      expect(LayerRegistry.consumeUnknownWarning('ghost')).toBe(true);
    });
  });
});
