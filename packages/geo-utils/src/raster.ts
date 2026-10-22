/**
 * Raster utilities: PMTiles generation, PNG rendering, reprojection.
 */

import { type PMTilesOptions, type Colormap, GeoError } from './types';
import { initGeoLibre, runTool } from './wasm';
import { toGeoLibreColormap } from './colormaps';

export async function rasterToPmtiles(cog: Uint8Array, opts: PMTilesOptions): Promise<Uint8Array> {
  await initGeoLibre();
  const args = [
    '--input=/work/input.tif', '--output=/work/output.pmtiles',
    `--colormap=${toGeoLibreColormap(opts.colormap)}`,
  ];
  if (opts.minZoom !== undefined) args.push(`--min_zoom=${opts.minZoom}`);
  if (opts.maxZoom !== undefined) args.push(`--max_zoom=${opts.maxZoom}`);
  const result = await runTool('write_pmtiles', args, { 'input.tif': cog });
  const pmtiles = result.files['output.pmtiles'];
  if (!pmtiles) throw new GeoError('TOOL_EXECUTION_FAILED', 'write_pmtiles produced no output');
  return pmtiles;
}

export async function renderRasterPNG(cog: Uint8Array, colormap: Colormap): Promise<Uint8Array> {
  await initGeoLibre();
  const result = await runTool('render_raster_png', [
    '--input=/work/input.tif', '--output=/work/output.png',
    `--colormap=${toGeoLibreColormap(colormap)}`,
  ], { 'input.tif': cog });
  return result.files['output.png'];
}

export async function reprojectRaster(cog: Uint8Array, targetEpsg: number): Promise<Uint8Array> {
  await initGeoLibre();
  const result = await runTool('reproject_raster', [
    '--input=/work/input.tif', '--output=/work/output.tif',
    `--epsg=${targetEpsg}`, '--resampling=bilinear',
  ], { 'input.tif': cog });
  return result.files['output.tif'];
}
