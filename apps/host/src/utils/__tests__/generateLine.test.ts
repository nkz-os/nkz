import { describe, expect, it } from 'vitest';
import destination from '@turf/destination';
import { point } from '@turf/helpers';
import { generateLine, haversineM } from '../generateLine';

const start = { lat: 42.8, lon: -1.6 };
// exactly 10 m east of start (same sphere as the generator)
const [endLon, endLat] = destination(point([start.lon, start.lat]), 0.01, 90).geometry.coordinates;
const end = { lat: endLat, lon: endLon };

describe('generateLine', () => {
  it('places a plant every spacing metres from start, positions 1..n', () => {
    const pts = generateLine(start, end, 2);
    expect(pts.map((p) => p.position)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(haversineM(pts[0], start)).toBeLessThan(0.01);
    expect(haversineM(pts[5], end)).toBeLessThan(0.05);
  });

  it('keeps the points on the line', () => {
    const pts = generateLine(start, end, 2.5);
    for (const p of pts) expect(Math.abs(p.lat - start.lat)).toBeLessThan(1e-5);
  });

  it('a spacing of zero, negative or longer than the line gives only the start', () => {
    expect(generateLine(start, end, 0)).toHaveLength(1);
    expect(generateLine(start, end, -1)).toHaveLength(1);
    expect(generateLine(start, end, 50)).toHaveLength(1);
  });

  it('caps very dense lines', () => {
    expect(generateLine(start, end, 0.001).length).toBeLessThanOrEqual(2000);
  });
});
