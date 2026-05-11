import React from 'react';
import { useI18n } from '@/context/I18nContext';
import { useEntityEditor } from '../EntityEditorContext';
import { AttributeField } from '../AttributeField';
import { getSchemasForType } from '../attributeSchemas';

export const BasicAttributes: React.FC = () => {
  const { t } = useI18n();
  const { formState, setField, entityType } = useEntityEditor();
  const schemas = getSchemasForType(entityType).filter(s => s.section === 'basic');

  return (
    <details className="border border-gray-200 rounded-lg" open>
      <summary className="px-4 py-3 bg-gray-50 cursor-pointer text-sm font-semibold text-gray-700 hover:bg-gray-100 rounded-lg">
        {t('editor.section.basic') || 'Atributos básicos'}
      </summary>
      <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
        {schemas.map(schema => (
          <AttributeField
            key={schema.key}
            schema={schema}
            value={formState.attributes[schema.key]}
            onChange={attr => setField(schema.key, attr)}
          />
        ))}
      </div>
    </details>
  );
};
