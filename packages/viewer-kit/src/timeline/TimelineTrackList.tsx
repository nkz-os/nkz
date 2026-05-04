/**
 * Renders visible module tracks in expanded mode.
 * Each track shows a label + data visualization (markers, ranges, or sparkline).
 */
import React from 'react';
import clsx from 'clsx';
import type {
  Track,
  TrackMarker,
  TrackRange,
  TrackSparklinePoint,
} from './Track';

interface TimelineTrackListProps {
  tracks: Track[];
  startTime: number;
  endTime: number;
  cursor: number;
  forecastFrom?: number;
  maxVisible?: number;
  className?: string;
}

// ---------------------------------------------------------------------------
// Position helpers
// ---------------------------------------------------------------------------

function timeToPercent(t: number, start: number, end: number): number {
  return ((t - start) / (end - start)) * 100;
}

// ---------------------------------------------------------------------------
// Sub-renderers for each track type
// ---------------------------------------------------------------------------

function MarkerDots({
  data,
  startTime,
  endTime,
  accent,
}: {
  data: TrackMarker[];
  startTime: number;
  endTime: number;
  accent?: string;
}) {
  return (
    <div className="relative h-4">
      {data.map((m) => (
        <div
          key={m.id}
          className="absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full"
          style={{
            left: `${timeToPercent(m.time, startTime, endTime)}%`,
            backgroundColor: m.color || accent || 'var(--nkz-color-accent-base)',
          }}
          title={m.label}
        />
      ))}
    </div>
  );
}

function RangeBars({
  data,
  startTime,
  endTime,
  accent,
}: {
  data: TrackRange[];
  startTime: number;
  endTime: number;
  accent?: string;
}) {
  return (
    <div className="relative h-4">
      {data.map((r) => {
        const leftPct = timeToPercent(r.start, startTime, endTime);
        const rightPct = timeToPercent(r.end, startTime, endTime);
        const width = Math.max(0.5, rightPct - leftPct);
        return (
          <div
            key={r.id}
            className="absolute top-1/2 -translate-y-1/2 h-2 rounded-sm opacity-80"
            style={{
              left: `${leftPct}%`,
              width: `${width}%`,
              backgroundColor:
                r.color || accent || 'var(--nkz-color-accent-base)',
            }}
            title={r.label}
          />
        );
      })}
    </div>
  );
}

function SparklineSVG({
  data,
  startTime,
  endTime,
  accent,
  valueRange,
}: {
  data: TrackSparklinePoint[];
  startTime: number;
  endTime: number;
  accent?: string;
  valueRange?: [number, number];
}) {
  if (data.length < 2) return <div className="h-4" />;

  const min = valueRange?.[0] ?? Math.min(...data.map((d) => d.value));
  const max = valueRange?.[1] ?? Math.max(...data.map((d) => d.value));
  const range = max - min || 1;

  const points = data
    .map((d) => {
      const x = timeToPercent(d.time, startTime, endTime);
      const y = 100 - ((d.value - min) / range) * 100;
      return `${x},${y}`;
    })
    .join(' ');

  return (
    <div className="h-5">
      <svg
        width="100%"
        height="100%"
        preserveAspectRatio="none"
        className="overflow-visible"
      >
        <polyline
          points={points}
          fill="none"
          stroke={accent || 'var(--nkz-color-accent-base)'}
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main track list
// ---------------------------------------------------------------------------

export function TimelineTrackList({
  tracks,
  startTime,
  endTime,
  cursor,
  forecastFrom: _forecastFrom,
  maxVisible = 3,
  className,
}: TimelineTrackListProps) {
  const visibleTracks = tracks
    .filter((t) => t.visible !== false)
    .slice(0, maxVisible);
  const hiddenCount =
    tracks.filter((t) => t.visible !== false).length - maxVisible;

  if (visibleTracks.length === 0) return null;

  return (
    <div className={clsx('flex flex-col gap-nkz-tight', className)}>
      {visibleTracks.map((track) => {
        const accentColor = track.accent?.base;
        const color = accentColor || 'var(--nkz-color-accent-base)';

        return (
          <div key={track.id} className="flex items-center gap-nkz-inline">
            {/* Track label */}
            <span
              className="text-nkz-xs text-nkz-text-secondary min-w-[120px] max-w-[160px] truncate flex-shrink-0"
              style={{
                borderLeft: `3px solid ${color}`,
                paddingLeft: 'var(--nkz-space-tight, 4px)',
              }}
            >
              {track.label}
            </span>

            {/* Track visualization */}
            <div className="flex-1 min-w-0 relative">
              {track.type === 'markers' && (
                <MarkerDots
                  data={track.data as TrackMarker[]}
                  startTime={startTime}
                  endTime={endTime}
                  accent={color}
                />
              )}
              {track.type === 'range' && (
                <RangeBars
                  data={track.data as TrackRange[]}
                  startTime={startTime}
                  endTime={endTime}
                  accent={color}
                />
              )}
              {track.type === 'sparkline' && (
                <SparklineSVG
                  data={track.data as TrackSparklinePoint[]}
                  startTime={startTime}
                  endTime={endTime}
                  accent={color}
                  valueRange={
                    'valueRange' in track
                      ? (track as SparklineTrackWithRange).valueRange
                      : undefined
                  }
                />
              )}
              {track.type === 'forecast' && (
                <div className="opacity-60">
                  {track.subtype === 'markers' && (
                    <MarkerDots
                      data={track.data as TrackMarker[]}
                      startTime={startTime}
                      endTime={endTime}
                      accent={color}
                    />
                  )}
                  {track.subtype === 'range' && (
                    <RangeBars
                      data={track.data as TrackRange[]}
                      startTime={startTime}
                      endTime={endTime}
                      accent={color}
                    />
                  )}
                  {track.subtype === 'sparkline' && (
                    <SparklineSVG
                      data={track.data as TrackSparklinePoint[]}
                      startTime={startTime}
                      endTime={endTime}
                      accent={color}
                      valueRange={
                        'valueRange' in track
                          ? (track as SparklineTrackWithRange).valueRange
                          : undefined
                      }
                    />
                  )}
                </div>
              )}

              {/* Cursor indicator line */}
              <div
                className="absolute top-0 bottom-0 w-px bg-nkz-accent-base pointer-events-none"
                style={{
                  left: `${timeToPercent(cursor, startTime, endTime)}%`,
                }}
              />
            </div>
          </div>
        );
      })}

      {hiddenCount > 0 && (
        <div className="text-nkz-xs text-nkz-text-muted pl-[128px]">
          + {hiddenCount} more tracks
        </div>
      )}
    </div>
  );
}

// Helper type to extract valueRange from SparklineTrack
interface SparklineTrackWithRange {
  valueRange?: [number, number];
}
