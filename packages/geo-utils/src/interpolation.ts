/**
 * Spatial interpolation of sensor point readings into a regular grid heatmap.
 *
 * Uses geolibre-wasm's lidar_idw_interpolation for IDW method.
 * Pure-JS fallback when WASM is unavailable.
 * Includes a built-in PNG encoder (no external deps).
 */

import { type SensorPoint, type HeatmapOptions, type HeatmapResult, type Colormap, GeoError } from './types';
import { initGeoLibre, runTool } from './wasm';
import { buildColorLUT, toGeoLibreColormap } from './colormaps';
import type { LatLngBounds } from './types';

/**
 * Interpolate sensor readings into a regular grid heatmap.
 *
 * @param points - Array of geolocated sensor readings
 * @param opts - Resolution, method, colormap
 * @returns Grid, bounds, stats, and PNG image bytes
 */
export async function interpolateHeatmap(
  points: SensorPoint[],
  opts?: HeatmapOptions,
): Promise<HeatmapResult> {
  if (points.length < 3) {
    throw new GeoError('INSUFFICIENT_POINTS', `Need at least 3 points, got ${points.length}`);
  }

  const resolution = opts?.resolution ?? 50;
  const method = opts?.method ?? 'idw';
  const power = opts?.power ?? 2;
  const colormap = opts?.colormap ?? 'temperature';

  // Compute bounds with 10% padding
  const xs = points.map(p => p.x);
  const ys = points.map(p => p.y);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yMin = Math.min(...ys), yMax = Math.max(...ys);
  const padX = (xMax - xMin) * 0.1 || 0.001;
  const padY = (yMax - yMin) * 0.1 || 0.001;

  try {
    await initGeoLibre();

    // Build GeoJSON from points
    const geojson: GeoJSON.FeatureCollection = {
      type: 'FeatureCollection',
      features: points.map((p) => ({
        type: 'Feature' as const,
        geometry: { type: 'Point' as const, coordinates: [p.x, p.y] },
        properties: { value: p.value },
      })),
    };
    const geojsonBytes = new TextEncoder().encode(JSON.stringify(geojson));

    // Determine cell size from bounds and resolution
    const cellSize = Math.min(
      ((xMax - xMin) || 0.01) / resolution,
      ((yMax - yMin) || 0.01) / resolution,
    );

    // Run IDW interpolation via geolibre
    const idwResult = await runTool('lidar_idw_interpolation', [
      '--input=/work/points.geojson',
      '--output=/work/grid.tif',
      `--resolution=${Math.max(cellSize, 0.0001)}`,
      `--weight=${power}`,
      '--field=value',
    ], { 'points.geojson': geojsonBytes });

    const gridTif = idwResult.files['grid.tif'];

    // Render to PNG via geolibre
    const pngResult = await runTool('render_raster_png', [
      '--input=/work/grid.tif',
      '--output=/work/heatmap.png',
      `--colormap=${toGeoLibreColormap(colormap)}`,
    ], { 'grid.tif': gridTif });

    const pngBytes = pngResult.files['heatmap.png'];

    // Read grid values using GeoTiffReader
    const { GeoTiffReader } = await import('geolibre-wasm');
    const reader = new GeoTiffReader(gridTif);
    const flatValues = reader.read_band_f64(0);
    const w = reader.width;
    const h = reader.height;
    reader.free();

    // Build 2D grid
    const grid: number[][] = [];
    let idx = 0;
    for (let row = 0; row < h; row++) {
      const rowData: number[] = [];
      for (let col = 0; col < w; col++) {
        rowData.push(flatValues[idx] ?? 0);
        idx++;
      }
      grid.push(rowData);
    }

    // Stats
    const valid = flatValues.filter(v => !isNaN(v) && v !== -9999);
    const sum = valid.reduce((a, b) => a + b, 0);
    const count = valid.length;
    const stats = {
      min: count > 0 ? Math.min(...valid) : 0,
      max: count > 0 ? Math.max(...valid) : 0,
      mean: count > 0 ? sum / count : 0,
    };

    return {
      grid,
      bounds: [xMin - padX, yMin - padY, xMax + padX, yMax + padY] as LatLngBounds,
      stats,
      png: pngBytes,
    };
  } catch (err) {
    // Fallback to pure-JS IDW
    return fallbackIDW(
      points, resolution, power, colormap,
      xMin - padX, xMax + padX, yMin - padY, yMax + padY,
    );
  }
}

/**
 * Pure-JS IDW fallback.
 */
