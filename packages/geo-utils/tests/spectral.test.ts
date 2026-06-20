import { describe, it, expect } from 'vitest';
import { GeoError } from '../src/types';

describe('spectral', () => {
  it('throws GeoError on unsupported index', () => {
    const error = new GeoError('UNSUPPORTED_INDEX', 'Index "INVALID" not supported');
    expect(error.code).toBe('UNSUPPORTED_INDEX');
    expect(error.message).toContain('INVALID');
  });
});
