import React, { createContext, useContext, useState, useCallback } from 'react';
import type { NGSAttribute } from '@/types/ngsi-ld';
import type { EditorFormState, EntityEditorContextValue } from './types';

/* eslint-disable @typescript-eslint/no-explicit-any */
const EntityEditorContext = createContext<EntityEditorContextValue | null>(null);

export function useEntityEditor(): EntityEditorContextValue {
  const ctx = useContext(EntityEditorContext);
  if (!ctx) throw new Error('useEntityEditor must be used within EntityEditorProvider');
  return ctx;
}

export const EntityEditorProvider: React.FC<{
  entityId: string;
  entityType: string;
  initialAttributes: Record<string, NGSAttribute>;
  children: React.ReactNode;
}> = ({ entityId, entityType, initialAttributes, children }) => {
  const [formState, setFormState] = useState<EditorFormState>({
    attributes: { ...initialAttributes },
    dirtyFields: new Set<string>(),
    originalAttributes: { ...initialAttributes },
  });

  const setField = useCallback((key: string, attr: NGSAttribute) => {
    setFormState(prev => {
      const next = { ...prev, attributes: { ...prev.attributes, [key]: attr } };
      const dirty = new Set(prev.dirtyFields);
      const orig = prev.originalAttributes[key];
      const origValue = orig && (orig as any).value;
      const newValue = (attr as any).value;
      if (JSON.stringify(origValue) !== JSON.stringify(newValue)) {
        dirty.add(key);
      } else {
        dirty.delete(key);
      }
      return { ...next, dirtyFields: dirty };
    });
  }, []);

  const hasChanges = formState.dirtyFields.size > 0;

  const resetAll = useCallback(() => {
    setFormState(prev => ({
      attributes: { ...prev.originalAttributes },
      dirtyFields: new Set<string>(),
      originalAttributes: { ...prev.originalAttributes },
    }));
  }, []);

  return (
    <EntityEditorContext.Provider value={{
      formState, setField, hasChanges, resetAll, entityType, entityId,
    }}>
      {children}
    </EntityEditorContext.Provider>
  );
};
