import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

const setCurrentDate = vi.fn();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let viewerState: any;
let visibleTracks: unknown[];
vi.mock('@/context/ViewerContext', () => ({ useViewer: () => viewerState }));
vi.mock('@/context/SlotRegistry', () => ({
  useSlotRegistryOptional: () => ({ getVisibleWidgets: () => visibleTracks }),
}));

import { useViewerTimelineRange } from '../useViewerTimelineRange';

describe('useViewerTimelineRange', () => {
  beforeEach(() => {
    setCurrentDate.mockClear();
    visibleTracks = [{ id: 'veg' }];
    viewerState = { selectedEntityId: null, setCurrentDate };
  });

  it('does not touch the cursor while nothing is selected', () => {
    renderHook(() => useViewerTimelineRange([]));
    expect(setCurrentDate).not.toHaveBeenCalled();
  });

  it('moves the cursor to today once per entity change, never on re-render', () => {
    const { rerender } = renderHook(() => useViewerTimelineRange([]));
    viewerState = { ...viewerState, selectedEntityId: 'urn:ngsi-ld:AgriParcel:p1' };
    rerender();
    expect(setCurrentDate).toHaveBeenCalledTimes(1);
    const d = setCurrentDate.mock.calls[0][0] as Date;
    expect(d.getTime() % 86_400_000).toBe(0);           // UTC midnight
    rerender();
    expect(setCurrentDate).toHaveBeenCalledTimes(1);
    viewerState = { ...viewerState, selectedEntityId: 'urn:ngsi-ld:AgriParcel:p2' };
    rerender();
    expect(setCurrentDate).toHaveBeenCalledTimes(2);
  });

  it('does not touch the cursor when no module contributes a visible track', () => {
    visibleTracks = [];
    const { rerender } = renderHook(() => useViewerTimelineRange([]));
    viewerState = { ...viewerState, selectedEntityId: 'urn:ngsi-ld:AgriParcel:p1' };
    rerender();
    expect(setCurrentDate).not.toHaveBeenCalled();
  });
});
