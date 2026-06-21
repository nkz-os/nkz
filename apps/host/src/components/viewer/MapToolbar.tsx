// =============================================================================
// Map Toolbar - Contextual Floating Toolbar for Map Interaction Modes
// =============================================================================
// Displays a floating toolbar at the top center of the map when in drawing/editing modes.
// Provides actions specific to the current map mode (Accept, Cancel, Undo, etc.)

import React from 'react';
import { useViewer, MapMode } from '@/context/ViewerContext';
import { useI18n } from '@/context/I18nContext';
import { CheckCircle2, X, Undo2, Eraser } from 'lucide-react';
import { Button } from '@nekazari/ui-kit';

interface MapToolbarProps {
    /** Callback when user accepts (completes) the current operation */
    onAccept?: () => void;
    /** Callback when user cancels the current operation */
    onCancel?: () => void;
    /** Callback when user wants to undo last action */
    onUndo?: () => void;
    /** Callback when user wants to clear current drawing */
    onClear?: () => void;
    /** Additional mode-specific actions */
    customActions?: React.ReactNode;
}

export const MapToolbar: React.FC<MapToolbarProps> = ({
    onAccept,
    onCancel,
    onUndo,
    onClear,
    customActions,
}) => {
    const { mapMode, resetMapMode, isFocusMode } = useViewer();
    const { t } = useI18n();

    // Only show toolbar when not in VIEW mode and not in focus mode
    // Focus mode: drawing/editing tools are hidden because they don't make sense
    // in the isolated parcel view
    if (mapMode === 'VIEW' || isFocusMode) {
        return null;
    }

    const handleCancel = () => {
        if (onCancel) {
            onCancel();
        }
        resetMapMode();
    };

    const getModeLabel = (mode: MapMode): string => {
        switch (mode) {
            case 'DRAW_PARCEL':
                return t('viewer.mapmode.draw_parcel');
            case 'SELECT_CADASTRAL':
                return t('viewer.mapmode.select_cadastral');
            case 'EDIT_GEOMETRY':
                return t('viewer.mapmode.edit_geometry');
            case 'ZONING':
                return t('viewer.mapmode.zoning');
            default:
                return t('viewer.mapmode.active_badge');
        }
    };

    const getModeInstructions = (mode: MapMode): string => {
        switch (mode) {
            case 'DRAW_PARCEL':
                return t('viewer.drawing.parcel_instructions');
            case 'SELECT_CADASTRAL':
                return t('viewer.drawing.cadastral_instructions');
            case 'EDIT_GEOMETRY':
                return t('viewer.drawing.edit_instructions');
            case 'ZONING':
                return t('viewer.drawing.zoning_instructions');
            default:
                return '';
        }
    };

    return (
        <div className="absolute top-24 left-1/2 transform -translate-x-1/2 z-50">
            <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl shadow-xl px-4 py-3 flex items-center gap-3 min-w-[400px]">
                {/* Mode Label */}
                <div className="flex-1">
                    <div className="font-semibold text-slate-800 dark:text-slate-100 text-sm">
                        {getModeLabel(mapMode)}
                    </div>
                    <div className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                        {getModeInstructions(mapMode)}
                    </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2 border-l border-slate-200 dark:border-slate-700 pl-3">
                    {/* Undo */}
                    {onUndo && mapMode === 'DRAW_PARCEL' && (
                        <Button
                            onClick={onUndo}
                            className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors text-slate-600 dark:text-slate-300 hover:text-slate-800 dark:hover:text-slate-100"
                            title={t('viewer.toolbar.undo_title')}
                        >
                            <Undo2 className="w-4 h-4" />
                        </Button>
                    )}

                    {/* Clear */}
                    {onClear && mapMode === 'DRAW_PARCEL' && (
                        <Button
                            onClick={onClear}
                            className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors text-slate-600 dark:text-slate-300 hover:text-slate-800 dark:hover:text-slate-100"
                            title={t('viewer.toolbar.clear_title')}
                        >
                            <Eraser className="w-4 h-4" />
                        </Button>
                    )}

                    {/* Custom Actions */}
                    {customActions}

                    {/* Accept */}
                    {onAccept && (
                        <Button
                            onClick={onAccept}
                            className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors flex items-center gap-2 font-medium"
                            title={t('viewer.toolbar.accept')}
                        >
                            <CheckCircle2 className="w-4 h-4" />
                            {t('viewer.toolbar.accept')}
                        </Button>
                    )}

                    {/* Cancel */}
                    <Button
                        onClick={handleCancel}
                        className="px-4 py-2 bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-100 rounded-lg hover:bg-slate-300 dark:hover:bg-slate-600 transition-colors flex items-center gap-2 font-medium"
                        title={t('cancel')}
                    >
                        <X className="w-4 h-4" />
                        {t('cancel')}
                    </Button>
                </div>
            </div>
        </div>
    );
};
