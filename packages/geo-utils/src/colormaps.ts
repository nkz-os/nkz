/**
 * Colormap definitions for raster rendering.
 *
 * Custom Nekazari colormaps (temperature, ndvi, stress) map float values [0-1]
 * to RGBA tuples. Used by renderRasterPNG and rasterToPmtiles.
 *
 * geolibre natively supports: viridis, magma, turbo, terrain, grayscale.
 * For custom colormaps we delegate to geolibre's native viridis and apply
 * the LUT client-side.
 */

import type { Colormap } from './types';

/** RGBA color tuple [r, g, b, a] */
type RGBA = [number, number, number, number];

/** Lookup table for a colormap, 256 entries */
export type ColorLUT = RGBA[];

/**
 * Check if a colormap is native to geolibre or custom Nekazari.
 */
export function isNativeColormap(cmap: Colormap): boolean {
  return ['viridis', 'magma', 'turbo', 'terrain', 'grayscale'].includes(cmap);
}

/**
 * Get the geolibre colormap name for native colormaps.
 * Custom colormaps fall back to 'viridis' (LUT applied client-side).
 */
export function toGeoLibreColormap(cmap: Colormap): string {
  if (isNativeColormap(cmap)) return cmap;
  return 'viridis';
}

/**
 * Build a 256-entry RGBA LUT for a colormap.
 */
export function buildColorLUT(cmap: Colormap): ColorLUT {
  switch (cmap) {
    case 'temperature': return buildTemperatureLUT();
    case 'ndvi':        return buildNDVILUT();
    case 'stress':      return buildStressLUT();
    default:            return buildViridisFallbackLUT();
  }
}

function buildTemperatureLUT(): ColorLUT {
  const lut: ColorLUT = new Array(256);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    const r = Math.min(1, t * 3);
    const g = Math.min(1, Math.sin(t * Math.PI) * 1.2);
    const b = Math.max(0, 1 - t * 1.5);
    lut[i] = [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255), 255];
  }
  return lut;
}

function buildNDVILUT(): ColorLUT {
  const lut: ColorLUT = new Array(256);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    const r = t < 0.5 ? 0.8 - t * 0.4 : 0.2;
    const g = t < 0.3 ? t * 2 : 0.3 + (t - 0.3) * 1.2;
    const b = t < 0.5 ? 0.1 : 0.05;
    lut[i] = [
      Math.round(Math.min(1, Math.max(0, r)) * 255),
      Math.round(Math.min(1, Math.max(0, g)) * 255),
      Math.round(Math.min(1, Math.max(0, b)) * 255),
      255,
    ];
  }
  return lut;
}

function buildStressLUT(): ColorLUT {
  const lut: ColorLUT = new Array(256);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    const r = Math.min(1, t * 1.2);
    const g = Math.max(0, 1 - t * 1.2);
    const b = Math.max(0, 0.2 - t * 0.3);
    lut[i] = [
      Math.round(Math.min(1, Math.max(0, r)) * 255),
      Math.round(Math.min(1, Math.max(0, g)) * 255),
      Math.round(Math.min(1, Math.max(0, b)) * 255),
      255,
    ];
  }
  return lut;
}

function buildViridisFallbackLUT(): ColorLUT {
  const lut: ColorLUT = new Array(256);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    lut[i] = [
      Math.min(255, Math.max(0, Math.round((0.27 + t * 0.97 - Math.pow(t - 0.5, 2) * 0.5) * 255))),
      Math.min(255, Math.max(0, Math.round((0.0 + t * 0.73 + Math.max(0, t - 0.3) * 0.5) * 255))),
      Math.min(255, Math.max(0, Math.round((0.33 + (1 - t) * 0.67) * 255))),
      255,
    ];
  }
  return lut;
}
