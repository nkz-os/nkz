import React from 'react';
import { useI18n } from '@/context/I18nContext';
import { getNGSIValue } from '@/types/ngsi-ld';
import type { NGSAttribute } from '@/types/ngsi-ld';
import type { AttributeSchema } from './types';
import { Input } from '@nekazari/ui-kit';

interface Props {
  schema: AttributeSchema;
  value: NGSAttribute | undefined;
  onChange: (attr: NGSAttribute) => void;
}

export const AttributeField: React.FC<Props> = ({ schema, value, onChange }) => {
  const { t } = useI18n();
  const currentValue = value ? getNGSIValue(value) : undefined;

  const emit = (newValue: string | number | boolean) => {
    onChange({
      type: 'Property',
      value: newValue,
      ...(schema.unitCode ? { unitCode: schema.unitCode } : {}),
    } as NGSAttribute);
  };

  if (schema.type === 'boolean') {
    return (
      <label className="flex items-center gap-2 cursor-pointer">
        <Input
          type="checkbox"
          checked={!!currentValue}
          onChange={(e: any) => emit(e.target.checked)}
          className="w-4 h-4 rounded border-nkz-border text-nkz-info focus:ring-blue-500"
        />
        <span className="text-sm text-gray-700">{t(schema.labelKey)}</span>
      </label>
    );
  }

  if (schema.type === 'select') {
    return (
      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-gray-600">{t(schema.labelKey)}</label>
        <select
          value={String(currentValue ?? '')}
          onChange={(e: any) => emit(e.target.value)}
          className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        >
          <option value="">—</option>
          {schema.options?.map(opt => (
            <option key={opt.value} value={opt.value}>{t(opt.labelKey)}</option>
          ))}
        </select>
      </div>
    );
  }

  if (schema.type === 'number') {
    return (
      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-gray-600">{t(schema.labelKey)}</label>
        <div className="flex items-center gap-1">
          <Input
            type="number"
            value={currentValue != null ? String(currentValue) : ''}
            onChange={(e: any) => emit(e.target.value === '' ? '' : Number(e.target.value))}
            min={schema.min}
            max={schema.max}
            step={schema.step}
            className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
          {schema.unitCode && (
            <span className="text-xs text-nkz-muted w-10">{schema.unitCode}</span>
          )}
        </div>
      </div>
    );
  }

  // text (default)
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-gray-600">{t(schema.labelKey)}</label>
      <Input
        type="text"
        value={currentValue != null ? String(currentValue) : ''}
        onChange={(e: any) => emit(e.target.value)}
        className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
      />
    </div>
  );
};
