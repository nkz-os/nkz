import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LayerRegistry, registerViewerLayers } from '../src/viewer/layerRegistry';
import { useViewerLayer } from '../src/viewer/useViewerLayer';

beforeEach(() => {
  LayerRegistry.reset();
});

describe('useViewerLayer', () => {
  it('returns the registered layer state', () => {
    registerViewerLayers('weather-map', [
      { id: 'wm-precip', titleKey: 'weather.precip', supportsOpacity: true, defaultVisible: false },
    ]);

    const { result } = renderHook(() => useViewerLayer('wm-precip'));

    expect(result.current.visible).toBe(false);
    expect(result.current.opacity).toBe(100);
    expect(result.current.status).toBe('idle');
  });

  it('re-renders and reflects new state when setVisible/setOpacity/setStatus are called', () => {
    registerViewerLayers('weather-map', [
      { id: 'wm-precip', titleKey: 'weather.precip', supportsOpacity: true, defaultVisible: true },
    ]);

    const { result } = renderHook(() => useViewerLayer('wm-precip'));

    act(() => {
      result.current.setVisible(false);
    });
    expect(result.current.visible).toBe(false);

    act(() => {
      result.current.setOpacity(42);
    });
    expect(result.current.opacity).toBe(42);

    act(() => {
      result.current.setStatus('ready');
    });
    expect(result.current.status).toBe('ready');
  });

  it('picks up registration that happens after the hook has already mounted', () => {
    const { result } = renderHook(() => useViewerLayer('soil-texture'));
    expect(result.current.visible).toBe(false); // inert default, not yet registered

    act(() => {
      registerViewerLayers('soil', [{ id: 'soil-texture', titleKey: 'soil.texture', defaultVisible: true }]);
    });

    expect(result.current.visible).toBe(true);
  });

  it('restores visible/opacity from the storage adapter on register', () => {
    const store = new Map<string, string>();
    LayerRegistry.setStorageAdapter({
      getItem: (key) => store.get(key) ?? null,
      setItem: (key, value) => {
        store.set(key, value);
      },
    });
    store.set('nkz.viewerLayer.wm-precip', JSON.stringify({ visible: false, opacity: 30 }));

    registerViewerLayers('weather-map', [
      { id: 'wm-precip', titleKey: 'weather.precip', supportsOpacity: true, defaultVisible: true },
    ]);

    const { result } = renderHook(() => useViewerLayer('wm-precip'));
    expect(result.current.visible).toBe(false);
    expect(result.current.opacity).toBe(30);
  });

  describe('unknown layer id', () => {
    let warnSpy: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
      warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    });

    afterEach(() => {
      warnSpy.mockRestore();
    });

    it('returns inert defaults and never throws', () => {
      const { result } = renderHook(() => useViewerLayer('does-not-exist'));

      expect(result.current.visible).toBe(false);
      expect(result.current.opacity).toBe(100);
      expect(result.current.status).toBe('idle');
      expect(() => result.current.setVisible(true)).not.toThrow();
      expect(() => result.current.setOpacity(50)).not.toThrow();
      expect(() => result.current.setStatus('error')).not.toThrow();

      // Setters on an unknown id must be true no-ops — no phantom entry created.
      expect(LayerRegistry.getLayer('does-not-exist')).toBeUndefined();
    });

    it('warns exactly once even across multiple renders', () => {
      const { result, rerender } = renderHook(() => useViewerLayer('does-not-exist'));
      rerender();
      rerender();
      act(() => {
        result.current.setVisible(true);
      });

      expect(warnSpy).toHaveBeenCalledTimes(1);
    });
  });
});
