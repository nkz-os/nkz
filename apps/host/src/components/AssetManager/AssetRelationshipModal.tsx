// =============================================================================
// Asset Relationship Modal - Assign Parent Entity (FIWARE Relationships)
// =============================================================================
// Modal that places an entity under a parcel. The broker operations come from
// planParentChange (merge-patch hasAgriParcel to assign, DELETE to remove).

import React, { useState, useMemo, useCallback } from 'react';
import {
/* eslint-disable @typescript-eslint/no-explicit-any */
  X,
  Search,
  MapPin,
  Link2,
  Unlink,
  Loader2,
  AlertCircle,
  CheckCircle,
} from 'lucide-react';
import api from '@/services/api';
import { UnifiedAsset, ASSET_TYPE_REGISTRY } from '@/types/assets';
import { logger } from '@/utils/logger';
import { Button, Input } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';
import { canRemoveParent, planParentChange, type ParentChangeOperation } from './parentAssignment';

// =============================================================================
// Types
// =============================================================================

export interface AssetRelationshipModalProps {
  /** The asset to assign a parent to */
  asset: UnifiedAsset;
  /** Parcels the asset can be placed under */
  potentialParents: UnifiedAsset[];
  /** Close modal callback */
  onClose: () => void;
  /** Success callback - called after relationship is updated */
  onSuccess: () => void;
}

// =============================================================================
// Helper Functions
// =============================================================================

async function applyOperation(entityId: string, operation: ParentChangeOperation): Promise<void> {
  if (operation.kind === 'merge') {
    await api.mergeSDMEntity(entityId, operation.fragment);
    return;
  }
  try {
    await api.deleteSDMEntityAttribute(entityId, operation.attribute);
  } catch (err: unknown) {
    // Already absent: the removal is idempotent.
    if ((err as { response?: { status?: number } })?.response?.status !== 404) throw err;
  }
}

// =============================================================================
// Component
// =============================================================================

