import { describe, expect, it } from 'vitest';
import { generateGrid } from '../generateGrid';

describe('generateGrid row/col', () => {
  it('every point carries its 1-based row and column', () => {
    const pts = generateGrid({ anchor: { lat: 42.8, lon: -1.6 }, rows: 2, columns: 3, rowSpacing: 5,
      colSpacing: 4, bearing: 0, scale: 1 });
    expect(pts.map((p) => [p.row, p.col])).toEqual([[1, 1], [1, 2], [1, 3], [2, 1], [2, 2], [2, 3]]);
  });
});
