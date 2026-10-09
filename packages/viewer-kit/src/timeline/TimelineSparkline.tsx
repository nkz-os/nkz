import React from 'react';
import { timeToPct, isInRange, type TimeRange } from './timeScale';

export function TimelineSparkline({ range, points, valueRange, color, height = 24 }: {
  range: TimeRange; points: { time: number; value: number }[];
  valueRange: [number, number]; color: string; height?: number;
}) {
  const inRange = points.filter(p => isInRange(p.time, range) && Number.isFinite(p.value));
  if (inRange.length < 2) return null;
  const [lo, hi] = valueRange;
  const span = hi - lo || 1;
  const pts = inRange
    .map(p => `${timeToPct(p.time, range)},${100 - ((Math.min(hi, Math.max(lo, p.value)) - lo) / span) * 100}`)
    .join(' ');
  return (
    <svg width="100%" height={height} viewBox="0 0 100 100" preserveAspectRatio="none" style={{ display: 'block' }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
