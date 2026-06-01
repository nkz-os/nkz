import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import { FieldPhotoCarousel } from '../FieldPhotoCarousel';
import type { FieldPhotoRecord } from '@/utils/fieldPhotos';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, o?: any) => (o ? `${k}:${JSON.stringify(o)}` : k) }) }));

const mk = (id: string, note: string): FieldPhotoRecord => ({
  id, imageUrl: `/api/field-images/${id}.jpg`, lng: -2.9, lat: 43.2,
  dateObserved: '2026-05-30T10:00:00Z', note, accuracy: 3, refAgriParcel: null,
});
const photos = [mk('a', 'note-a'), mk('b', 'note-b'), mk('c', 'note-c')];

describe('FieldPhotoCarousel', () => {
  it('shows the photo at the given index', () => {
    const { getByText } = render(
      <FieldPhotoCarousel photos={photos} index={1} onIndexChange={() => {}} onClose={() => {}} />,
    );
    expect(getByText('note-b')).toBeTruthy();
  });

  it('advances and goes back via next/prev', () => {
    const onIndexChange = vi.fn();
    const { getByLabelText } = render(
      <FieldPhotoCarousel photos={photos} index={1} onIndexChange={onIndexChange} onClose={() => {}} />,
    );
    fireEvent.click(getByLabelText('viewer.fieldPhotos.next'));
    expect(onIndexChange).toHaveBeenCalledWith(2);
    fireEvent.click(getByLabelText('viewer.fieldPhotos.prev'));
    expect(onIndexChange).toHaveBeenCalledWith(0);
  });

  it('calls onClose from the close button', () => {
    const onClose = vi.fn();
    const { getByLabelText } = render(
      <FieldPhotoCarousel photos={photos} index={0} onIndexChange={() => {}} onClose={onClose} />,
    );
    fireEvent.click(getByLabelText('viewer.fieldPhotos.close'));
    expect(onClose).toHaveBeenCalled();
  });
});
