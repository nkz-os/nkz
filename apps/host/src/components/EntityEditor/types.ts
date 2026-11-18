import type { NGSAttribute } from '@/types/ngsi-ld';

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
  /** ManufacturingMachine `category` values the field applies to; absent = every category. */
  categories?: string[];
  /** i18n key of a one-line help shown under the field. */
  helpKey?: string;
}

export interface EditorFormState {
  attributes: Record<string, NGSAttribute>;
  dirtyFields: Set<string>;
  originalAttributes: Record<string, NGSAttribute>;
}

export interface EntityEditorContextValue {
  formState: EditorFormState;
  setField: (key: string, attr: NGSAttribute | undefined) => void;
  hasChanges: boolean;
  resetAll: () => void;
  entityType: string;
  entityId: string;
}
