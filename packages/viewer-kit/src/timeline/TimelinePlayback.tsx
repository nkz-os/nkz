/**
 * Playback controls: play/pause, skip, speed selector, and current timestamp display.
 */
import React from 'react';
import clsx from 'clsx';
import { IconButton } from '@nekazari/ui-kit';

interface TimelinePlaybackProps {
  isPlaying: boolean;
  onTogglePlay: () => void;
  speed: number;
  onSpeedChange: (speed: number) => void;
  cursor: number;
  onSkipBack: () => void;
  onSkipForward: () => void;
  className?: string;
}

const speeds = [0.5, 1, 2, 5, 10];

export function TimelinePlayback({
  isPlaying,
  onTogglePlay,
  speed,
  onSpeedChange,
  cursor,
  onSkipBack,
  onSkipForward,
  className,
}: TimelinePlaybackProps) {
  const d = new Date(cursor);
  const dateStr = d.toLocaleDateString('es-ES', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
  const timeStr = d.toLocaleTimeString('es-ES', {
    hour: '2-digit',
    minute: '2-digit',
  });

  return (
    <div className={clsx('flex items-center gap-nkz-inline', className)}>
      {/* Skip back 1 step */}
      <IconButton aria-label="Retroceder" size="sm" onClick={onSkipBack}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
          <path d="M3 3v8M5 7l6-4v8L5 7z" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </IconButton>

      {/* Play/Pause */}
      <IconButton
        aria-label={isPlaying ? 'Pausa' : 'Reproducir'}
        size="sm"
        onClick={onTogglePlay}
      >
        {isPlaying ? (
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <rect x="3" y="2" width="2.5" height="10" rx="0.5" fill="currentColor" />
            <rect x="8.5" y="2" width="2.5" height="10" rx="0.5" fill="currentColor" />
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <path d="M4 2.5v9l7-4.5L4 2.5z" fill="currentColor" />
          </svg>
        )}
      </IconButton>

      {/* Skip forward 1 step */}
      <IconButton aria-label="Avanzar" size="sm" onClick={onSkipForward}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
          <path d="M11 3v8M9 7l-6-4v8l6-4z" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </IconButton>

      {/* Date/time display */}
      <span className="text-nkz-sm text-nkz-text-primary font-medium tabular-nums min-w-[180px]">
        {dateStr} {timeStr}
      </span>

      {/* Speed selector */}
      <div className="flex items-center gap-nkz-tight">
        {speeds.map((s) => (
          <button
            key={s}
            onClick={() => onSpeedChange(s)}
            className={clsx(
              'px-nkz-tight py-0.5 text-nkz-xs rounded-nkz-sm transition-colors duration-nkz-fast',
              s === speed
                ? 'bg-nkz-accent-base text-nkz-text-on-accent'
                : 'text-nkz-text-muted hover:text-nkz-text-primary hover:bg-nkz-surface-sunken'
            )}
          >
            {s}x
          </button>
        ))}
      </div>
    </div>
  );
}
