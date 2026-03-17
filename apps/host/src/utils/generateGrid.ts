/**
 * Geodesic grid generator for Array Mode placement.
 *
 * Given an anchor point, row/column counts, spacing (meters), and a bearing
 * (degrees from north), produces an array of grid points using geodesic
 * calculations so the result is accurate regardless of latitude.
 */
import destination from '@turf/destination';
import { point } from '@turf/helpers';

export interface GridParams {
  anchor: { lat: number; lon: number };
  rows: number;
  columns: number;
  rowSpacing: number;   // meters
  colSpacing: number;   // meters
  bearing: number;      // degrees from north (0-360)
  scale: number;
  minScale?: number;    // override uniform scale with random range
  maxScale?: number;
  randomRotation?: boolean; // per-instance random heading (0-360)
}

export interface GridPoint {
  lat: number;
  lon: number;
  height: number;
  scale: number;
  rotation: number; // heading = bearing
}

/**
 * Generate a grid of points using geodesic distance calculations.
 *
 * Rows advance along the bearing direction.
 * Columns advance perpendicular (bearing + 90°).
 */
export function generateGrid(params: GridParams): GridPoint[] {
  const { anchor, rows, columns, rowSpacing, colSpacing, bearing, scale,
    minScale, maxScale, randomRotation } = params;

  if (rows < 1 || columns < 1) return [];

  const useRandomScale = minScale != null && maxScale != null && minScale !== maxScale;
  const results: GridPoint[] = [];
  const anchorPt = point([anchor.lon, anchor.lat]);
  const colBearing = (bearing + 90) % 360;

  // Seeded-ish deterministic random using index (consistent across re-renders)
  const rand = (i: number) => {
    const x = Math.sin(i * 9301 + 49297) * 233280;
    return x - Math.floor(x);
  };

  for (let r = 0; r < rows; r++) {
    const rowPt = r === 0
      ? anchorPt
      : destination(anchorPt, (r * rowSpacing) / 1000, bearing);

    for (let c = 0; c < columns; c++) {
      const cellPt = c === 0
        ? rowPt
        : destination(rowPt, (c * colSpacing) / 1000, colBearing);

      const idx = r * columns + c;
      const [lon, lat] = cellPt.geometry.coordinates;

      const instanceScale = useRandomScale
        ? minScale + rand(idx) * (maxScale - minScale)
        : scale;

      const instanceRotation = randomRotation
        ? Math.floor(rand(idx + 10000) * 360)
        : bearing;

      results.push({ lat, lon, height: 0, scale: instanceScale, rotation: instanceRotation });
    }
  }

  return results;
}
