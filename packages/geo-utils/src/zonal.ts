/**
 * Zonal statistics: compute raster statistics within a polygon geometry.
 */

import { type ZonalStatsResult, GeoError } from './types';
import { initGeoLibre, runTool } from './wasm';
import type { Polygon, MultiPolygon } from 'geojson';

export async function zonalStats(
  raster: Uint8Array,
  geometry: Polygon | MultiPolygon,
): Promise<ZonalStatsResult> {
  await initGeoLibre();
  const geojson: GeoJSON.FeatureCollection = {
    type: 'FeatureCollection',
    features: [{ type: 'Feature', geometry: geometry as any, properties: { id: 'zone' } }],
  };
  const bytes = new TextEncoder().encode(JSON.stringify(geojson));

  const result = await runTool('zonal_statistics', [
    '--input=/work/raster.tif', '--features=/work/zone.geojson',
    '--output=/work/stats.json', '--stat=mean', '--stat=min', '--stat=max', '--stat=sum', '--stat=std', '--ncells',
  ], { 'raster.tif': raster, 'zone.geojson': bytes });

  const str = new TextDecoder().decode(result.files['stats.json']);
  const raw = JSON.parse(str);

  return {
    mean: raw.mean ?? 0, min: raw.min ?? 0, max: raw.max ?? 0,
    sum: raw.sum ?? 0, std: raw.std ?? 0,
    count: raw.ncells ?? 0, areaM2: estimateArea(geometry),
  };
}

function estimateArea(geom: Polygon | MultiPolygon): number {
  const R = 6371000;
  const rings = geom.type === 'Polygon'
    ? [geom.coordinates[0]]
    : geom.coordinates.flatMap(p => [p[0]]);
  let total = 0;
  for (const ring of rings) {
    let area = 0;
    for (let i = 0; i < ring.length - 1; i++) {
      const [lon1, lat1] = ring[i], [lon2, lat2] = ring[i + 1];
      const x1 = lon1 * Math.PI / 180, y1 = lat1 * Math.PI / 180;
      const x2 = lon2 * Math.PI / 180, y2 = lat2 * Math.PI / 180;
      area += (x2 - x1) * (2 + Math.sin(y1) + Math.sin(y2));
    }
    total += Math.abs(area);
  }
  return total * R * R / 2;
}
