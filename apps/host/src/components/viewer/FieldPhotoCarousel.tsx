import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import type { FieldPhotoRecord } from '@/utils/fieldPhotos';
import { Button } from '@nekazari/ui-kit';

interface FieldPhotoCarouselProps {
  photos: FieldPhotoRecord[];
  index: number;
  onIndexChange: (i: number) => void;
  onClose: () => void;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const API = (import.meta as any).env?.VITE_API_URL ?? '';

export const FieldPhotoCarousel: React.FC<FieldPhotoCarouselProps> = ({
  photos, index, onIndexChange, onClose,
}) => {
  const { t } = useTranslation();
  const photo = photos[index];
  const [errored, setErrored] = useState(false);

  useEffect(() => { setErrored(false); }, [photo?.id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowRight' && index < photos.length - 1) onIndexChange(index + 1);
      else if (e.key === 'ArrowLeft' && index > 0) onIndexChange(index - 1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [index, photos.length, onIndexChange, onClose]);

  if (!photo) return null;
  const hasGps = photo.lat !== null && photo.lng !== null;
  const src = /^https?:\/\//i.test(photo.imageUrl) ? photo.imageUrl : `${API}${photo.imageUrl}`;

  return (
    <div className="absolute inset-0 z-50 flex flex-col items-center justify-center bg-black/80 p-4">
      <Button
        type="button" aria-label={t('viewer.fieldPhotos.close')} onClick={onClose}
        className="absolute top-3 right-3 text-white p-2 rounded-nkz-md bg-nkz-surface-sunken/60 hover:bg-nkz-surface-sunken"
      >
        <X className="w-5 h-5" />
      </Button>

      <div className="flex items-center gap-3 max-w-full max-h-full">
        <Button
          type="button" aria-label={t('viewer.fieldPhotos.prev')}
          disabled={index <= 0} onClick={() => onIndexChange(index - 1)}
          className="text-white p-2 rounded-nkz-md bg-nkz-surface-sunken/60 disabled:opacity-30 hover:bg-nkz-surface-sunken"
        >
          <ChevronLeft className="w-6 h-6" />
        </Button>

        {errored ? (
          <div className="max-h-[70vh] max-w-[80vw] rounded-nkz-md flex items-center justify-center bg-nkz-surface-sunken/60 w-64 h-48">
            <p className="text-nkz-sm text-white opacity-80 text-center px-4">{t('viewer.fieldPhotos.imageError')}</p>
          </div>
        ) : (
          <img
            src={src} alt={photo.note || photo.id}
            className="max-h-[70vh] max-w-[80vw] rounded-nkz-md object-contain"
            onError={() => setErrored(true)}
          />
        )}

        <Button
          type="button" aria-label={t('viewer.fieldPhotos.next')}
          disabled={index >= photos.length - 1} onClick={() => onIndexChange(index + 1)}
          className="text-white p-2 rounded-nkz-md bg-nkz-surface-sunken/60 disabled:opacity-30 hover:bg-nkz-surface-sunken"
        >
          <ChevronRight className="w-6 h-6" />
        </Button>
      </div>

      <div className="mt-3 text-center text-white">
        {photo.note && <p className="text-nkz-sm font-medium">{photo.note}</p>}
        <p className="text-nkz-xs opacity-80">
          {hasGps ? `${photo.lat!.toFixed(5)}, ${photo.lng!.toFixed(5)}` : t('viewer.fieldPhotos.noGps')}
          {photo.dateObserved ? ` · ${new Date(photo.dateObserved).toLocaleString()}` : ''}
        </p>
        <p className="text-nkz-xs opacity-60">{t('viewer.fieldPhotos.counter', { i: index + 1, n: photos.length })}</p>
      </div>
    </div>
  );
};

export default FieldPhotoCarousel;
