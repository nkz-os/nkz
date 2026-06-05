// =============================================================================
// Core Timeline Controls - Bottom-panel date scrub + photo window selector
// =============================================================================
// Renders a date input (bounded to photo date span) and window buttons (7/30/90/All)
// that drive which field-photo markers are visible via ViewerContext.

import React from 'react';
import { useTranslation } from 'react-i18next';
import { useViewer } from '@/context/ViewerContext';
import type { FieldPhotoRecord } from '@/utils/fieldPhotos';
import { Button, Input } from '@nekazari/ui-kit';

/* eslint-disable @typescript-eslint/no-explicit-any */
interface Props {
    /** Used only to derive the scrub input's min/max date span. */
    photos: FieldPhotoRecord[];
}

const WINDOWS: { key: string; days: number | null }[] = [
    { key: 'viewer.fieldPhotos.window7', days: 7 },
    { key: 'viewer.fieldPhotos.window30', days: 30 },
    { key: 'viewer.fieldPhotos.window90', days: 90 },
    { key: 'viewer.fieldPhotos.windowAll', days: null },
];

const toDateInput = (d: Date) => d.toISOString().slice(0, 10);

export const CoreTimelineControls: React.FC<Props> = ({ photos }) => {
    const { t } = useTranslation();
    const { currentDate, setCurrentDate, photoWindowDays, setPhotoWindowDays } = useViewer();

    const dated = photos.map(p => p.dateObserved).filter(Boolean).sort();
    const min = dated[0]?.slice(0, 10);
    const max = dated[dated.length - 1]?.slice(0, 10);

    return (
        <div className="flex items-center gap-4 px-4 w-full text-slate-700 dark:text-slate-200">
            <label className="flex items-center gap-2 text-xs">
                <span className="text-slate-500 dark:text-slate-400">{t('viewer.fieldPhotos.currentDate')}</span>
                <Input
                    type="date"
                    aria-label={t('viewer.fieldPhotos.currentDate')}
                    value={toDateInput(currentDate)}
                    min={min}
                    max={max}
                    onChange={(e: any) => { if (e.target.value) setCurrentDate(new Date(e.target.value)); }}
                    className="bg-slate-100 dark:bg-slate-700 rounded-lg px-2 py-1 text-xs"
                />
            </label>

            <div className="flex items-center gap-2">
                <span className="text-xs text-slate-500 dark:text-slate-400">{t('viewer.fieldPhotos.window')}</span>
                {WINDOWS.map(w => (
                    <Button
                        key={w.key}
                        type="button"
                        aria-pressed={photoWindowDays === w.days}
                        onClick={() => setPhotoWindowDays(w.days)}
                        className={`px-2 py-1 text-xs rounded-lg transition-all ${
                            photoWindowDays === w.days
                                ? 'bg-nkz-info-light text-nkz-info border border-blue-200 dark:bg-blue-900 dark:text-blue-300 dark:border-blue-700'
                                : 'bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-600'
                        }`}
                    >
                        {t(w.key)}
                    </Button>
                ))}
            </div>
        </div>
    );
};

export default CoreTimelineControls;
