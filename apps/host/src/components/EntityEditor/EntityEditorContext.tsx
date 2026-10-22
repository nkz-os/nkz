import React, { createContext, useContext, useState, useCallback } from 'react';
import { getNGSIValue } from '@/types/ngsi-ld';
import type { NGSAttribute } from '@/types/ngsi-ld';
import type { EditorFormState, EntityEditorContextValue } from './types';

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

  // `undefined` clears the attribute. Relationships compare by object, properties by value.
  const setField = useCallback((key: string, attr: NGSAttribute | undefined) => {
    setFormState(prev => {
      const attributes = { ...prev.attributes };
      if (attr === undefined) delete attributes[key];
      else attributes[key] = attr;
      const next = { ...prev, attributes };
      const dirty = new Set(prev.dirtyFields);
      const orig = prev.originalAttributes[key];
      const origValue = orig === undefined ? undefined : getNGSIValue(orig);
      const newValue = attr === undefined ? undefined : getNGSIValue(attr);
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
