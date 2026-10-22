/**
 * One row of the viewer timeline: a fixed label column and a track area that
 * shares the host axis scale. The label width comes from the host container
 * through a CSS variable, so rows from separately built modules still align.
 */
import React from 'react';
import { TIMELINE_LABEL_VAR, timeToPct, isInRange, type TimeRange } from './timeScale';

interface Props {
  label: React.ReactNode;
  range: TimeRange;
  cursor: number;
  height?: number;
  children?: React.ReactNode;
}

export function TimelineTrackRow({ label, range, cursor, height = 24, children }: Props) {
  return (
    <div className="flex items-center" style={{ minHeight: height }}>
      <div
        className="text-nkz-xs text-nkz-text-secondary truncate"
        style={{ width: `var(${TIMELINE_LABEL_VAR}, 112px)`, flexShrink: 0, paddingRight: 8 }}
      >
        {label}
      </div>
      <div className="relative" style={{ flex: 1, minWidth: 0, height }}>
        {children}
        {isInRange(cursor, range) && (
          <div
            className="absolute bg-nkz-accent-base"
            style={{ left: `${timeToPct(cursor, range)}%`, top: 0, bottom: 0, width: 1, pointerEvents: 'none' }}
          />
        )}
      </div>
    </div>
  );
}
