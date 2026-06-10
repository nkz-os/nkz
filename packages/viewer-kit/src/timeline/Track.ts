// Track type definitions for the TimelineShell system

export interface TrackMarker {
  id: string;
  time: number; // unix timestamp ms
  label?: string;
  color?: string;
  icon?: string;
}

export interface TrackRange {
  id: string;
  start: number; // unix timestamp ms
  end: number;
  label?: string;
  color?: string;
  fill?: 'solid' | 'striped';
}

export interface TrackSparklinePoint {
  time: number;
  value: number;
}

export interface BaseTrack {
  id: string;
  moduleId: string;
  label: string;
  accent?: { base: string; soft: string; strong: string };
  visible?: boolean;
}

export interface MarkersTrack extends BaseTrack {
  type: 'markers';
  data: TrackMarker[];
}

export interface RangeTrack extends BaseTrack {
  type: 'range';
  data: TrackRange[];
}

export interface SparklineTrack extends BaseTrack {
  type: 'sparkline';
  data: TrackSparklinePoint[];
  valueRange?: [number, number];
}

export interface ForecastTrack extends BaseTrack {
  type: 'forecast';
  horizon: number; // ms into future from forecast boundary
  confidence?: number; // 0-1
  subtype: 'markers' | 'range' | 'sparkline';
  data: TrackMarker[] | TrackRange[] | TrackSparklinePoint[];
}

export type Track = MarkersTrack | RangeTrack | SparklineTrack | ForecastTrack;
