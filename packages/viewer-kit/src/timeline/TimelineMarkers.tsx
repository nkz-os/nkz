import React from 'react';
import { timeToPct, isInRange, type TimeRange } from './timeScale';

export interface TimelineMarker { id: string; time: number; color: string; selected?: boolean; title?: string }

export function TimelineMarkers({ range, markers, onSelect }: {
  range: TimeRange; markers: TimelineMarker[]; onSelect?: (id: string) => void;
}) {
  return (
    <>
      {markers.filter(m => isInRange(m.time, range)).map(m => {
        const size = m.selected ? 12 : 8;
        return (
          <button
            key={m.id}
            type="button"
            title={m.title}
            aria-label={m.title}
            aria-pressed={!!m.selected}
            onClick={() => onSelect?.(m.id)}
            className="absolute rounded-full cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-nkz-accent-base"
            style={{
              left: `${timeToPct(m.time, range)}%`, top: '50%', width: size, height: size,
              marginLeft: -size / 2, marginTop: -size / 2, backgroundColor: m.color,
              boxShadow: m.selected ? `0 0 0 2px var(--nkz-color-surface, #fff), 0 0 0 3px ${m.color}` : 'none',
              zIndex: m.selected ? 2 : 1,
            }}
          />
        );
      })}
    </>
  );
}
