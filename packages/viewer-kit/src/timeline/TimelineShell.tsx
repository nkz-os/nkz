/**
 * TimelineShell — main compound component composing canvas axis, playback,
 * track list, and forecast zone into a single timeline panel.
 *
 * Variants:
 *   - docked:   sits in the main content area
 *   - floating: absolute-positioned at the bottom (for map overlays)
 *   - minimal:  compact variant without expand/collapse
 */
import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import clsx from 'clsx';
import { TimelineCanvas } from './TimelineCanvas';
import { TimelinePlayback } from './TimelinePlayback';
import { TimelineTrackList } from './TimelineTrackList';
import { TimelineCursorProvider, type TimelineCursorState } from './useTimelineCursor';
import type { Track } from './Track';

type TimelineVariant = 'docked' | 'floating' | 'minimal';

interface TimelineShellProps {
  startTime: number;
  endTime: number;
  cursor: number;
  onCursorChange: (time: number) => void;
  isPlaying?: boolean;
  onPlayingChange?: (playing: boolean) => void;
  speed?: number;
  onSpeedChange?: (speed: number) => void;
  forecastFrom?: number;
  snapping?: 'hour' | 'day' | 'week' | null;
  tracks?: Track[];
  maxVisibleTracks?: number;
  variant?: TimelineVariant;
  className?: string;
  children?: React.ReactNode;
}

// ---------------------------------------------------------------------------

