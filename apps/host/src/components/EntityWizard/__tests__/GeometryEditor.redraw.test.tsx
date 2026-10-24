import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import type { Geometry } from 'geojson';

vi.mock('@/context/I18nContext', () => ({ useI18n: () => ({ t: (k: string) => k }) }));
vi.mock('@/context/ViewerContext', () => ({
  useViewer: () => ({ cesiumViewer: null, startDrawing: vi.fn() }),
}));
vi.mock('@/utils/logger', () => ({
  logger: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

import { GeometryEditor } from '../GeometryEditor';

const added: Array<{ id?: string }> = [];
const removed: Array<{ id?: string }> = [];

function installCesium() {
  const viewer = {
    isDestroyed: () => false,
    destroy: vi.fn(),
    zoomTo: vi.fn(),
    entities: {
      add: (e: { id?: string }) => { added.push(e); return e; },
      remove: (e: { id?: string }) => { removed.push(e); },
    },
    scene: { backgroundColor: null, globe: {}, canvas: {} },
    camera: { setView: vi.fn() },
    imageryLayers: { removeAll: vi.fn(), addImageryProvider: vi.fn() },
    cesiumWidget: null,
  };
  const color = { withAlpha: () => ({}) };
  (window as unknown as { Cesium: unknown }).Cesium = {
    Viewer: function () { return viewer; },
    Color: { fromCssColorString: () => ({}), YELLOW: color, BLACK: {}, CYAN: {}, WHITE: {}, GRAY: color, GREEN: color, RED: color },
    EllipsoidTerrainProvider: function () { return {}; },
    UrlTemplateImageryProvider: function () { return {}; },
    Cartesian3: { fromDegrees: (lon: number, lat: number) => ({ lon, lat }) },
    ScreenSpaceEventHandler: function () { return { setInputAction: vi.fn(), destroy: vi.fn() }; },
    ScreenSpaceEventType: {},
    HeightReference: { CLAMP_TO_GROUND: 1 },
    ClassificationType: { TERRAIN: 1 },
    LabelStyle: {}, VerticalOrigin: {}, Cartesian2: function () { return {}; },
  };
}

const pt = (lon: number, lat: number): Geometry => ({ type: 'Point', coordinates: [lon, lat] });

describe('GeometryEditor follows initialGeometry', () => {
  beforeEach(() => { added.length = 0; removed.length = 0; installCesium(); });
  afterEach(() => { delete (window as unknown as { Cesium?: unknown }).Cesium; });

  it('redraws the marker when the point changes and clears it on null', () => {
    const props = { geometryType: 'Point' as const, onGeometryChange: vi.fn() };
    const { rerender } = render(<GeometryEditor {...props} initialGeometry={pt(-1.6, 42.8)} />);
    expect(added.filter((e) => e.id === 'current-geometry')).toHaveLength(1);

    rerender(<GeometryEditor {...props} initialGeometry={pt(-2, 41)} />);
    expect(added.filter((e) => e.id === 'current-geometry')).toHaveLength(2);
    expect(removed.filter((e) => e.id === 'current-geometry')).toHaveLength(1);

    rerender(<GeometryEditor {...props} initialGeometry={null} />);
    expect(removed.filter((e) => e.id === 'current-geometry')).toHaveLength(2);
    expect(added.filter((e) => e.id === 'current-geometry')).toHaveLength(2);
  });
});
