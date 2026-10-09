import { describe, it, expect } from 'vitest';
import {
  DAY_MS, isoToUtcMs, utcMsToIso, snapToUtcDay, timeToPct, isInRange,
  monthTicks, nearestTime, latestAtOrBefore,
} from '../timeScale';

const R = { start: Date.UTC(2026, 3, 1), end: Date.UTC(2026, 6, 1) }; // 1 abr – 1 jul

describe('timeScale', () => {
  it('round-trips ISO days in UTC regardless of time part', () => {
    expect(utcMsToIso(isoToUtcMs('2026-05-03'))).toBe('2026-05-03');
    expect(isoToUtcMs('2026-05-03T23:30:00+05:00')).toBe(isoToUtcMs('2026-05-03'));
    expect(Number.isNaN(isoToUtcMs('nope'))).toBe(true);
  });

  it('snaps to UTC midnight', () => {
    expect(snapToUtcDay(Date.UTC(2026, 4, 3, 17, 12))).toBe(Date.UTC(2026, 4, 3));
  });

  it('maps time to percent and clamps outside the range', () => {
    expect(timeToPct(R.start, R)).toBe(0);
    expect(timeToPct(R.end, R)).toBe(100);
    expect(timeToPct(R.start - 10 * DAY_MS, R)).toBe(0);
    expect(timeToPct(R.end + 10 * DAY_MS, R)).toBe(100);
    expect(isInRange(R.start - 1, R)).toBe(false);
  });

  it('lists first-of-month ticks inside the range', () => {
    expect(monthTicks(R).map(utcMsToIso)).toEqual(['2026-04-01', '2026-05-01', '2026-06-01', '2026-07-01']);
  });

  it('finds the nearest time, honouring a max distance', () => {
    const ts = [Date.UTC(2026, 3, 5), Date.UTC(2026, 3, 20)];
    expect(nearestTime(ts, Date.UTC(2026, 3, 14))).toBe(ts[1]);
    expect(nearestTime(ts, Date.UTC(2026, 5, 30), 10 * DAY_MS)).toBeNull();
    expect(nearestTime([], 0)).toBeNull();
  });

  it('returns the latest item at or before a target', () => {
    const items = [{ t: 10 }, { t: 30 }, { t: 20 }];
    expect(latestAtOrBefore(items, i => i.t, 25)).toEqual({ t: 20 });
    expect(latestAtOrBefore(items, i => i.t, 5)).toBeNull();
  });
});