export async function fallbackIDW(
  points: SensorPoint[],
  resolution: number,
  power: number,
  colormap: Colormap,
  xMin: number, xMax: number, yMin: number, yMax: number,
): Promise<HeatmapResult> {
  const flatValues: number[] = [];
  const grid: number[][] = [];

  for (let row = 0; row < resolution; row++) {
    const y = yMin + (yMax - yMin) * (row / (resolution - 1));
    const rowData: number[] = [];
    for (let col = 0; col < resolution; col++) {
      const x = xMin + (xMax - xMin) * (col / (resolution - 1));
      let numerator = 0, denominator = 0;
      for (const pt of points) {
        const dist = Math.sqrt((x - pt.x) ** 2 + (y - pt.y) ** 2);
        if (dist === 0) { numerator = pt.value; denominator = 1; break; }
        const w = 1 / Math.pow(dist, power);
        numerator += w * pt.value;
        denominator += w;
      }
      const v = denominator > 0 ? numerator / denominator : 0;
      rowData.push(v);
      flatValues.push(v);
    }
    grid.push(rowData);
  }

  const valid = flatValues.filter(v => !isNaN(v));
  const sum = valid.reduce((a, b) => a + b, 0);
  const count = valid.length;
  const stats = {
    min: count > 0 ? Math.min(...valid) : 0,
    max: count > 0 ? Math.max(...valid) : 0,
    mean: count > 0 ? sum / count : 0,
  };

  // Build PNG from grid
  const lut = buildColorLUT(colormap);
  const pixelCount = resolution * resolution;
  const pixelData = new Uint8Array(pixelCount * 4);
  const range = stats.max - stats.min || 1;
  for (let i = 0; i < pixelCount; i++) {
    const t = Math.max(0, Math.min(1, (flatValues[i] - stats.min) / range));
    const color = lut[Math.floor(t * 255)];
    pixelData[i * 4] = color[0];
    pixelData[i * 4 + 1] = color[1];
    pixelData[i * 4 + 2] = color[2];
    pixelData[i * 4 + 3] = color[3];
  }

  const png = encodeSimplePNG(pixelData, resolution, resolution);

  return {
    grid,
    bounds: [xMin, yMin, xMax, yMax] as LatLngBounds,
    stats,
    png,
  };
}

// ── PNG encoder ──────────────────────────────────────────────────────────

function buildCRC32Table(): Uint32Array {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    table[n] = c;
  }
  return table;
}

const CRC_TABLE = buildCRC32Table();

function crc32(buf: Uint8Array): number {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}

function pngChunk(type: string, data: Uint8Array): Uint8Array {
  const typeBytes = new TextEncoder().encode(type);
  const crcInput = new Uint8Array(typeBytes.length + data.length);
  crcInput.set(typeBytes);
  crcInput.set(data, typeBytes.length);
  const len = 12 + data.length;
  const chunk = new Uint8Array(len);
  const view = new DataView(chunk.buffer);
  view.setUint32(0, data.length, false);
  chunk.set(typeBytes, 4);
  chunk.set(data, 8);
  view.setUint32(len - 4, crc32(crcInput), false);
  return chunk;
}

/**
 * Encode raw RGBA pixels to a valid PNG.
 * Uses stored deflate blocks (no compression) — produces larger files but needs no zlib.
 */
export function encodeSimplePNG(pixels: Uint8Array, width: number, height: number): Uint8Array {
  // Raw data with filter byte per row
  const rawStride = width * 4 + 1;
  const rawData = new Uint8Array(rawStride * height);
  for (let y = 0; y < height; y++) {
    rawData[y * rawStride] = 0; // None filter
    for (let x = 0; x < width * 4; x++) {
      rawData[y * rawStride + 1 + x] = pixels[y * width * 4 + x];
    }
  }

  // Deflate header + stored block
  const adlerA = 1, adlerB = 0;
  let a1 = adlerA, a2 = adlerB;
  for (let i = 0; i < rawData.length; i++) {
    a1 = (a1 + rawData[i]) % 65521;
    a2 = (a2 + a1) % 65521;
  }
  const adler = ((a2 << 16) | a1) >>> 0;
  const adlerBytes = new Uint8Array(4);
  new DataView(adlerBytes.buffer).setUint32(0, adler, false);

  const deflate = new Uint8Array(rawData.length + 11);
  deflate[0] = 0x78; deflate[1] = 0x01; // zlib header
  deflate[2] = 0x01; // BTYPE=stored, BFINAL=1
  const len = rawData.length;
  deflate[3] = len & 0xFF;
  deflate[4] = (len >> 8) & 0xFF;
  deflate[5] = ((~len) & 0xFFFF) & 0xFF;
  deflate[6] = ((~len) >> 8) & 0xFF;
  deflate.set(rawData, 7);
  deflate.set(adlerBytes, 7 + rawData.length);

  // Assemble PNG
  const parts: Uint8Array[] = [];
  parts.push(new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])); // signature

  const ihdr = new Uint8Array(13);
  new DataView(ihdr.buffer).setUint32(0, width, false);
  new DataView(ihdr.buffer).setUint32(4, height, false);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  parts.push(pngChunk('IHDR', ihdr));

  parts.push(pngChunk('IDAT', deflate));
  parts.push(pngChunk('IEND', new Uint8Array(0)));

  const total = parts.reduce((s, p) => s + p.length, 0);
  const result = new Uint8Array(total);
  let offset = 0;
  for (const p of parts) { result.set(p, offset); offset += p.length; }
  return result;
}
