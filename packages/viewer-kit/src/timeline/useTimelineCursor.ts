// Timeline cursor context and hook for external sync (e.g. map-layer)
import { createContext, useContext } from 'react';

export interface TimelineCursorState {
  cursor: number; // unix timestamp ms
  isPlaying: boolean;
  speed: number;
  startTime: number;
  endTime: number;
  forecastFrom: number | null;
}

const TimelineCursorContext = createContext<TimelineCursorState | null>(null);

export const TimelineCursorProvider = TimelineCursorContext.Provider;

export function useTimelineCursor(): TimelineCursorState {
  const ctx = useContext(TimelineCursorContext);
  if (!ctx) throw new Error('useTimelineCursor must be used within <TimelineShell>');
  return ctx;
}
