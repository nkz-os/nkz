import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const setCurrentDate = vi.fn();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let viewerState: any;
let visibleTracks: unknown[];
vi.mock('@/context/ViewerContext', () => ({ useViewer: () => viewerState }));
vi.mock('@/context/SlotRegistry', () => ({
  useSlotRegistryOptional: () => ({ getVisibleWidgets: () => visibleTracks }),
}));
const slotProps = vi.fn();
vi.mock('@/components/SlotRenderer', () => ({
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  SlotRenderer: (p: any) => { slotProps(p); return <div data-testid="tracks" />; },
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k, i18n: { language: 'es' } }) }));

import { ViewerTimeline } from '../ViewerTimeline';

const DAY = 86_400_000;
const today = Date.UTC(2026, 9, 9);
const range = { start: today - 90 * DAY, end: today + 30 * DAY };

describe('ViewerTimeline', () => {
  beforeEach(() => {
    setCurrentDate.mockClear(); slotProps.mockClear();
    visibleTracks = [{ id: 'veg' }];
    viewerState = { selectedEntityId: 'urn:ngsi-ld:AgriParcel:p1', selectedEntityType: 'AgriParcel',
      currentDate: new Date(today - 10 * DAY), setCurrentDate };
  });

  it('renders nothing without a selected entity', () => {
    viewerState.selectedEntityId = null;
    const { container } = render(<ViewerTimeline range={range} source="default" today={today} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when no module contributes a visible track', () => {
    visibleTracks = [];
    const { container } = render(<ViewerTimeline range={range} source="default" today={today} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('passes the track contract to the timeline-track slot', () => {
    render(<ViewerTimeline range={range} source="campaign" today={today} />);
    const p = slotProps.mock.calls[slotProps.mock.calls.length - 1][0];
    expect(p.slot).toBe('timeline-track');
    expect(p.additionalProps).toMatchObject({
      entityId: 'urn:ngsi-ld:AgriParcel:p1', entityType: 'AgriParcel',
      range, cursor: today - 10 * DAY, forecastFrom: today,
    });
    p.additionalProps.onCursorChange(today - 3 * DAY);
    expect(setCurrentDate).toHaveBeenLastCalledWith(new Date(today - 3 * DAY));
    expect(screen.getByRole('slider', { name: 'viewer.timeline.axis' })).toBeInTheDocument();
    expect(screen.getByText('viewer.timeline.campaign')).toBeInTheDocument();
  });
});
