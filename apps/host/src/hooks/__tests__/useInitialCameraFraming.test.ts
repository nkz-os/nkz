import { it, expect } from 'vitest';
import { decideInitialFraming } from '../useInitialCameraFraming';

it('frames parcels when present', () => {
  const d = decideInitialFraming([[-1.6, 42.8], [-1.5, 42.7]]);
  expect(d.kind).toBe('parcels');
  if (d.kind === 'parcels') {
    expect(d.centroids).toHaveLength(2);
  }
});

it('frames EU when no parcels', () => {
  expect(decideInitialFraming([]).kind).toBe('eu');
});