export const AssetRelationshipModal: React.FC<AssetRelationshipModalProps> = ({
  asset,
  potentialParents,
  onClose,
  onSuccess,
}) => {
  const { t } = useI18n();

  // State
  const [search, setSearch] = useState('');
  const [selectedParentId, setSelectedParentId] = useState<string | null>(asset.parentId || null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  
  // Filter parents by search
  const filteredParents = useMemo(() => {
    if (!search) return potentialParents;
    const searchLower = search.toLowerCase();
    return potentialParents.filter(p =>
      p.name.toLowerCase().includes(searchLower) ||
      p.type.toLowerCase().includes(searchLower) ||
      p.municipality?.toLowerCase().includes(searchLower)
    );
  }, [potentialParents, search]);
  
  // Handle save
  const handleSave = useCallback(async () => {
    if (selectedParentId === (asset.parentId || null)) {
      // No change
      onClose();
      return;
    }
    
    setIsLoading(true);
    setError(null);
    
    try {
      for (const operation of planParentChange(asset.rawEntity, selectedParentId)) {
        await applyOperation(asset.id, operation);
      }
      
      setSuccess(true);
      
      // Close after short delay to show success
      setTimeout(() => {
        onSuccess();
        onClose();
      }, 800);
      
    } catch (err: unknown) {
      logger.error('[AssetRelationshipModal] Error updating relationship:', err);
      setError(t('entities.assets.assign.error'));
    } finally {
      setIsLoading(false);
    }
  }, [asset, selectedParentId, onClose, onSuccess, t]);
  
  // Handle remove relationship
  const handleRemove = useCallback(() => {
    setSelectedParentId(null);
  }, []);
  
  // Current parent info
  const currentParent = useMemo(() => {
    if (!asset.parentId) return null;
    return potentialParents.find(p => p.id === asset.parentId);
  }, [asset.parentId, potentialParents]);
  
  const selectedParent = useMemo(() => {
    if (!selectedParentId) return null;
    return potentialParents.find(p => p.id === selectedParentId);
  }, [selectedParentId, potentialParents]);
  
  const hasChanges = selectedParentId !== (asset.parentId || null);
  const removable = canRemoveParent(asset.rawEntity);
  
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between bg-gradient-to-r from-slate-50 to-white">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-nkz-info-soft flex items-center justify-center">
              <Link2 className="w-5 h-5 text-nkz-info" />
            </div>
            <div>
              <h2 className="font-semibold text-slate-800">{t('entities.assets.assign.title')}</h2>
              <p className="text-sm text-slate-500">{asset.name}</p>
            </div>
          </div>
          <Button variant="ghost"
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-600"
          >
            <X className="w-5 h-5" />
          </Button>
        </div>
        
        {/* Current Assignment */}
        {currentParent && (
          <div className="px-6 py-3 bg-slate-50 border-b border-slate-100">
            <p className="text-xs text-slate-500 mb-1">{t('entities.assets.assign.current')}</p>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <MapPin className="w-4 h-4 text-nkz-success" />
                <span className="font-medium text-sm text-slate-700">{currentParent.name}</span>
                <span className="text-xs text-slate-400">
                  ({ASSET_TYPE_REGISTRY[currentParent.type]?.label || currentParent.type})
                </span>
              </div>
              {removable && (
                <Button variant="ghost"
                  onClick={handleRemove}
                  className="flex items-center gap-1 text-xs text-nkz-danger-strong hover:text-nkz-danger-strong px-2 py-1 rounded hover:bg-nkz-danger-soft"
                >
                  <Unlink className="w-3 h-3" />
                  {t('entities.assets.assign.remove')}
                </Button>
              )}
            </div>
          </div>
        )}
        
        {/* Search */}
        <div className="px-6 py-3 border-b border-slate-100">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <Input
              type="text"
              placeholder={t('entities.assets.assign.search_placeholder')}
              value={search}
              onChange={(e: any) => setSearch(e.target.value)}
              className="w-full pl-9 pr-4 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
            />
          </div>
        </div>
        
        {/* Parent List */}
        <div className="max-h-72 overflow-y-auto">
          {filteredParents.length === 0 ? (
            <div className="px-6 py-8 text-center text-sm text-slate-400">
              {t('entities.assets.assign.empty')}
            </div>
          ) : (
            filteredParents.map(parent => {
              const isSelected = selectedParentId === parent.id;
              const typeInfo = ASSET_TYPE_REGISTRY[parent.type];
              
              return (
                <Button variant="ghost"
                  key={parent.id}
                  onClick={() => setSelectedParentId(parent.id)}
                  className={`w-full px-6 py-3 flex items-center gap-3 text-left transition-colors ${
                    isSelected
                      ? 'bg-nkz-info-soft border-l-2 border-blue-500'
                      : 'hover:bg-slate-50 border-l-2 border-transparent'
                  }`}
                >
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-nkz-success-soft">
                    <MapPin className="w-4 h-4 text-nkz-success" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className={`font-medium text-sm ${isSelected ? 'text-nkz-info' : 'text-slate-700'}`}>
                      {parent.name}
                    </p>
                    <p className="text-xs text-slate-500 truncate">
                      {typeInfo?.label || parent.type}
                      {parent.municipality && ` • ${parent.municipality}`}
                    </p>
                  </div>
                  {isSelected && (
                    <CheckCircle className="w-5 h-5 text-nkz-info flex-shrink-0" />
                  )}
                </Button>
              );
            })
          )}
        </div>
        
        {/* Footer */}
        <div className="px-6 py-4 border-t border-slate-200 bg-slate-50 flex items-center justify-between">
          {/* Status */}
          <div className="text-sm">
            {error && (
              <div className="flex items-center gap-2 text-nkz-danger-strong">
                <AlertCircle className="w-4 h-4" />
                {error}
              </div>
            )}
            {success && (
              <div className="flex items-center gap-2 text-nkz-success-strong">
                <CheckCircle className="w-4 h-4" />
                {t('entities.assets.assign.updated')}
              </div>
            )}
            {!error && !success && selectedParent && (
              <span className="text-slate-500">
                {t('entities.assets.assign.located_in')}: <span className="font-medium text-slate-700">{selectedParent.name}</span>
              </span>
            )}
            {!error && !success && !selectedParent && asset.parentId && (
              <span className="text-amber-600">
                {t('entities.assets.assign.will_remove')}
              </span>
            )}
          </div>
          
          {/* Actions */}
          <div className="flex items-center gap-2">
            <Button variant="ghost"
              onClick={onClose}
              disabled={isLoading}
              className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-800 hover:bg-slate-100 rounded-lg transition-colors"
            >
              {t('entities.assets.assign.cancel')}
            </Button>
            <Button variant="ghost"
              onClick={handleSave}
              disabled={isLoading || !hasChanges || success}
              className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors flex items-center gap-2 ${
                hasChanges && !success
                  ? 'bg-blue-600 text-white hover:bg-blue-700'
                  : 'bg-slate-200 text-slate-400 cursor-not-allowed'
              }`}
            >
              {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
              {success ? t('entities.assets.assign.saved') : t('entities.assets.assign.save')}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AssetRelationshipModal;

