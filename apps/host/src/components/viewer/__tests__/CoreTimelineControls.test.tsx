import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import { CoreTimelineControls } from '../CoreTimelineControls';

const setCurrentDate = vi.fn();
const setPhotoWindowDays = vi.fn();
vi.mock('@/context/ViewerContext', () => ({
  useViewer: () => ({
    currentDate: new Date('2026-05-18T00:00:00Z'),
    setCurrentDate,
    photoWindowDays: 30,
    setPhotoWindowDays,
  }),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

describe('CoreTimelineControls', () => {
  beforeEach(() => {
    setCurrentDate.mockClear();
    setPhotoWindowDays.mockClear();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('selects a window when a window button is clicked', () => {
    const { getByText } = render(<CoreTimelineControls />);
    fireEvent.click(getByText('viewer.fieldPhotos.window7'));
    expect(setPhotoWindowDays).toHaveBeenCalledWith(7);
  });

  it('sets All (null) window', () => {
    const { getByText } = render(<CoreTimelineControls />);
    fireEvent.click(getByText('viewer.fieldPhotos.windowAll'));
    expect(setPhotoWindowDays).toHaveBeenCalledWith(null);
  });

  it('keeps the 7/30/90/All window buttons', () => {
    const { getByText } = render(<CoreTimelineControls />);
    for (const [key, days] of [
      ['viewer.fieldPhotos.window7', 7],
      ['viewer.fieldPhotos.window30', 30],
      ['viewer.fieldPhotos.window90', 90],
      ['viewer.fieldPhotos.windowAll', null],
    ] as const) {
      fireEvent.click(getByText(key));
      expect(setPhotoWindowDays).toHaveBeenLastCalledWith(days);
    }
    expect(setPhotoWindowDays).toHaveBeenCalledTimes(4);
  });

  it('labels the window group as field photos around the date', () => {
    const { getByText } = render(<CoreTimelineControls />);
    expect(getByText('viewer.fieldPhotos.window')).toBeTruthy();
  });

  it('renders no date input', () => {
    const { container, queryByLabelText } = render(<CoreTimelineControls />);
    expect(container.querySelector('input')).toBeNull();
    expect(queryByLabelText('viewer.fieldPhotos.currentDate')).toBeNull();
  });

  it('moves the cursor to UTC midnight of today when Today is clicked', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-15T17:42:11Z'));
    const { getByText } = render(<CoreTimelineControls />);
    fireEvent.click(getByText('viewer.timeline.today'));
    expect(setCurrentDate).toHaveBeenCalledTimes(1);
    const arg = setCurrentDate.mock.calls[0][0] as Date;
    expect(arg).toBeInstanceOf(Date);
    expect(arg.toISOString()).toBe('2026-07-15T00:00:00.000Z');
  });
});
