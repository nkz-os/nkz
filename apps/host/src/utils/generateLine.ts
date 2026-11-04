import destination from '@turf/destination';
import { point } from '@turf/helpers';

export interface LinePoint {
  lat: number;
  lon: number;
  /** 1-based position along the line (positionInRow) */
  position: number;
}

const R = 6_371_008.8; // mean Earth radius, m
const MAX_POINTS = 2000;
const rad = (d: number) => (d * Math.PI) / 180;

export function haversineM(a: { lat: number; lon: number }, b: { lat: number; lon: number }): number {
  const dLat = rad(b.lat - a.lat);
  const dLon = rad(b.lon - a.lon);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

function bearingDeg(a: { lat: number; lon: number }, b: { lat: number; lon: number }): number {
  const y = Math.sin(rad(b.lon - a.lon)) * Math.cos(rad(b.lat));
  const x = Math.cos(rad(a.lat)) * Math.sin(rad(b.lat))
    - Math.sin(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.cos(rad(b.lon - a.lon));
  return (Math.atan2(y, x) * 180) / Math.PI;
}

/** Plants every `spacingM` metres from `start` towards `end` (a row of vines or olives). */
export function generateLine(
  start: { lat: number; lon: number },
  end: { lat: number; lon: number },
  spacingM: number,
): LinePoint[] {
  const first: LinePoint = { lat: start.lat, lon: start.lon, position: 1 };
  const length = haversineM(start, end);
  if (!(spacingM > 0) || spacingM > length) return [first];
  const n = Math.min(MAX_POINTS, Math.floor(length / spacingM + 1e-9) + 1);
  const bearing = bearingDeg(start, end);
  const origin = point([start.lon, start.lat]);
  const out: LinePoint[] = [first];
  for (let i = 1; i < n; i++) {
    const [lon, lat] = destination(origin, (i * spacingM) / 1000, bearing).geometry.coordinates;
    out.push({ lat, lon, position: i + 1 });
  }
  return out;
}

export { bearingDeg };
