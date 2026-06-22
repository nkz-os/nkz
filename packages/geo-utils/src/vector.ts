/**
 * Vector data utilities: GeoParquet I/O, reprojection, format conversion.
 */

import { GeoError } from './types';
import { initGeoLibre, runTool } from './wasm';
import type { FeatureCollection, GeometryObject } from 'geojson';

export async function writeGeoParquet(geojson: FeatureCollection<GeometryObject>): Promise<Uint8Array> {
  await initGeoLibre();
  const bytes = new TextEncoder().encode(JSON.stringify(geojson));
  const result = await runTool('write_geoparquet', [
    '--input=/work/input.geojson', '--output=/work/output.parquet',
  ], { 'input.geojson': bytes });
  return result.files['output.parquet'];
}

export async function readGeoParquet(parquet: Uint8Array): Promise<FeatureCollection<GeometryObject>> {
  await initGeoLibre();
  const result = await runTool('read_geoparquet', [
    '--input=/work/input.parquet', '--output=/work/output.geojson',
  ], { 'input.parquet': parquet });
  const str = new TextDecoder().decode(result.files['output.geojson']);
  return JSON.parse(str) as FeatureCollection<GeometryObject>;
}

export async function reprojectVector(
  data: FeatureCollection<GeometryObject> | GeometryObject,
  fromEpsg: number,
  toEpsg: number,
): Promise<FeatureCollection<GeometryObject>> {
  await initGeoLibre();
  const fc: FeatureCollection<GeometryObject> = data.type === 'FeatureCollection'
    ? data as FeatureCollection<GeometryObject>
    : { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: data as GeometryObject, properties: {} }] };
  const bytes = new TextEncoder().encode(JSON.stringify(fc));
  const result = await runTool('reproject_vector', [
    '--input=/work/input.geojson', '--output=/work/output.geojson', `--epsg=${toEpsg}`,
  ], { 'input.geojson': bytes });
  const str = new TextDecoder().decode(result.files['output.geojson']);
  return JSON.parse(str) as FeatureCollection<GeometryObject>;
}

export async function vectorConvert(data: Uint8Array, sourceFormat: string, targetFormat: string): Promise<Uint8Array> {
  await initGeoLibre();
  const result = await runTool('vector_convert', [
    `--input=/work/input.${sourceFormat}`, `--output=/work/output.${targetFormat}`,
  ], { [`input.${sourceFormat}`]: data });
  return result.files[`output.${targetFormat}`];
}
