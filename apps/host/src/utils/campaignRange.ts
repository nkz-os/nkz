/**
 * Default window of the viewer timeline for a parcel: its active campaign
 * (AgriCrop planting → expected end), else the last 90 days plus 30 ahead.
 * AgriCrop comes from two writers: assign-crop (harvestDate) and crop-plan
 * segments (status lifecycle, expectedTerminationDate / terminationDate).
 */
import { DAY_MS, isoToUtcMs, type TimeRange } from '@nekazari/viewer-kit';
import { isRelatedToParcel } from './entityRelations';

const DEFAULT_PAST_DAYS = 90;
const DEFAULT_FUTURE_DAYS = 30;
const DEFAULT_SEASON_DAYS = 180;
const MAX_PAST_DAYS = 365;
const MAX_FUTURE_DAYS = 365;
const CLOSED = new Set(['planned', 'harvested', 'terminated']);
const END_ATTRS = ['harvestDate', 'expectedTerminationDate', 'terminationDate'] as const;

function unwrap(attr: unknown): unknown {
  let v: unknown = attr;
  if (v && typeof v === 'object' && 'value' in (v as object)) v = (v as { value: unknown }).value;
  if (v && typeof v === 'object' && '@value' in (v as object)) v = (v as { '@value': unknown })['@value'];
  return v;
}

function dateAttr(attr: unknown): number {
  const v = unwrap(attr);
  return typeof v === 'string' ? isoToUtcMs(v) : NaN;
}

export function resolveTimelineRange(
  crops: unknown[], parcelId: string, now: number,
): { range: TimeRange; source: 'campaign' | 'default' } {
  const candidates = crops
    .filter(c => isRelatedToParcel(c, parcelId))
    .map(c => {
      const rec = c as Record<string, unknown>;
      const status = unwrap(rec.status);
      return { rec, status: typeof status === 'string' ? status : null, start: dateAttr(rec.plantingDate) };
    })
    .filter(c => Number.isFinite(c.start) && c.start <= now && !(c.status && CLOSED.has(c.status)));

  const active = candidates.filter(c => c.status === 'active');
  const pool = active.length > 0 ? active : candidates;
  const best = pool.reduce<(typeof pool)[number] | null>((a, c) => (!a || c.start > a.start ? c : a), null);

  if (best) {
    const declared = END_ATTRS.map(k => dateAttr(best.rec[k])).find(t => Number.isFinite(t) && t > best.start);
    let end = declared ?? best.start + DEFAULT_SEASON_DAYS * DAY_MS;
    if (end < now && best.status === 'active') end = now + DEFAULT_FUTURE_DAYS * DAY_MS;
    if (end >= now) {
      // Perennial crops (e.g., orchards planted decades ago) keep their original plantingDate for selection,
      // but the returned range is capped to the last 365 days to avoid overwhelming the timeline.
      const cappedStart = Math.max(best.start, now - MAX_PAST_DAYS * DAY_MS);
      // A far-future or mistyped end date must not stretch the axis over decades.
      const cappedEnd = Math.min(end, now + MAX_FUTURE_DAYS * DAY_MS);
      return { range: { start: cappedStart, end: cappedEnd }, source: 'campaign' };
    }
  }
  return {
    range: { start: now - DEFAULT_PAST_DAYS * DAY_MS, end: now + DEFAULT_FUTURE_DAYS * DAY_MS },
    source: 'default',
  };
}

export interface CycleDto { start: { date: string | null }; end: { date: string | null } }
export interface CropCycles { current: CycleDto | null; next: CycleDto | null }

/** Window of the parcel's current crop cycle as resolved by the platform; without one,
 * the recent past plus the next cycle, so the present and the coming campaign show together. */
export function rangeFromCropCycles(
  cycles: CropCycles | null, now: number,
): { range: TimeRange; source: 'campaign' } | null {
  const cur = cycles?.current;
  if (!cur) return rangeOfNextCycle(cycles?.next ?? null, now);
  const start = cur.start.date ? isoToUtcMs(cur.start.date) : NaN;
  if (!Number.isFinite(start)) return null;
  const declared = cur?.end.date ? isoToUtcMs(cur.end.date) : NaN;
  const end = Number.isFinite(declared) && declared >= now ? declared : now + DEFAULT_FUTURE_DAYS * DAY_MS;
  return {
    range: {
      start: Math.max(start, now - MAX_PAST_DAYS * DAY_MS),
      end: Math.min(end, now + MAX_FUTURE_DAYS * DAY_MS),
    },
    source: 'campaign',
  };
}

function rangeOfNextCycle(next: CycleDto | null, now: number): { range: TimeRange; source: 'campaign' } | null {
  const start = next?.start.date ? isoToUtcMs(next.start.date) : NaN;
  if (!Number.isFinite(start)) return null;
  const declared = next?.end.date ? isoToUtcMs(next.end.date) : NaN;
  const end = Number.isFinite(declared) && declared > start ? declared : start + DEFAULT_SEASON_DAYS * DAY_MS;
  return {
    range: { start: now - DEFAULT_PAST_DAYS * DAY_MS, end: Math.min(end, now + MAX_FUTURE_DAYS * DAY_MS) },
    source: 'campaign',
  };
}
