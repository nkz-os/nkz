/**
 * Spectral index computation using geolibre-wasm's spectral_index tool.
 */

import { type SpectralOptions, type SpectralResult, GeoError } from './types';
import { initGeoLibre, runTool, getBrowserLib } from './wasm';

const INDEX_MAP: Record<string, string> = {
  NDVI: 'ndvi',
  NDWI: 'ndwi',
  EVI: 'evi',
  SAVI: 'savi',
  NBR: 'nbr',
  NDBI: 'ndbi',
  GNDVI: 'gndvi',
  OSAVI: 'osavi',
  CIre: 'ci_re',
};

function resolveIndex(index: string): string {
  const mapped = INDEX_MAP[index];
  if (!mapped) throw new GeoError('UNSUPPORTED_INDEX', `Index "${index}" not supported`);
  return mapped;
}

/**
 * Compute a spectral index from a multi-band COG.
 *
 * @param cog - Raw bytes of the GeoTIFF/COG file
 * @param opts - Index type and band mapping
 * @returns Computed index values, dimensions, statistics
 */
export async function computeSpectralIndex(
  cog: Uint8Array,
  opts: SpectralOptions,
): Promise<SpectralResult> {
  await initGeoLibre();

  const geolibreIndex = resolveIndex(opts.index);
  const args = [
    '--input=/work/input.tif',
    '--output=/work/output.tif',
    `--index=${geolibreIndex}`,
    `--red_band=${opts.redBand ?? 4}`,
    `--nir_band=${opts.nirBand ?? 8}`,
  ];
  if (opts.blueBand !== undefined) args.push(`--blue_band=${opts.blueBand}`);
  if (opts.swirBand !== undefined) args.push(`--swir_band=${opts.swirBand}`);

  const result = await runTool('spectral_index', args, { 'input.tif': cog });
  const outputCog = result.files['output.tif'];

  // Read output COG for stats
  const { GeoTiffReader } = await import('geolibre-wasm');
  const reader = new GeoTiffReader(outputCog);
  const values = reader.read_band_f64(0);
  const width = reader.width;
  const height = reader.height;
  const epsg = reader.epsg ?? 0;
  reader.free();

  // Compute stats
  let sum = 0, min = Infinity, max = -Infinity, count = 0;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (!isNaN(v) && v !== -9999 && v !== -3.4e38) { sum += v; if (v < min) min = v; if (v > max) max = v; count++; }
  }
  const mean = count > 0 ? sum / count : 0;
  let sumSq = 0;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (!isNaN(v) && v !== -9999 && v !== -3.4e38) sumSq += (v - mean) ** 2;
  }
  const std = count > 1 ? Math.sqrt(sumSq / (count - 1)) : 0;

  return { values, width, height, stats: { min, max, mean, std }, epsg };
}
