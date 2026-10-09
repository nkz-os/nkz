/**
 * Shared time scale for the viewer timeline: the host axis and every module
 * track place things with these helpers, so they line up.
 * All instants are UTC-midnight milliseconds; acquisitions are dated by day.
 */
export type TimeRange = { start: number; end: number };

export const DAY_MS = 86_400_000;
export const TIMELINE_LABEL_VAR = '--nkz-timeline-label-w';

export function isoToUtcMs(iso: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? '');
  if (!m) return NaN;
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

export function utcMsToIso(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

export function snapToUtcDay(ms: number): number {
  return Math.floor(ms / DAY_MS) * DAY_MS;
}

export function isInRange(ms: number, r: TimeRange): boolean {
  return ms >= r.start && ms <= r.end;
}

export function timeToPct(ms: number, r: TimeRange): number {
  const span = Math.max(r.end - r.start, DAY_MS);
  return Math.min(100, Math.max(0, ((ms - r.start) / span) * 100));
}

export function monthTicks(r: TimeRange): number[] {
  const out: number[] = [];
  const d = new Date(r.start);
  let t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
  if (t < r.start) t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
  while (t <= r.end) {
    out.push(t);
    const c = new Date(t);
    t = Date.UTC(c.getUTCFullYear(), c.getUTCMonth() + 1, 1);
  }
  return out;
}

export function nearestTime(times: number[], target: number, maxDistanceMs = Infinity): number | null {
  let best: number | null = null;
  let bestD = Infinity;
  for (const t of times) {
    const d = Math.abs(t - target);
    if (d < bestD) { best = t; bestD = d; }
  }
  return best != null && bestD <= maxDistanceMs ? best : null;
}

export function latestAtOrBefore<T>(items: T[], getTime: (t: T) => number, target: number): T | null {
  let best: T | null = null;
  let bestT = -Infinity;
  for (const it of items) {
    const t = getTime(it);
    if (t <= target && t > bestT) { best = it; bestT = t; }
  }
  return best;
}
