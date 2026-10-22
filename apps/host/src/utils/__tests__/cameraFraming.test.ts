import { describe, it, expect } from 'vitest';
import { EU_RECTANGLE_DEGREES, heightForResult, bboxCenter } from '../cameraFraming';
import type { GeocodeResult } from '@/types/geocode';

const mk = (over: Partial<GeocodeResult>): GeocodeResult =>
  ({ label: 'x', lat: 42, lon: -1, bbox: null, type: 'city', countryCode: 'ES', ...over }) as GeocodeResult;

describe('cameraFraming', () => {
  it('EU rectangle spans roughly the EU', () => {
    const [w, s, e, n] = EU_RECTANGLE_DEGREES;
    expect(w).toBeLessThan(e);
    expect(s).toBeLessThan(n);
    expect(w).toBeLessThanOrEqual(-10);
    expect(e).toBeGreaterThanOrEqual(30);
    expect(s).toBeLessThanOrEqual(36);
    expect(n).toBeGreaterThanOrEqual(60);
  });

  it('height shrinks as result gets more specific', () => {
    expect(heightForResult(mk({ type: 'country' }))).toBeGreaterThan(heightForResult(mk({ type: 'city' })));
    expect(heightForResult(mk({ type: 'city' }))).toBeGreaterThan(heightForResult(mk({ type: 'house' })));
  });

  it('bboxCenter returns midpoint', () => {
    expect(bboxCenter([-2, 40, 0, 42])).toEqual([-1, 41]);
  });
});