function TimelineShellRoot({
  startTime,
  endTime,
  cursor,
  onCursorChange,
  isPlaying = false,
  onPlayingChange,
  speed = 1,
  onSpeedChange,
  forecastFrom,
  snapping = 'day',
  tracks = [],
  maxVisibleTracks = 3,
  variant = 'docked',
  className,
  children,
}: TimelineShellProps) {
  const [expanded, setExpanded] = useState(false);
  const [clockOnly, setClockOnly] = useState(false);
  const animFrameRef = useRef<number | null>(null);
  const lastTickRef = useRef<number>(0);

  // --------------- Playback animation loop ---------------

  useEffect(() => {
    if (!isPlaying) {
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
      return;
    }

    lastTickRef.current = performance.now();

    const tick = (now: number) => {
      const delta = now - lastTickRef.current;
      lastTickRef.current = now;
      // Advance cursor proportionally to speed
      const advance = ((endTime - startTime) / 10000) * speed * (delta / 16.67);
      const newCursor = cursor + advance > endTime ? startTime : cursor + advance;
      onCursorChange(newCursor);
      animFrameRef.current = requestAnimationFrame(tick);
    };

    animFrameRef.current = requestAnimationFrame(tick);
    return () => {
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
    };
  }, [isPlaying, speed, cursor, startTime, endTime, onCursorChange]);

  // --------------- Callbacks ---------------

  const handleTogglePlay = useCallback(
    () => onPlayingChange?.(!isPlaying),
    [isPlaying, onPlayingChange],
  );

  const handleSkipBack = useCallback(() => {
    const step = (endTime - startTime) / 20;
    onCursorChange(Math.max(startTime, cursor - step));
  }, [cursor, startTime, endTime, onCursorChange]);

  const handleSkipForward = useCallback(() => {
    const step = (endTime - startTime) / 20;
    onCursorChange(Math.min(endTime, cursor + step));
  }, [cursor, startTime, endTime, onCursorChange]);

  const handleSpeedChange = useCallback(
    (s: number) => onSpeedChange?.(s),
    [onSpeedChange],
  );

  // --------------- Context value ---------------

  const cursorState: TimelineCursorState = useMemo(
    () => ({
      cursor,
      isPlaying,
      speed,
      startTime,
      endTime,
      forecastFrom: forecastFrom ?? null,
    }),
    [cursor, isPlaying, speed, startTime, endTime, forecastFrom],
  );

  // --------------- Derived ---------------

  const isFloating = variant === 'floating';
  const isMinimal = variant === 'minimal';
  const showTracks = !clockOnly && expanded && tracks.length > 0;
  // floating and minimal variants don't support clock-only toggle (always show axis)
  const effectiveClockOnly = isFloating || isMinimal ? false : clockOnly;

  return (
    <TimelineCursorProvider value={cursorState}>
      <div
        className={clsx(
          'bg-nkz-surface border border-nkz-border rounded-nkz-lg',
          'shadow-nkz-lg transition-all duration-nkz-normal',
          !isFloating && 'mx-nkz-section mb-nkz-stack',
          isFloating && 'absolute z-50',
          isMinimal && 'minimal',
          effectiveClockOnly && 'h-7 overflow-hidden',
          className,
        )}
        style={
          isFloating
            ? { bottom: '1rem', left: '1rem', right: '1rem' }
            : undefined
        }
      >
        {/* Control bar */}
        <div className="flex items-center justify-between px-nkz-stack py-nkz-tight border-b border-nkz-border">
          <div className="flex-1 min-w-0">
            {!effectiveClockOnly && (
              <TimelinePlayback
                isPlaying={isPlaying}
                onTogglePlay={handleTogglePlay}
                speed={speed}
                onSpeedChange={handleSpeedChange}
                cursor={cursor}
                onSkipBack={handleSkipBack}
                onSkipForward={handleSkipForward}
              />
            )}
            {effectiveClockOnly && (
              <span className="text-nkz-xs text-nkz-text-muted tabular-nums">
                {new Date(cursor).toLocaleString('es-ES', {
                  day: '2-digit',
                  month: 'short',
                  year: 'numeric',
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </span>
            )}
          </div>

          {/* Toggle buttons */}
          <div className="flex items-center gap-nkz-tight flex-shrink-0 ml-nkz-inline">
            {!effectiveClockOnly && !isMinimal && (
              <button
                onClick={() => setExpanded((e) => !e)}
                className="text-nkz-text-muted hover:text-nkz-text-primary p-nkz-tight rounded-nkz-sm transition-colors"
                aria-label={expanded ? 'Compact timeline' : 'Expand timeline'}
              >
                {expanded ? (
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                    <path d="M3 9l4-4 4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                ) : (
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                    <path d="M3 5l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                )}
              </button>
            )}
            {!isFloating && !isMinimal && (
              <button
                onClick={() => setClockOnly((c) => !c)}
                className="text-nkz-text-muted hover:text-nkz-text-primary p-nkz-tight rounded-nkz-sm transition-colors"
                aria-label={clockOnly ? 'Show full timeline' : 'Show clock only'}
                title={clockOnly ? 'Mostrar timeline completa' : 'Solo reloj'}
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                  <circle cx="7" cy="7" r="5.5" stroke="currentColor" strokeWidth="1.3" />
                  <path d="M7 4.5v3l2 1" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                </svg>
              </button>
            )}
          </div>
        </div>

        {/* Canvas axis */}
        {!effectiveClockOnly && (
          <div className="px-nkz-stack pt-nkz-tight">
            <TimelineCanvas
              startTime={startTime}
              endTime={endTime}
              cursor={cursor}
              onCursorChange={onCursorChange}
              forecastFrom={forecastFrom}
              snapping={snapping}
              height={expanded ? 40 : 32}
            />
          </div>
        )}

        {/* Expanded tracks */}
        {showTracks && (
          <div className="px-nkz-stack pb-nkz-stack">
            <TimelineTrackList
              tracks={tracks}
              startTime={startTime}
              endTime={endTime}
              cursor={cursor}
              forecastFrom={forecastFrom}
              maxVisible={maxVisibleTracks}
            />
          </div>
        )}

        {/* Custom children */}
        {children}
      </div>
    </TimelineCursorProvider>
  );
}

export const TimelineShell = TimelineShellRoot;
