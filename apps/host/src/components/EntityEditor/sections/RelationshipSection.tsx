import React from 'react';
import { useI18n } from '@/context/I18nContext';
import { useEntityEditor } from '../EntityEditorContext';
import { EntitySearchInput } from '../EntitySearchInput';
import { getSchemasForType } from '../attributeSchemas';

/* eslint-disable @typescript-eslint/no-explicit-any */
export const RelationshipSection: React.FC = () => {
  const { t } = useI18n();
  const { formState, setField, entityType, entityId } = useEntityEditor();
  const schemas = getSchemasForType(entityType).filter(s => s.section === 'relationships');
  if (schemas.length === 0) return null;

  return (
    <details className="border border-nkz-border rounded-lg">
      <summary className="px-4 py-3 bg-nkz-bg-secondary cursor-pointer text-sm font-semibold text-gray-700 hover:bg-nkz-bg-secondary rounded-lg">
        {t('editor.section.relationships') || 'Relaciones'}
      </summary>
      <div className="p-4 grid grid-cols-1 gap-4">
        {schemas.map(schema => (
          <EntitySearchInput
            key={schema.key}
            targetType={schema.targetType || entityType}
            currentEntityType={entityType}
            currentEntityId={entityId}
            labelKey={schema.labelKey}
            value={formState.attributes[schema.key]}
            onChange={attr => setField(schema.key, attr as any)}
          />
        ))}
      </div>
    </details>
  );
};
