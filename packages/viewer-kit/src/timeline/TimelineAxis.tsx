/**
 * Host-owned time axis: month gridlines, today marker, striped forecast zone
 * and the shared cursor. Click or drag moves the cursor (snapped to a UTC day);
 * ←/→ step one day.
 */
import React, { useCallback, useRef } from 'react';
import { DAY_MS, monthTicks, snapToUtcDay, timeToPct, isInRange, type TimeRange } from './timeScale';

interface Props {
  range: TimeRange;
  cursor: number;
  onCursorChange: (ms: number) => void;
  forecastFrom?: number;
  locale?: string;
  ariaLabel: string;
}

const clamp = (ms: number, r: TimeRange) => Math.min(r.end, Math.max(r.start, ms));

export function TimelineAxis({ range, cursor, onCursorChange, forecastFrom, locale, ariaLabel }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  const fromClientX = useCallback((clientX: number) => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    const ms = range.start + ((clientX - rect.left) / rect.width) * (range.end - range.start);
    onCursorChange(clamp(snapToUtcDay(ms), range));
  }, [range, onCursorChange]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    onCursorChange(clamp(snapToUtcDay(cursor) + (e.key === 'ArrowLeft' ? -DAY_MS : DAY_MS), range));
  };

  const fmt = (ms: number) => new Date(ms).toLocaleDateString(locale, { month: 'short', timeZone: 'UTC' });
  const now = Date.now();

  return (
    <div
      ref={ref}
      role="slider"
      aria-label={ariaLabel}
      aria-valuemin={range.start}
      aria-valuemax={range.end}
      aria-valuenow={cursor}
      aria-valuetext={new Date(cursor).toLocaleDateString(locale, { timeZone: 'UTC' })}
      tabIndex={0}
      onKeyDown={onKeyDown}
      onPointerDown={e => { dragging.current = true; e.currentTarget.setPointerCapture?.(e.pointerId); fromClientX(e.clientX); }}
      onPointerMove={e => { if (dragging.current) fromClientX(e.clientX); }}
      onPointerUp={() => { dragging.current = false; }}
      onPointerCancel={() => { dragging.current = false; }}
      className="relative cursor-pointer select-none focus:outline-none"
      style={{ height: 28, touchAction: 'none' }}
    >
      {forecastFrom != null && forecastFrom < range.end && (
        <div
          className="absolute"
          style={{
            left: `${timeToPct(forecastFrom, range)}%`, right: 0, top: 0, bottom: 0, pointerEvents: 'none',
            background: 'repeating-linear-gradient(-45deg, transparent 0 4px, var(--nkz-color-border, rgba(148,163,184,.3)) 4px 6px)',
          }}
        />
      )}
      <div className="absolute bg-nkz-border" style={{ left: 0, right: 0, bottom: 8, height: 1 }} />
      {monthTicks(range).map(t => (
        <div key={t} className="absolute" style={{ left: `${timeToPct(t, range)}%`, bottom: 4, pointerEvents: 'none' }}>
          <div className="bg-nkz-border" style={{ width: 1, height: 9 }} />
          <span className="absolute text-nkz-text-muted whitespace-nowrap" style={{ bottom: 10, left: 3, fontSize: 10 }}>{fmt(t)}</span>
        </div>
      ))}
      {isInRange(now, range) && (
        <div
          className="absolute"
          style={{ left: `${timeToPct(now, range)}%`, top: 0, bottom: 0, borderLeft: '1px dashed var(--nkz-color-text-muted, #94a3b8)', pointerEvents: 'none' }}
        />
      )}
      <div
        className="absolute bg-nkz-accent-base"
        style={{ left: `${timeToPct(cursor, range)}%`, top: 0, bottom: 0, width: 2, marginLeft: -1, pointerEvents: 'none' }}
      />
    </div>
  );
}
