import React from 'react';
import { useI18n } from '@/context/I18nContext';
import { useEntityEditor } from '../EntityEditorContext';
import { AttributeField } from '../AttributeField';
import { getSchemasForType } from '../attributeSchemas';
import { getNGSIValue } from '@/types/ngsi-ld';

export const KinematicSection: React.FC = () => {
  const { t } = useI18n();
  const { formState, setField, entityType } = useEntityEditor();
  const categoryAttr = formState.attributes.category;
  const category = categoryAttr ? String(getNGSIValue(categoryAttr) ?? '') : undefined;
  const forRole = new Set(getSchemasForType(entityType, category || undefined).map(s => s.key));
  // A value stored for the other role (older entities were edited without roles) stays
  // visible, so it can be checked or cleared instead of lingering unseen.
  const hasValue = (key: string) => {
    const attr = formState.attributes[key];
    const v = attr ? getNGSIValue(attr) : undefined;
    return v !== undefined && v !== null && v !== '';
  };
  const schemas = getSchemasForType(entityType)
    .filter(s => s.section === 'kinematic' && (forRole.has(s.key) || hasValue(s.key)));
  if (schemas.length === 0) return null;

  return (
    <details className="border border-nkz-border rounded-lg">
      <summary className="px-4 py-3 bg-nkz-bg-secondary cursor-pointer text-sm font-semibold text-gray-700 hover:bg-nkz-bg-secondary rounded-lg">
        {t('editor.section.kinematic') || 'Cinemática y enganche'}
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
