/**
 * Window of the viewer timeline for the selected entity, plus the cursor reset
 * on entity change. Lives in the always-mounted viewer (not in the bottom
 * panel) so opening or closing the panel never moves a user-set cursor.
 */
import { useEffect, useMemo } from 'react';
import { snapToUtcDay } from '@nekazari/viewer-kit';
import { useViewer } from '@/context/ViewerContext';
import { useSlotRegistryOptional } from '@/context/SlotRegistry';
import { resolveTimelineRange } from '@/utils/campaignRange';

export function useViewerTimelineRange(crops: unknown[]) {
  const { selectedEntityId, setCurrentDate } = useViewer();
  const slotRegistry = useSlotRegistryOptional();
  const hasTracks = (slotRegistry?.getVisibleWidgets('timeline-track').length ?? 0) > 0;
  const today = snapToUtcDay(Date.now());

  const resolved = useMemo(
    () => resolveTimelineRange(crops, selectedEntityId ?? '', today),
    [crops, selectedEntityId, today],
  );

  // New entity → cursor at today (or at the end of a window that is already over).
  // Only when a module contributes timeline tracks: otherwise the cursor (and the
  // field-photo window centred on it) belongs to the user and stays untouched.
  useEffect(() => {
    if (selectedEntityId && hasTracks) setCurrentDate(new Date(Math.min(today, resolved.range.end)));
    // Only on entity change: a later range update must not move a user-set cursor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedEntityId]);

  return { ...resolved, today };
}
