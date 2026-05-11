import type { NGSIProperty, NGSIRelationship, NGSIGeoProperty } from '@/types/ngsi-ld';

export interface EntityEditorProps {
  entityId: string;
  entityType: string;
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

export interface AttributeSchema {
  key: string;
  labelKey: string;
  type: 'text' | 'number' | 'select' | 'geo' | 'relationship' | 'boolean';
  section: 'basic' | 'geometry' | 'kinematic' | 'relationships' | 'visual' | 'additional';
  unitCode?: string;
  options?: { value: string; labelKey: string }[];
  min?: number;
  max?: number;
  step?: number;
  targetType?: string;
  entityTypes: string[];
}

export type NGSAttribute = NGSIProperty | NGSIRelationship | NGSIGeoProperty;

export interface EditorFormState {
  attributes: Record<string, NGSAttribute>;
  dirtyFields: Set<string>;
  originalAttributes: Record<string, NGSAttribute>;
}

export interface EntityEditorContextValue {
  formState: EditorFormState;
  setField: (key: string, attr: NGSAttribute) => void;
  hasChanges: boolean;
  resetAll: () => void;
  entityType: string;
  entityId: string;
}
