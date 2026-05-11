import React from 'react';
import { useI18n } from '@/context/I18nContext';
import { useEntityEditor } from '../EntityEditorContext';
import { AttributeField } from '../AttributeField';
import { getSchemaByKey } from '../attributeSchemas';
import type { AttributeSchema } from '../types';

function inferSchema(key: string, attr: any): AttributeSchema {
  const attrType = attr?.type;
  if (attrType === 'GeoProperty') {
    return { key, labelKey: key, type: 'geo', section: 'additional', entityTypes: ['*'] };
  }
  if (attrType === 'Relationship') {
    return { key, labelKey: key, type: 'relationship', section: 'additional', entityTypes: ['*'] };
  }
  const val = attr?.value;
  if (typeof val === 'boolean') {
    return { key, labelKey: key, type: 'boolean', section: 'additional', entityTypes: ['*'] };
  }
  if (typeof val === 'number') {
    return { key, labelKey: key, type: 'number', section: 'additional', entityTypes: ['*'] };
  }
  return { key, labelKey: key, type: 'text', section: 'additional', entityTypes: ['*'] };
}

export const AdditionalAttributes: React.FC = () => {
  const { t } = useI18n();
  const { formState, setField, entityType } = useEntityEditor();
  const knownKeys = new Set(['id', 'type', '@context', 'dateCreated', 'dateModified']);
  const extraKeys = Object.keys(formState.attributes).filter(k =>
    !knownKeys.has(k) && !getSchemaByKey(k, entityType)
  );
  if (extraKeys.length === 0) return null;

  return (
    <details className="border border-gray-200 rounded-lg">
      <summary className="px-4 py-3 bg-gray-50 cursor-pointer text-sm font-semibold text-gray-700 hover:bg-gray-100 rounded-lg">
        {t('editor.section.additional') || 'Atributos adicionales'} ({extraKeys.length})
      </summary>
      <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
        {extraKeys.map(key => (
          <AttributeField
            key={key}
            schema={inferSchema(key, formState.attributes[key])}
            value={formState.attributes[key]}
            onChange={attr => setField(key, attr)}
          />
        ))}
      </div>
    </details>
  );
};
