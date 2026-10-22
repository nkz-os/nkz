import { beforeEach, describe, expect, it } from 'vitest';
import { LayerRegistry } from '@nekazari/sdk';
import { defineModule } from '../src/defineModule';

const base = {
  id: 'weather-map',
  displayName: 'Weather Map',
  hostApiVersion: '^2.0.0',
  accent: { base: '#A16207', soft: '#FEF3C7', strong: '#713F12' },
};

beforeEach(() => {
  LayerRegistry.reset();
});

describe('defineModule({ viewerLayers })', () => {
  it('registers declared layers into the SDK LayerRegistry', () => {
    defineModule({
      ...base,
      viewerLayers: [
        { id: 'wm-precip', titleKey: 'weatherMap.layers.precip', group: 'weather', supportsOpacity: true, defaultVisible: false },
      ],
    });

    const entry = LayerRegistry.getLayer('wm-precip');
    expect(entry).toBeDefined();
    expect(entry?.moduleId).toBe('weather-map');
    expect(entry?.titleKey).toBe('weatherMap.layers.precip');
    expect(entry?.group).toBe('weather');
    expect(entry?.supportsOpacity).toBe(true);
    expect(entry?.visible).toBe(false);
  });

  it('does not touch the registry when viewerLayers is omitted or empty', () => {
    defineModule(base);
    defineModule({ ...base, id: 'soil', viewerLayers: [] });
    expect(LayerRegistry.getAllLayers()).toHaveLength(0);
  });

  it('re-defining the same module replaces its layer set (idempotent)', () => {
    defineModule({
      ...base,
      viewerLayers: [{ id: 'wm-precip', titleKey: 'weatherMap.layers.precip' }],
    });
    defineModule({
      ...base,
      viewerLayers: [{ id: 'wm-frost', titleKey: 'weatherMap.layers.frost' }],
    });

    expect(LayerRegistry.getLayer('wm-precip')).toBeUndefined();
    expect(LayerRegistry.getLayer('wm-frost')).toBeDefined();
    expect(LayerRegistry.getAllLayers()).toHaveLength(1);
  });

  it('rejects a non-kebab-case layer id', () => {
    expect(() =>
      defineModule({
        ...base,
        viewerLayers: [{ id: 'BadId', titleKey: 'x.y' }],
      }),
    ).toThrowError(/viewerLayers.*kebab-case/is);
  });

  it('rejects a layer without titleKey', () => {
    expect(() =>
      defineModule({
        ...base,
        viewerLayers: [{ id: 'wm-precip' } as never],
      }),
    ).toThrowError(/titleKey/);
  });

  it('rejects unknown fields on a layer declaration (strict schema)', () => {
    expect(() =>
      defineModule({
        ...base,
        viewerLayers: [{ id: 'wm-precip', titleKey: 'x.y', opacity: 50 } as never],
      }),
    ).toThrowError(/viewerLayers/i);
  });
});
