/**
 * Window of the viewer timeline for the selected entity, plus the cursor reset
 * on entity change. Lives in the always-mounted viewer (not in the bottom
 * panel) so opening or closing the panel never moves a user-set cursor.
 */
import { useEffect, useMemo } from 'react';
import { snapToUtcDay } from '@nekazari/viewer-kit';
import { useViewer } from '@/context/ViewerContext';
import { resolveTimelineRange } from '@/utils/campaignRange';

export function useViewerTimelineRange(crops: unknown[]) {
  const { selectedEntityId, setCurrentDate } = useViewer();
  const today = snapToUtcDay(Date.now());

  const resolved = useMemo(
    () => resolveTimelineRange(crops, selectedEntityId ?? '', today),
    [crops, selectedEntityId, today],
  );

  // New entity → cursor at today (or at the end of a window that is already over).
  useEffect(() => {
    if (selectedEntityId) setCurrentDate(new Date(Math.min(today, resolved.range.end)));
    // Only on entity change: a later range update must not move a user-set cursor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedEntityId]);

  return { ...resolved, today };
}
