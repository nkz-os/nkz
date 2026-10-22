import React from 'react';
import { timeToPct, isInRange, type TimeRange } from './timeScale';

export interface TimelineMarker {
  id: string;
  time: number;
  color: string;
  selected?: boolean;
  title?: string;
  /**
   * Vertical position as a fraction in [0, 1] (0 = bottom, 1 = top), clamped.
   * Omit it to keep the dot centred in the row.
   */
  y?: number;
}

/** Invisible click target, centred on the dot. */
const HIT_PX = 24;
const DOT_PX = 10;
const DOT_SELECTED_PX = 14;
/** Vertical padding (in % of the row) so dots at y = 0 or 1 are not clipped. */
const PAD = 12;

const hasY = (m: TimelineMarker): m is TimelineMarker & { y: number } =>
  typeof m.y === 'number' && Number.isFinite(m.y);

/** Single vertical mapping shared by the dots and the connecting line. */
function topPct(y: number): number {
  const clamped = Math.min(1, Math.max(0, y));
  return PAD + (1 - clamped) * (100 - 2 * PAD);
}

export function TimelineMarkers({ range, markers, onSelect, connect = false, lineColor }: {
  range: TimeRange;
  markers: TimelineMarker[];
  onSelect?: (id: string) => void;
  /** Draw a thin line, behind the dots, through the markers that have `y`, ordered by time. */
  connect?: boolean;
  lineColor?: string;
}) {
  const visible = markers.filter(m => isInRange(m.time, range));

  const linePoints = connect
    ? visible
        .filter(hasY)
        .sort((a, b) => a.time - b.time)
        .map(m => `${timeToPct(m.time, range)},${topPct(m.y)}`)
    : [];

  return (
    <>
      {linePoints.length >= 2 && (
        <svg
          aria-hidden="true"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          className="absolute"
          style={{ left: 0, top: 0, width: '100%', height: '100%', pointerEvents: 'none', overflow: 'visible', zIndex: 0 }}
        >
          <polyline
            points={linePoints.join(' ')}
            fill="none"
            stroke={lineColor ?? 'var(--nkz-color-text-muted, #94a3b8)'}
            strokeWidth={1.5}
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      )}
      {visible.map(m => {
        const dot = m.selected ? DOT_SELECTED_PX : DOT_PX;
        return (
          <button
            key={m.id}
            type="button"
            title={m.title}
            aria-label={m.title}
            aria-pressed={!!m.selected}
            onClick={() => onSelect?.(m.id)}
            className="absolute rounded-full cursor-pointer flex items-center justify-center focus:outline-none focus-visible:ring-2 focus-visible:ring-nkz-accent-base"
            style={{
              left: `${timeToPct(m.time, range)}%`,
              top: hasY(m) ? `${topPct(m.y)}%` : '50%',
              width: HIT_PX, height: HIT_PX,
              marginLeft: -HIT_PX / 2, marginTop: -HIT_PX / 2,
              padding: 0, border: 0, backgroundColor: 'transparent',
              zIndex: m.selected ? 2 : 1,
            }}
          >
            <span
              className="block rounded-full"
              style={{
                width: dot, height: dot, backgroundColor: m.color,
                boxShadow: m.selected ? `0 0 0 2px var(--nkz-color-surface, #fff), 0 0 0 3px ${m.color}` : 'none',
              }}
            />
          </button>
        );
      })}
    </>
  );
}
