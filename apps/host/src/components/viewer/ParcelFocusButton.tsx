// =============================================================================
// ParcelFocusButton — toggle button for parcel focus mode
// =============================================================================
// Rendered in the right panel between the parcel name and context modules.
// Visible only when an AgriParcel entity is selected.

import React, { useCallback } from 'react';
import { useViewer } from '@/context/ViewerContext';
import { useI18n } from '@/context/I18nContext';
import { Button } from '@nekazari/ui-kit';
import { Maximize2, Minimize2 } from 'lucide-react';

interface ParcelFocusButtonProps {
    className?: string;
}

export const ParcelFocusButton: React.FC<ParcelFocusButtonProps> = ({ className }) => {
    const { isFocusMode, setFocusParcel, clearFocusParcel, selectedEntityId, selectedEntityType } = useViewer();
    const { t } = useI18n();

    const handleClick = useCallback(() => {
        if (isFocusMode) {
            clearFocusParcel();
        } else if (selectedEntityId) {
            setFocusParcel(selectedEntityId);
        }
    }, [isFocusMode, selectedEntityId, setFocusParcel, clearFocusParcel]);

    // Only show for AgriParcel entities
    if (!selectedEntityId) return null;
    const isParcel = selectedEntityType === 'AgriParcel' || (selectedEntityType && selectedEntityType.includes('Parcel'));
    if (!isParcel) return null;

    return (
        <Button
            onClick={handleClick}
            variant={isFocusMode ? 'primary' : 'secondary'}
            size="sm"
            className={`flex items-center gap-2 w-full ${className ?? ''}`}
            aria-label={isFocusMode ? t('viewer.focus.exit') : t('viewer.focus.enter')}
        >
            {isFocusMode ? (
                <><Minimize2 className="w-4 h-4" /> {t('viewer.focus.exit')}</>
            ) : (
                <><Maximize2 className="w-4 h-4" /> {t('viewer.focus.enter')}</>
            )}
        </Button>
    );
};
