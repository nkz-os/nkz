// =============================================================================
// Parcel List - Display and manage selected parcels
// =============================================================================

import React, { useState } from 'react';
import { Edit2, Trash2, Check, X, Ruler, Eye } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import { Button, Input } from '@nekazari/ui-kit';

export interface SelectedParcel {
  id: string;
  name: string;
  areaHectares: number;
  geometry: any;
  cadastralReference?: string;
  municipality?: string;
  province?: string;
  isEditing?: boolean;
}

interface ParcelListProps {
  parcels: SelectedParcel[];
  onUpdateName: (id: string, newName: string) => void;
  onRemove: (id: string) => void;
  onView?: (parcel: SelectedParcel) => void;
  canEdit?: boolean;
}

export const ParcelList: React.FC<ParcelListProps> = ({
  parcels,
  onUpdateName,
  onRemove,
  onView,
  canEdit = true,
}) => {
  const { t } = useI18n();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState<string>('');

  const totalHectares = parcels.reduce((sum, p) => sum + p.areaHectares, 0);

  const handleStartEdit = (parcel: SelectedParcel) => {
    setEditingId(parcel.id);
    setEditValue(parcel.name);
  };

  const handleSaveEdit = (id: string) => {
    if (editValue.trim()) {
      onUpdateName(id, editValue.trim());
    }
    setEditingId(null);
    setEditValue('');
  };

  const handleCancelEdit = () => {
    setEditingId(null);
    setEditValue('');
  };

  if (parcels.length === 0) {
    return (
      <div className="bg-nkz-bg-secondary border border-dashed border-nkz-border rounded-lg p-6 text-center">
        <p className="text-sm text-nkz-muted">
          {t('parcels.no_parcels_selected')}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        {parcels.map((parcel) => (
          <div
            key={parcel.id}
            className="bg-white border border-nkz-border rounded-lg p-4 hover:border-emerald-300 transition-colors"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                {editingId === parcel.id ? (
                  <div className="flex items-center gap-2">
                    <Input
                      type="text"
                      value={editValue}
                      onChange={(e: any) => setEditValue(e.target.value)}
                      className="flex-1 px-2 py-1 border border-emerald-500 rounded text-sm focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                      autoFocus
                      onKeyDown={(e: any) => {
                        if (e.key === 'Enter') {
                          handleSaveEdit(parcel.id);
                        } else if (e.key === 'Escape') {
                          handleCancelEdit();
                        }
                      }}
                    />
                    <Button
                      onClick={() => handleSaveEdit(parcel.id)}
                      className="p-1 text-emerald-600 hover:bg-emerald-50 rounded transition-colors"
                      title={t('parcels.save')}
                    >
                      <Check className="w-4 h-4" />
                    </Button>
                    <Button
                      onClick={handleCancelEdit}
                      className="p-1 text-nkz-muted hover:bg-nkz-bg-secondary rounded transition-colors"
                      title={t('parcels.cancel')}
                    >
                      <X className="w-4 h-4" />
                    </Button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <h4 className="text-sm font-semibold text-gray-900 truncate">
                      {parcel.name}
                    </h4>
                    {canEdit && (
                      <Button
                        onClick={() => handleStartEdit(parcel)}
                        className="p-1 text-nkz-muted hover:text-emerald-600 hover:bg-emerald-50 rounded transition-colors"
                        title={t('parcels.edit_name')}
                      >
                        <Edit2 className="w-3.5 h-3.5" />
                      </Button>
                    )}
                  </div>
                )}
                
                <div className="mt-2 flex items-center gap-4 text-xs text-gray-600">
                  <div className="flex items-center gap-1">
                    <Ruler className="w-3 h-3 text-emerald-600" />
                    <span className="font-medium">{parcel.areaHectares.toFixed(2)} {t('ndvi.hectares')}</span>
                  </div>
                  {parcel.cadastralReference && (
                    <span className="text-nkz-muted">
                      {t('parcels.reference', { reference: parcel.cadastralReference })}
                    </span>
                  )}
                  {(parcel.municipality || parcel.province) && (
                    <span className="text-nkz-muted">
                      {parcel.municipality && parcel.province 
                        ? `${parcel.municipality}, ${parcel.province}`
                        : parcel.municipality || parcel.province}
                    </span>
                  )}
                </div>
              </div>
              
              <div className="flex items-center gap-2">
                {onView && (
                  <Button
                    onClick={() => onView(parcel)}
                    className="p-1.5 text-emerald-600 hover:text-emerald-700 hover:bg-emerald-50 rounded transition-colors"
                    title={t('ndvi.view_ndvi')}
                  >
                    <Eye className="w-4 h-4" />
                  </Button>
                )}
                {canEdit && editingId !== parcel.id && (
                  <Button
                    onClick={() => onRemove(parcel.id)}
                    className="p-1.5 text-red-400 hover:text-nkz-error hover:bg-nkz-error-light rounded transition-colors"
                    title={t('parcels.remove_parcel')}
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Ruler className="w-5 h-5 text-emerald-700" />
            <span className="text-sm font-medium text-emerald-900">
              {t('parcels.total_area')}
            </span>
          </div>
          <span className="text-xl font-bold text-emerald-900">
            {totalHectares.toFixed(2)} {t('ndvi.hectares')}
          </span>
        </div>
        <p className="mt-2 text-xs text-emerald-700">
          {t('parcels.area_plan_limit')}
        </p>
      </div>
    </div>
  );
};

