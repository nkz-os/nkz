// =============================================================================
// Viewer Timeline - shared time axis + one row per module (timeline-track slot)
// =============================================================================
import React from 'react';
import { useTranslation } from 'react-i18next';
import type { TimelineTrackProps } from '@nekazari/sdk';
import { TimelineAxis, TIMELINE_LABEL_VAR, snapToUtcDay, type TimeRange } from '@nekazari/viewer-kit';
import { useViewer } from '@/context/ViewerContext';
import { useSlotRegistryOptional } from '@/context/SlotRegistry';
import { SlotRenderer } from '@/components/SlotRenderer';

const LABEL_W = '112px';

interface Props {
  range: TimeRange;
  source: 'campaign' | 'default';
  today: number;
}

export const ViewerTimeline: React.FC<Props> = ({ range, source, today }) => {
  const { t, i18n } = useTranslation();
  const { selectedEntityId, selectedEntityType, currentDate, setCurrentDate } = useViewer();
  const slotRegistry = useSlotRegistryOptional();
  const hasTracks = (slotRegistry?.getVisibleWidgets('timeline-track').length ?? 0) > 0;

  if (!selectedEntityId || !hasTracks) return null;

  const cursor = snapToUtcDay(currentDate.getTime());
  const trackProps: TimelineTrackProps = {
    entityId: selectedEntityId,
    entityType: selectedEntityType,
    range,
    cursor,
    onCursorChange: ms => setCurrentDate(new Date(ms)),
    forecastFrom: today <= range.end ? today : undefined,
  };

  return (
    <div className="flex flex-col gap-1 px-3 py-2 overflow-x-hidden" style={{ [TIMELINE_LABEL_VAR]: LABEL_W } as React.CSSProperties}>
      <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
        <span>{t(source === 'campaign' ? 'viewer.timeline.campaign' : 'viewer.timeline.lastDays')}</span>
        <span className="text-slate-700 dark:text-slate-200">
          {new Date(cursor).toLocaleDateString(i18n.language, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })}
        </span>
      </div>
      <div className="flex items-center">
        <div style={{ width: LABEL_W, flexShrink: 0 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <TimelineAxis
            range={range}
            cursor={cursor}
            onCursorChange={trackProps.onCursorChange}
            forecastFrom={trackProps.forecastFrom}
            locale={i18n.language}
            ariaLabel={t('viewer.timeline.axis')}
          />
        </div>
      </div>
      <SlotRenderer
        slot="timeline-track"
        className="flex flex-col gap-1"
        additionalProps={trackProps as unknown as Record<string, unknown>}
        resetKeys={[selectedEntityId]}
      />
    </div>
  );
};

export default ViewerTimeline;
