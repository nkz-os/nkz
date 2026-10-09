import { describe, it, expect } from 'vitest';
import { rangeFromCropCycles, resolveTimelineRange } from '../campaignRange';

const P = 'urn:ngsi-ld:AgriParcel:p1';
const NOW = Date.UTC(2026, 9, 9);
const DAY = 86_400_000;
const D = (iso: string) => Date.parse(`${iso}T00:00:00Z`);
const typed = (iso: string) => ({ type: 'Property', value: { '@type': 'Date', '@value': iso } });
const crop = (attrs: Record<string, unknown>, parcel = P) => ({
  id: `c-${Math.random()}`, type: 'AgriCrop',
  hasAgriParcel: { type: 'Relationship', object: parcel },
  ...attrs,
});
const DEFAULT = { source: 'default', range: { start: NOW - 90 * DAY, end: NOW + 30 * DAY } };

describe('resolveTimelineRange', () => {
  it('uses an assign-crop campaign with typed @value dates', () => {
    const r = resolveTimelineRange([crop({
      plantingDate: typed('2026-09-15'), harvestDate: typed('2027-07-01'),
      status: { type: 'Property', value: 'active' },
    })], P, NOW);
    expect(r).toEqual({ source: 'campaign', range: { start: D('2026-09-15'), end: D('2027-07-01') } });
  });

  it('uses expectedTerminationDate for crop-plan segments', () => {
    const r = resolveTimelineRange([crop({
      plantingDate: typed('2026-09-20'), expectedTerminationDate: typed('2027-06-15'), status: 'active',
    })], P, NOW);
    expect(r.range).toEqual({ start: D('2026-09-20'), end: D('2027-06-15') });
  });

  it('prefers the active segment over a later-planted crop without status, and ignores other parcels', () => {
    const r = resolveTimelineRange([
      crop({ plantingDate: '2026-03-01', harvestDate: '2026-12-01', status: 'active' }),
      crop({ plantingDate: '2026-05-01', harvestDate: '2026-12-01' }),
      crop({ plantingDate: '2026-09-30', harvestDate: '2027-01-01', status: 'active' }, 'urn:other'),
    ], P, NOW);
    expect(r.range.start).toBe(D('2026-03-01'));
  });

  it('never picks harvested, terminated or planned segments', () => {
    expect(resolveTimelineRange([
      crop({ plantingDate: '2026-05-01', terminationDate: '2026-12-01', status: 'harvested' }),
      crop({ plantingDate: '2026-06-01', status: 'terminated' }),
      crop({ sowingWindowStart: '2026-10-01', status: 'planned' }),
    ], P, NOW)).toEqual(DEFAULT);
  });

  it('keeps a late active campaign open until now + 30 days', () => {
    const r = resolveTimelineRange([crop({ plantingDate: '2026-03-01', harvestDate: '2026-09-01', status: 'active' })], P, NOW);
    expect(r).toEqual({ source: 'campaign', range: { start: D('2026-03-01'), end: NOW + 30 * DAY } });
  });

  it('defaults the end to planting + 180 days when no end date is usable', () => {
    expect(resolveTimelineRange([crop({ plantingDate: '2026-09-01' })], P, NOW).range.end).toBe(D('2026-09-01') + 180 * DAY);
    expect(resolveTimelineRange([crop({ plantingDate: '2026-09-01', harvestDate: '2026-01-01' })], P, NOW).range.end)
      .toBe(D('2026-09-01') + 180 * DAY);
  });

  it('falls back to the default window without a usable campaign', () => {
    expect(resolveTimelineRange([], P, NOW)).toEqual(DEFAULT);
    expect(resolveTimelineRange([crop({ plantingDate: { type: 'Property', value: {} } })], P, NOW)).toEqual(DEFAULT);
    expect(resolveTimelineRange([crop({ plantingDate: '2027-01-01' })], P, NOW)).toEqual(DEFAULT);                         // not sown yet
    expect(resolveTimelineRange([crop({ plantingDate: '2025-01-01', harvestDate: '2025-08-01' })], P, NOW)).toEqual(DEFAULT); // finished, no status
  });

  it('caps a perennial campaign to the last 365 days', () => {
    const r = resolveTimelineRange([crop({ plantingDate: '2008-03-01', status: 'active' })], P, NOW);
    expect(r).toEqual({ source: 'campaign', range: { start: NOW - 365 * DAY, end: NOW + 30 * DAY } });
  });

  it('caps a far-future campaign end to now + 365 days', () => {
    const r = resolveTimelineRange([crop({
      plantingDate: '2026-09-01', expectedTerminationDate: '2062-01-01', status: 'active',
    })], P, NOW);
    expect(r).toEqual({ source: 'campaign', range: { start: D('2026-09-01'), end: NOW + 365 * DAY } });
  });
});

describe('rangeFromCropCycles', () => {
  const cyc = (s: string | null, e: string | null) => ({ start: { date: s }, end: { date: e } });
  it('uses the current cycle', () => {
    expect(rangeFromCropCycles({ current: cyc('2026-03-12', '2026-11-30'), next: null }, NOW))
      .toEqual({ source: 'campaign', range: { start: D('2026-03-12'), end: D('2026-11-30') } });
  });
  it('open-ended current cycle ends 30 days ahead', () => {
    expect(rangeFromCropCycles({ current: cyc('2026-03-12', null), next: null }, NOW)!.range.end).toBe(NOW + 30 * DAY);
  });
  it('caps the window at 365 days back', () => {
    expect(rangeFromCropCycles({ current: cyc('2015-02-01', null), next: null }, NOW)!.range.start).toBe(NOW - 365 * DAY);
  });
  it('no current cycle → null (caller falls back)', () => {
    expect(rangeFromCropCycles({ current: null, next: cyc('2026-10-15', '2027-06-30') }, NOW)).toBeNull();
    expect(rangeFromCropCycles(null, NOW)).toBeNull();
  });
});
