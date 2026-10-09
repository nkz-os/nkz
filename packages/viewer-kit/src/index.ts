// Viewer shells
export { SlotShell, SlotShellCompact } from './viewer/SlotShell';
export { ModuleGroup } from './viewer/ModuleGroup';
export { SidebarShell, type SidebarState, type SidebarLabels } from './viewer/SidebarShell';

// Page layout
export { PageShell } from './page/PageShell';
export { PageHeader } from './page/PageHeader';
export { PageNav } from './page/PageNav';
export { PageSection } from './page/PageSection';
export { PageFooter } from './page/PageFooter';

// Hooks
export { useModuleGroupState } from './hooks/useModuleGroupState';
export { useScrollHeaderState } from './hooks/useScrollHeaderState';
export { useKeyboardShortcut } from './hooks/useKeyboardShortcut';

// Timeline
export { TimelineShell } from './timeline/TimelineShell';
export { TimelineCanvas } from './timeline/TimelineCanvas';
export { TimelinePlayback } from './timeline/TimelinePlayback';
export { TimelineTrackList } from './timeline/TimelineTrackList';
export { TimelineForecastZone } from './timeline/TimelineForecastZone';
export { useTimelineCursor } from './timeline/useTimelineCursor';
export type {
  Track,
  TrackMarker,
  TrackRange,
  TrackSparklinePoint,
  MarkersTrack,
  RangeTrack,
  SparklineTrack,
  ForecastTrack,
} from './timeline/Track';

export { TimelineAxis } from './timeline/TimelineAxis';
export { TimelineTrackRow } from './timeline/TimelineTrackRow';
export { TimelineMarkers, type TimelineMarker } from './timeline/TimelineMarkers';
export { TimelineSparkline } from './timeline/TimelineSparkline';
export {
  type TimeRange, DAY_MS, TIMELINE_LABEL_VAR, isoToUtcMs, utcMsToIso, snapToUtcDay,
  timeToPct, isInRange, monthTicks, nearestTime, latestAtOrBefore,
} from './timeline/timeScale';

export { CapabilityValue } from './components/CapabilityValue';
export type { CapabilityValueProps } from './components/CapabilityValue';
export { EntitlementGuard } from './components/EntitlementGuard';
export type { EntitlementGuardProps } from './components/EntitlementGuard';
