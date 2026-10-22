import { describe, expect, it, vi } from 'vitest';
import { createTerrariumProvider } from '../useTerrainProvider';

/**
 * Terrarium tiles are Web Mercator (z/x/y with a single tile at z0). Without an
 * explicit tiling scheme Cesium's CustomHeightmapTerrainProvider uses the
 * geographic scheme (2x1 tiles at level 0), so it requested 0/1/0 — which does
 * not exist — and draped every other level with heights from the wrong tile.
 */
function fakeCesium() {
  class WebMercatorTilingScheme {}
  const created: { options?: any } = {};
  class CustomHeightmapTerrainProvider {
    constructor(options: any) {
      created.options = options;
    }
  }
  return {
    cesium: { WebMercatorTilingScheme, CustomHeightmapTerrainProvider } as any,
    created,
    WebMercatorTilingScheme,
  };
}

describe('createTerrariumProvider', () => {
  it('uses the Web Mercator tiling scheme', () => {
    const { cesium, created, WebMercatorTilingScheme } = fakeCesium();
    createTerrariumProvider(cesium);
    expect(created.options.tilingScheme).toBeInstanceOf(WebMercatorTilingScheme);
  });

  it('requests the terrarium tile with the same z/x/y it is asked for', async () => {
    const { cesium, created } = fakeCesium();
    createTerrariumProvider(cesium);
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 404 });
    vi.stubGlobal('fetch', fetchMock);
    await expect(created.options.callback(3, 5, 4)).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledWith(
      'https://elevation-tiles-prod.s3.amazonaws.com/terrarium/4/3/5.png',
    );
    vi.unstubAllGlobals();
  });
});
