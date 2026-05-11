import React from 'react';
import { useI18n } from '@/context/I18nContext';
import { useEntityEditor } from '../EntityEditorContext';
import { EntitySearchInput } from '../EntitySearchInput';
import { getSchemasForType } from '../attributeSchemas';

export const RelationshipSection: React.FC = () => {
  const { t } = useI18n();
  const { formState, setField, entityType, entityId } = useEntityEditor();
  const schemas = getSchemasForType(entityType).filter(s => s.section === 'relationships');
  if (schemas.length === 0) return null;

  return (
    <details className="border border-gray-200 rounded-lg">
      <summary className="px-4 py-3 bg-gray-50 cursor-pointer text-sm font-semibold text-gray-700 hover:bg-gray-100 rounded-lg">
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
