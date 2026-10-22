import { describe, it, expect } from 'vitest';
import { buildColorLUT, isNativeColormap, toGeoLibreColormap } from '../src/colormaps';
import type { Colormap } from '../src/types';

describe('colormaps', () => {
  it('builds 256-entry temperature LUT', () => {
    const lut = buildColorLUT('temperature');
    expect(lut).toHaveLength(256);
    expect(lut[0]).toHaveLength(4);
    expect(lut[0][3]).toBe(255);
  });

  it('builds 256-entry NDVI LUT', () => {
    const lut = buildColorLUT('ndvi');
    expect(lut).toHaveLength(256);
  });

  it('builds 256-entry stress LUT', () => {
    const lut = buildColorLUT('stress');
    expect(lut).toHaveLength(256);
  });

  it('all colormaps have valid RGBA ranges', () => {
    for (const cmap of ['temperature', 'ndvi', 'stress', 'viridis'] as Colormap[]) {
      const lut = buildColorLUT(cmap);
      for (let i = 0; i < 256; i++) {
        expect(lut[i][0]).toBeGreaterThanOrEqual(0);
        expect(lut[i][0]).toBeLessThanOrEqual(255);
        expect(lut[i][1]).toBeGreaterThanOrEqual(0);
        expect(lut[i][1]).toBeLessThanOrEqual(255);
        expect(lut[i][2]).toBeGreaterThanOrEqual(0);
        expect(lut[i][2]).toBeLessThanOrEqual(255);
        expect(lut[i][3]).toBe(255);
      }
    }
  });

  it('temperature goes blue→red', () => {
    const lut = buildColorLUT('temperature');
    expect(lut[0][2]).toBeGreaterThan(lut[0][0]);
    expect(lut[255][0]).toBeGreaterThan(lut[255][2]);
  });

  it('ndvi goes brown→green', () => {
    const lut = buildColorLUT('ndvi');
    expect(lut[0][0]).toBeGreaterThan(lut[0][1]);
    expect(lut[255][1]).toBeGreaterThan(lut[255][0]);
  });

  it('stress goes green→red', () => {
    const lut = buildColorLUT('stress');
    expect(lut[0][1]).toBeGreaterThan(lut[0][0]);
    expect(lut[255][0]).toBeGreaterThan(lut[255][1]);
  });

  it('isNativeColormap returns true for viridis', () => {
    expect(isNativeColormap('viridis')).toBe(true);
    expect(isNativeColormap('temperature')).toBe(false);
  });

  it('toGeoLibreColormap maps correctly', () => {
    expect(toGeoLibreColormap('viridis')).toBe('viridis');
    expect(toGeoLibreColormap('temperature')).toBe('viridis');
  });
});
