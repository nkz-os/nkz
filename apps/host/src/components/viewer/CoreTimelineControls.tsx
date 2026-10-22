// =============================================================================
// Core Timeline Controls - Bottom-panel "Today" shortcut + photo window selector
// =============================================================================
// Renders a Today button (moves the shared cursor to today) and window buttons
// (7/30/90/All) that drive which field-photo markers are visible via
// ViewerContext. The cursor itself is moved on the time axis.

import React from 'react';
import { useTranslation } from 'react-i18next';
import { useViewer } from '@/context/ViewerContext';
import { snapToUtcDay } from '@nekazari/viewer-kit';
import { Button } from '@nekazari/ui-kit';

const WINDOWS: { key: string; days: number | null }[] = [
    { key: 'viewer.fieldPhotos.window7', days: 7 },
    { key: 'viewer.fieldPhotos.window30', days: 30 },
    { key: 'viewer.fieldPhotos.window90', days: 90 },
    { key: 'viewer.fieldPhotos.windowAll', days: null },
];

export const CoreTimelineControls: React.FC = () => {
    const { t } = useTranslation();
    const { setCurrentDate, photoWindowDays, setPhotoWindowDays } = useViewer();

    return (
        <div className="flex items-center gap-4 px-4 w-full text-slate-700 dark:text-slate-200">
            <Button
                type="button"
                onClick={() => setCurrentDate(new Date(snapToUtcDay(Date.now())))}
                className="px-2 py-1 text-xs rounded-lg transition-all bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-600"
            >
                {t('viewer.timeline.today')}
            </Button>

            <div className="flex items-center gap-2">
                <span className="text-xs text-slate-500 dark:text-slate-400">{t('viewer.fieldPhotos.window')}</span>
                {WINDOWS.map(w => (
                    <Button variant="ghost"
                        key={w.key}
                        type="button"
                        aria-pressed={photoWindowDays === w.days}
                        onClick={() => setPhotoWindowDays(w.days)}
                        className={`px-2 py-1 text-xs rounded-lg transition-all ${
                            photoWindowDays === w.days
                                ? 'bg-nkz-info-soft text-nkz-info border border-blue-200 dark:bg-blue-900 dark:text-blue-300 dark:border-blue-700'
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
