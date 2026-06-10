import React, { useState, useEffect } from 'react';
import { X, Loader2, Save } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import api from '@/services/api';
import { EntityEditorProvider } from './EntityEditorContext';
import { BasicAttributes } from './sections/BasicAttributes';
import { GeometrySection } from './sections/GeometrySection';
import { KinematicSection } from './sections/KinematicSection';
import { RelationshipSection } from './sections/RelationshipSection';
import { VisualSection } from './sections/VisualSection';
import { AdditionalAttributes } from './sections/AdditionalAttributes';
import { useEntityEditor } from './EntityEditorContext';
import { logger } from '@/utils/logger';
import type { EntityEditorProps } from './types';
import type { NGSAttribute } from '@/types/ngsi-ld';
import { Button } from '@nekazari/ui-kit';

/* eslint-disable @typescript-eslint/no-explicit-any */
function buildAttributeKeys(entity: any): Record<string, NGSAttribute> {
  const attrs: Record<string, NGSAttribute> = {};
  for (const [key, val] of Object.entries(entity)) {
    if (['id', 'type', '@context'].includes(key)) continue;
    if (val && typeof val === 'object' && 'type' in val) {
      attrs[key] = val as NGSAttribute;
    }
  }
  return attrs;
}

const EditorContent: React.FC<{ onClose: () => void; onSuccess?: () => void }> = ({ onClose, onSuccess }) => {
  const { t } = useI18n();
  const { formState, hasChanges, entityType, entityId } = useEntityEditor();
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmClose, setConfirmClose] = useState(false);

  const handleClose = () => {
    if (hasChanges && !confirmClose) {
      setConfirmClose(true);
      return;
    }
    onClose();
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const patch: Record<string, NGSAttribute> = {};
      formState.dirtyFields.forEach(key => {
        if (formState.attributes[key]) {
          patch[key] = formState.attributes[key];
        }
      });
      await api.updateSDMEntity(entityType, entityId, patch);
      onSuccess?.();
      onClose();
    } catch (err: any) {
      logger.error('[EntityEditor] Save failed:', err);
      setSaveError(err.response?.data?.detail || err.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] flex flex-col mx-4">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <div>
            <h2 className="text-lg font-bold text-gray-900">
              {t('editor.title') || 'Editar entidad'}
            </h2>
            <p className="text-xs text-nkz-muted">{entityType} &mdash; {entityId.split(':').pop()}</p>
          </div>
          <Button onClick={handleClose} className="p-1 text-nkz-muted hover:text-gray-600">
            <X className="w-5 h-5" />
          </Button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
          {saveError && (
            <div className="p-3 bg-nkz-error-light border border-red-200 rounded-lg text-sm text-nkz-error">{saveError}</div>
          )}

          <BasicAttributes />
          <KinematicSection />
          <GeometrySection />
          <RelationshipSection />
          <VisualSection />
          <AdditionalAttributes />
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t bg-nkz-bg-secondary rounded-b-2xl">
          <div>
            {hasChanges && (
              <span className="text-xs text-amber-600">
                {t('editor.unsaved_changes') || 'Cambios sin guardar'}
              </span>
            )}
          </div>
          <div className="flex gap-3">
            <Button
              onClick={handleClose}
              className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-200 rounded-lg transition"
            >
              {confirmClose ? (t('editor.confirm_discard') || '¿Descartar cambios?') : (t('common.cancel') || 'Cancelar')}
            </Button>
            <Button
              onClick={handleSave}
              disabled={!hasChanges || saving}
              className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition flex items-center gap-2"
            >
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              {t('common.save') || 'Guardar'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};

export const EntityEditorModal: React.FC<EntityEditorProps> = ({ entityId, entityType, isOpen, onClose, onSuccess }) => {
  const [entity, setEntity] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen || !entityId) return;
    let cancelled = false;
    setLoading(true);
    setFetchError(null);
    (async () => {
      try {
        const data = await api.getSDMEntityInstance(entityType, entityId);
        if (!cancelled) setEntity(data);
      } catch (err: any) {
        if (!cancelled) setFetchError(err.response?.data?.detail || err.message || 'Failed to load entity');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [isOpen, entityId, entityType]);

  useEffect(() => {
    if (!isOpen) setEntity(null);
  }, [isOpen]);

  if (!isOpen) return null;

  const initialAttributes = entity ? buildAttributeKeys(entity) : {};

  return (
    <EntityEditorProvider entityId={entityId} entityType={entityType} initialAttributes={initialAttributes}>
      {loading ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white rounded-2xl p-8 text-center">
            <Loader2 className="w-8 h-8 animate-spin text-nkz-info mx-auto mb-4" />
            <p className="text-gray-600">Cargando entidad...</p>
          </div>
        </div>
      ) : fetchError ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white rounded-2xl p-8 text-center max-w-md">
            <p className="text-nkz-error font-medium mb-2">Error</p>
            <p className="text-gray-600 text-sm">{fetchError}</p>
            <Button onClick={onClose} className="mt-4 px-4 py-2 bg-gray-200 rounded-lg text-sm">Cerrar</Button>
          </div>
        </div>
      ) : (
        <EditorContent onClose={onClose} onSuccess={onSuccess} />
      )}
    </EntityEditorProvider>
  );
};

// ── Global event listener for SDK access ──
const EDITOR_EVENT = 'nkz:open-entity-editor';

export interface EditorEventDetail {
  entityId: string;
  entityType: string;
}

export function initEntityEditorListener(
  onOpen: (detail: EditorEventDetail) => void
): () => void {
  const handler = (e: Event) => {
    const detail = (e as CustomEvent<EditorEventDetail>).detail;
    if (detail?.entityId && detail?.entityType) {
      onOpen(detail);
    }
  };
  window.addEventListener(EDITOR_EVENT, handler);
  return () => window.removeEventListener(EDITOR_EVENT, handler);
}

export function openEntityEditor(entityId: string, entityType: string): void {
  window.dispatchEvent(new CustomEvent<EditorEventDetail>(EDITOR_EVENT, {
    detail: { entityId, entityType },
  }));
}

// Expose globally for SDK access
if (typeof window !== 'undefined') {
  (window as any).__NKZ__OPEN_ENTITY_EDITOR__ = openEntityEditor;
}
