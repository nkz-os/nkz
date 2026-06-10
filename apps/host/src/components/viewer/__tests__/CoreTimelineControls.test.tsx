import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import { CoreTimelineControls } from '../CoreTimelineControls';
import type { FieldPhotoRecord } from '@/utils/fieldPhotos';

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

const mk = (d: string): FieldPhotoRecord => ({ id: d, imageUrl: '', lng: 0, lat: 0, dateObserved: d, note: '', accuracy: null, refAgriParcel: null });

describe('CoreTimelineControls', () => {
  it('selects a window when a window button is clicked', () => {
    const { getByText } = render(<CoreTimelineControls photos={[mk('2026-05-01T00:00:00Z')]} />);
    fireEvent.click(getByText('viewer.fieldPhotos.window7'));
    expect(setPhotoWindowDays).toHaveBeenCalledWith(7);
  });

  it('sets All (null) window', () => {
    const { getByText } = render(<CoreTimelineControls photos={[]} />);
    fireEvent.click(getByText('viewer.fieldPhotos.windowAll'));
    expect(setPhotoWindowDays).toHaveBeenCalledWith(null);
  });

  it('updates currentDate from the date input', () => {
    const { getByLabelText } = render(<CoreTimelineControls photos={[]} />);
    fireEvent.change(getByLabelText('viewer.fieldPhotos.currentDate'), { target: { value: '2026-06-01' } });
    expect(setCurrentDate).toHaveBeenCalled();
  });
});
