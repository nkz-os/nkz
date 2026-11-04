import React from 'react';
import { MousePointer2, Brush, Grid3x3, AlertTriangle, Spline } from 'lucide-react';
import { Button } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';

export type PlacementMode = 'single' | 'multi' | 'stamp' | 'array' | 'line' | 'polygon';

interface PlacementModeSelectorProps {
    mode: PlacementMode;
    onChange: (mode: PlacementMode) => void;
    entityType?: string;
}

// The wizard is a narrow side panel: one column always, and each card grows with
// its text (the ui-kit Button's fixed height made the descriptions overlap).
const CARD = 'h-auto w-full flex-col items-stretch justify-start whitespace-normal p-4 rounded-xl border-2 text-left transition-all relative';

export const PlacementModeSelector: React.FC<PlacementModeSelectorProps> = ({ mode, onChange, entityType }) => {
    const { t } = useI18n();

    // Stamp mode is only recommended for vegetation
    const isVegetation = entityType && ['OliveGrove', 'Vineyard', 'AgriPlant', 'AgriTree', 'OliveTree', 'Vine'].includes(entityType);

    return (
        <div className="grid grid-cols-1 gap-3">
            {/* Single Mode */}
            <Button variant="ghost"
                type="button"
                onClick={() => onChange('single')}
                className={`${CARD} ${mode === 'single'
                        ? 'border-blue-500 bg-nkz-surface-sunken shadow-sm'
                        : 'border-nkz-border hover:border-nkz-info/30 hover:bg-nkz-bg-secondary'
                    }`}
            >
                <div className="flex items-start gap-3">
                    <div className={`p-2 rounded-lg ${mode === 'single' ? 'bg-nkz-info-soft text-nkz-info-strong' : 'bg-nkz-bg-secondary text-nkz-muted'}`}>
                        <MousePointer2 className="w-6 h-6" />
                    </div>
                    <div>
                        <div className="font-semibold text-nkz-text-primary">{t('wizard.placement.single_title')}</div>
                        <p className="text-sm text-nkz-muted mt-1">
                            {t('wizard.placement.single_desc')}
                        </p>
                    </div>
                </div>
            </Button>

            {/* Stamp Mode */}
            <Button variant="ghost"
                type="button"
                onClick={() => onChange('stamp')}
                className={`${CARD} ${mode === 'stamp'
                        ? 'border-green-500 bg-nkz-surface-sunken shadow-sm'
                        : 'border-nkz-border hover:border-nkz-success/30 hover:bg-nkz-bg-secondary'
                    }`}
            >
                <div className="flex items-start gap-3">
                    <div className={`p-2 rounded-lg ${mode === 'stamp' ? 'bg-nkz-success-soft text-nkz-success-strong' : 'bg-nkz-bg-secondary text-nkz-muted'}`}>
                        <Brush className="w-6 h-6" />
                    </div>
                    <div>
                        <div className="font-semibold text-nkz-text-primary flex items-center gap-2">
                            {t('wizard.placement.stamp_title')}
                            <span className="bg-nkz-success-soft text-nkz-success-strong text-xs px-2 py-0.5 rounded-full font-bold">GPU INSTANCED</span>
                        </div>
                        <p className="text-sm text-nkz-muted mt-1">
                            {t('wizard.placement.stamp_desc')}
                            <br />
                            <span className="text-xs font-medium">{t('wizard.placement.stamp_requirement')}</span>
                        </p>
                    </div>
                </div>

                {!isVegetation && mode === 'stamp' && (
                    <div className="mt-3 p-2 bg-nkz-warning-soft text-nkz-warning-strong text-xs rounded border border-nkz-warning/30 flex items-center gap-2">
                        <AlertTriangle className="w-3 h-3 flex-shrink-0" />
                        {t('wizard.placement.stamp_vegetation_warning')}
                    </div>
                )}
            </Button>

            {/* Array Mode */}
            <Button variant="ghost"
                type="button"
                onClick={() => onChange('array')}
                className={`${CARD} ${mode === 'array'
                        ? 'border-purple-500 bg-nkz-surface-sunken shadow-sm'
                        : 'border-nkz-border hover:border-nkz-info/30 hover:bg-nkz-bg-secondary'
                    }`}
            >
                <div className="flex items-start gap-3">
                    <div className={`p-2 rounded-lg ${mode === 'array' ? 'bg-nkz-info-soft text-nkz-info-strong' : 'bg-nkz-bg-secondary text-nkz-muted'}`}>
                        <Grid3x3 className="w-6 h-6" />
                    </div>
                    <div>
                        <div className="font-semibold text-nkz-text-primary flex items-center gap-2">
                            {t('wizard.placement.array_title')}
                            <span className="bg-nkz-info-soft text-nkz-info-strong text-xs px-2 py-0.5 rounded-full font-bold">GPU INSTANCED</span>
                        </div>
                        <p className="text-sm text-nkz-muted mt-1">
                            {t('wizard.placement.array_desc')}
                            <br />
                            <span className="text-xs font-medium">{t('wizard.placement.array_requirement')}</span>
                        </p>
                    </div>
                </div>
            </Button>

            {/* Line mode: a row of individual plants (vines, olives) */}
            {entityType === 'AgriPlant' && (
                <Button variant="ghost"
                    type="button"
                    onClick={() => onChange('line')}
                    className={`${CARD} ${mode === 'line'
                            ? 'border-green-500 bg-nkz-surface-sunken shadow-sm'
                            : 'border-nkz-border hover:border-nkz-success/30 hover:bg-nkz-bg-secondary'
                        }`}
                >
                    <div className="flex items-start gap-3">
                        <div className={`p-2 rounded-lg ${mode === 'line' ? 'bg-nkz-success-soft text-nkz-success-strong' : 'bg-nkz-bg-secondary text-nkz-muted'}`}>
                            <Spline className="w-6 h-6" />
                        </div>
                        <div>
                            <div className="font-semibold text-nkz-text-primary">{t('wizard.line.title')}</div>
                            <p className="text-sm text-nkz-muted mt-1">{t('wizard.line.desc')}</p>
                        </div>
                    </div>
                </Button>
            )}
        </div>
    );
};
