import { describe, it, expect } from 'vitest';
import { resolveRegion, REGION_TABLE } from '../regions';

describe('resolveRegion', () => {
  it('Pamplona → navarra (most specific wins)', () => {
    expect(resolveRegion(-1.64, 42.81, 'eu')).toBe('navarra');
  });

  it('Madrid → spain', () => {
    expect(resolveRegion(-3.70, 40.42, 'eu')).toBe('spain');
  });

  it('Paris → eu', () => {
    expect(resolveRegion(2.35, 48.85, 'spain')).toBe('eu');
  });

  it('mid-Atlantic → world', () => {
    expect(resolveRegion(-40, 30, 'eu')).toBe('world');
  });

  it('hysteresis: just outside Navarra keeps navarra within margin', () => {
    const nav = REGION_TABLE.find(r => r.id === 'navarra')!;
    const justOutsideLon = nav.bbox[0] - 0.01;
    expect(resolveRegion(justOutsideLon, (nav.bbox[1] + nav.bbox[3]) / 2, 'navarra')).toBe('navarra');
  });

  it('hysteresis: too far outside Navarra flips to spain', () => {
    const nav = REGION_TABLE.find(r => r.id === 'navarra')!;
    const farLon = nav.bbox[0] - 0.2; // beyond hysteresis margin (0.15)
    expect(resolveRegion(farLon, (nav.bbox[1] + nav.bbox[3]) / 2, 'navarra')).toBe('spain');
  });
});
