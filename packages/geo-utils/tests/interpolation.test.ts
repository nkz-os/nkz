import { describe, it, expect } from 'vitest';
import { fallbackIDW, encodeSimplePNG } from '../src/interpolation';
import type { SensorPoint } from '../src/types';

describe('fallbackIDW', () => {
  it('interpolates 3 points into grid', async () => {
    const points: SensorPoint[] = [
      { x: 0, y: 0, value: 10 },
      { x: 1, y: 0, value: 20 },
      { x: 0, y: 1, value: 30 },
    ];
    const result = await fallbackIDW(points, 10, 2, 'temperature', -0.1, 1.1, -0.1, 1.1);
    expect(result.grid).toHaveLength(10);
    expect(result.grid[0]).toHaveLength(10);
    expect(result.stats.min).toBeGreaterThanOrEqual(9);
    expect(result.stats.max).toBeLessThanOrEqual(31);
    expect(result.stats.mean).toBeGreaterThan(0);
  });

  it('returns exact values near point locations', async () => {
    const points: SensorPoint[] = [
      { x: 0.5, y: 0.5, value: 42 },
      { x: 0, y: 0, value: 10 },
      { x: 1, y: 1, value: 100 },
    ];
    const result = await fallbackIDW(points, 20, 5, 'temperature', 0, 1, 0, 1);
    const mid = Math.floor(result.grid.length / 2);
    expect(result.grid[mid][mid]).toBeGreaterThan(35);
    expect(result.grid[mid][mid]).toBeLessThan(55);
  });

  it('generates valid PNG signature', async () => {
    const points: SensorPoint[] = [
      { x: -1.5, y: 42.3, value: 28 },
      { x: -1.49, y: 42.31, value: 32 },
      { x: -1.51, y: 42.29, value: 25 },
    ];
    const result = await fallbackIDW(points, 50, 2, 'temperature', -1.52, -1.48, 42.28, 42.32);
    expect(result.png[0]).toBe(137); // PNG magic
    expect(result.png[1]).toBe(80);  // 'P'
    expect(result.png[2]).toBe(78);  // 'N'
    expect(result.png[3]).toBe(71);  // 'G'
  });
});

describe('encodeSimplePNG', () => {
  it('produces valid PNG for small image', () => {
    const pixels = new Uint8Array(4 * 4 * 4); // 4x4 RGBA black
    const png = encodeSimplePNG(pixels, 4, 4);
    expect(png[0]).toBe(137);
    expect(png[1]).toBe(80);
    expect(png[2]).toBe(78);
    expect(png[3]).toBe(71);
    expect(png.length).toBeGreaterThan(50);
  });

  it('produces valid PNG for 50x50 image', () => {
    const pixels = new Uint8Array(50 * 50 * 4);
    for (let i = 0; i < pixels.length; i++) pixels[i] = Math.floor(Math.random() * 256);
    const png = encodeSimplePNG(pixels, 50, 50);
    expect(png[0]).toBe(137);
    expect(png.length).toBeGreaterThan(100);
  });
});
